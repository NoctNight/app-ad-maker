"""The end-to-end pipeline: brief in, finished vertical ad out.

Stages: script -> images (or video clips) -> narration -> timing -> render.
Each stage reports progress so the CLI and the web UI can show the same
thing, and each is independently swappable via the provider registry.
"""

from __future__ import annotations

import json
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from collections.abc import Callable

from . import ass_subs, ffmpeg, render
from .models import Brief, Platform, RenderOptions, Storyboard
from .providers import (
    IMAGE,
    SCRIPT,
    TTS,
    VIDEO,
    ImageProvider,
    ScriptProvider,
    TTSProvider,
    VideoProvider,
    build,
)
from .providers.tts import estimate_seconds

ProgressFn = Callable[[str, float], None]


@dataclass
class ProviderChoice:
    """Which provider to use for one stage, plus its key and model."""

    name: str
    api_key: str | None = None
    model: str | None = None
    voice: str | None = None
    options: dict[str, Any] = field(default_factory=dict)


@dataclass
class AdRequest:
    brief: Brief
    platform: Platform
    options: RenderOptions
    script: ProviderChoice
    image: ProviderChoice
    tts: ProviderChoice
    video: ProviderChoice | None = None  # optional true text-to-video
    output_path: str | None = None
    workdir: str | None = None
    keep_workdir: bool = False


@dataclass
class AdResult:
    video_path: str
    storyboard: Storyboard
    duration: float
    platform: str
    workdir: str
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "video_path": self.video_path,
            "duration": round(self.duration, 2),
            "platform": self.platform,
            "workdir": self.workdir,
            "warnings": self.warnings,
            "storyboard": self.storyboard.to_dict(),
        }


def generate_ad(request: AdRequest, progress: ProgressFn | None = None) -> AdResult:
    """Run the whole pipeline. Raises ProviderError / FFmpegError on failure."""
    report = progress or (lambda _m, _f: None)
    warnings: list[str] = []

    workdir = Path(
        request.workdir or Path.cwd() / "output" / f"ad_{int(time.time())}"
    )
    workdir.mkdir(parents=True, exist_ok=True)
    scenes_dir = workdir / "scenes"
    scenes_dir.mkdir(exist_ok=True)

    platform = request.platform
    options = request.options
    target_seconds = min(platform.ideal_seconds, platform.max_seconds)

    # ---------------------------------------------------------------- script
    report("Writing the script", 0.04)
    script_provider: ScriptProvider = build(
        SCRIPT,
        request.script.name,
        api_key=request.script.api_key,
        model=request.script.model,
        seconds=target_seconds,
        **request.script.options,
    )
    storyboard = script_provider.generate(request.brief)
    (workdir / "storyboard.json").write_text(
        json.dumps(storyboard.to_dict(), indent=2), encoding="utf-8"
    )
    report(f"Script ready: {len(storyboard.scenes)} scenes", 0.12)

    # ---------------------------------------------------------------- visuals
    image_provider: ImageProvider = build(
        IMAGE,
        request.image.name,
        api_key=request.image.api_key,
        model=request.image.model,
        **request.image.options,
    )
    video_provider: VideoProvider | None = None
    if request.video and request.video.name:
        video_provider = build(
            VIDEO,
            request.video.name,
            api_key=request.video.api_key,
            model=request.video.model,
            **request.video.options,
        )

    n = len(storyboard.scenes)
    for i, scene in enumerate(storyboard.scenes):
        report(f"Generating visual {i + 1} of {n}", 0.12 + 0.33 * (i / max(1, n)))
        target = scenes_dir / f"scene_{i:02d}.jpg"
        try:
            scene.image_path = image_provider.generate(
                scene.image_prompt,
                target,
                platform.width,
                platform.height,
                seed=i + 1,
            )
        except Exception as exc:  # noqa: BLE001 - one bad image must not kill the ad
            warnings.append(f"Scene {i + 1} image failed ({exc}); used a fallback.")
            from .providers.image import OfflineImage

            scene.image_path = OfflineImage().generate(
                scene.image_prompt, target, platform.width, platform.height, seed=i + 1
            )

    # ---------------------------------------------------------------- voice
    tts_provider: TTSProvider = build(
        TTS,
        request.tts.name,
        api_key=request.tts.api_key,
        model=request.tts.model,
        **request.tts.options,
    )
    for i, scene in enumerate(storyboard.scenes):
        report(f"Recording voiceover {i + 1} of {n}", 0.45 + 0.22 * (i / max(1, n)))
        target = scenes_dir / f"voice_{i:02d}.mp3"
        try:
            scene.audio_path = tts_provider.synthesize(
                scene.narration, target, voice=request.tts.voice
            )
            scene.duration = ffmpeg.probe_duration(scene.audio_path)
        except Exception as exc:  # noqa: BLE001 - keep the timeline, lose the audio
            warnings.append(f"Scene {i + 1} voiceover failed ({exc}); left silent.")
            scene.audio_path = None
            scene.duration = estimate_seconds(scene.narration)

    # ---------------------------------------------------------------- timing
    timings = render.compute_timings(storyboard.scenes)
    budget = platform.max_seconds - options.cta_seconds
    timings, tempo = render.fit_to_budget(timings, budget)
    scenes_duration = sum(t.duration for t in timings)
    total_duration = scenes_duration + options.cta_seconds

    if tempo > 1.0:
        warnings.append(
            f"Script ran long; audio sped up {tempo:.2f}x to fit "
            f"{platform.label}'s {platform.max_seconds:.0f}s limit."
        )
    for scene, timing in zip(storyboard.scenes, timings):
        scene.duration = timing.duration

    # ---------------------------------------------------------------- clips
    report("Animating scenes", 0.68)
    clip_paths: list[str] = []
    clip_durations: list[float] = []
    transition_pad = (
        options.transition_seconds
        if options.transition != "none" and options.transition_seconds > 0
        else 0.0
    )

    segments = list(timings)
    has_cta = options.cta_seconds > 0
    for i, timing in enumerate(segments):
        scene = storyboard.scenes[i]
        clip_out = scenes_dir / f"clip_{i:02d}.mp4"
        # Every clip but the final one in the timeline carries an extra
        # transition-length tail; the crossfade consumes it, so scene
        # boundaries land exactly where the captions and voice expect them.
        is_last = (i == len(segments) - 1) and not has_cta
        clip_len = timing.duration + (0.0 if is_last else transition_pad)

        if video_provider is not None:
            try:
                raw = scenes_dir / f"gen_{i:02d}.mp4"
                video_provider.generate(
                    scene.image_prompt,
                    raw,
                    seconds=clip_len,
                    width=platform.width,
                    height=platform.height,
                    image_path=scene.image_path,
                )
                scene.video_path = render.normalize_video_clip(
                    raw, clip_out, clip_len, platform, options
                )
            except Exception as exc:  # noqa: BLE001 - fall back to the still
                warnings.append(
                    f"Scene {i + 1} video generation failed ({exc}); used the still."
                )
                render.ken_burns_clip(
                    scene.image_path, clip_out, clip_len, platform, options, i
                )
        else:
            render.ken_burns_clip(
                scene.image_path, clip_out, clip_len, platform, options, i
            )
        clip_paths.append(str(clip_out))
        clip_durations.append(timing.duration)

    # A short CTA card closes the ad, blurred out of the final scene.
    if options.cta_seconds > 0:
        card = scenes_dir / "clip_cta.mp4"
        render.cta_card(
            card,
            options.cta_seconds,
            platform,
            options,
            source_image=storyboard.scenes[-1].image_path,
        )
        clip_paths.append(str(card))
        clip_durations.append(options.cta_seconds)

    report("Joining scenes", 0.80)
    joined = render.join_clips(
        clip_paths, clip_durations, workdir / "visual.mp4", options
    )

    # ---------------------------------------------------------------- audio
    report("Mixing audio", 0.86)
    voice = render.build_voice_track(
        [s.audio_path for s in storyboard.scenes],
        timings,
        workdir / "voice.m4a",
        total_duration,
        tempo=tempo,
    )
    audio = voice
    if options.music_path and Path(options.music_path).exists():
        audio = render.mix_with_music(
            voice, options.music_path, workdir / "audio.m4a", total_duration, options
        )

    # ---------------------------------------------------------------- captions
    ass_path: str | None = None
    if options.captions:
        report("Timing captions", 0.90)
        cues = render.build_cues(storyboard, timings)
        # Never let a caption bleed onto the CTA card.
        cues = [c for c in cues if c.start < scenes_duration]
        for cue in cues:
            cue.end = min(cue.end, scenes_duration)
        document = ass_subs.build_ass(
            cues,
            platform,
            options,
            hook=storyboard.hook,
            cta=storyboard.cta,
            cta_start=scenes_duration + 0.15,
            cta_duration=max(0.0, options.cta_seconds - 0.2),
        )
        ass_path = ass_subs.write_ass(workdir / "captions.ass", document)

    # ---------------------------------------------------------------- output
    report("Encoding the final video", 0.93)
    output_path = Path(
        request.output_path or workdir / f"{_slug(storyboard.title or 'ad')}.mp4"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    render.burn_and_mux(
        joined, audio, ass_path, output_path, options, total_duration
    )

    if not request.keep_workdir:
        shutil.rmtree(scenes_dir, ignore_errors=True)

    report("Done", 1.0)
    return AdResult(
        video_path=str(output_path),
        storyboard=storyboard,
        duration=ffmpeg.probe_duration(output_path),
        platform=platform.key,
        workdir=str(workdir),
        warnings=warnings,
    )


def _slug(text: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_ " else "" for c in text).strip()
    return ("-".join(cleaned.split()) or "ad").lower()[:60]
