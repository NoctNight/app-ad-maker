"""Turn a timed storyboard into a finished vertical video.

The render runs in stages rather than as one enormous filtergraph: each scene
becomes its own silent clip, the clips are joined, the audio is built
separately, and the two are married with burned-in captions at the end.
Staging costs a little disk but makes failures legible and progress
reportable, and it keeps any single ffmpeg command small enough to debug.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from collections.abc import Callable, Sequence

from . import ass_subs, ffmpeg
from .ass_subs import CaptionCue, cues_for_scene
from .models import Platform, RenderOptions, Scene, Storyboard

# Timing around each narration line. A beat of air before the voice and a
# longer one after is what makes a cut feel deliberate instead of clipped.
LEAD_IN = 0.12
TAIL = 0.45

# Ken Burns quality: zoompan quantizes its crop to integer pixels, which
# reads as jitter at 1x. Working at this multiple of the canvas hides it.
KEN_BURNS_SCALE = 2

ProgressFn = Callable[[str, float], None]


@dataclass
class SceneTiming:
    index: int
    start: float
    duration: float
    narration_start: float
    narration_duration: float


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


def compute_timings(scenes: Sequence[Scene]) -> list[SceneTiming]:
    """Lay scenes on a timeline from their measured narration durations."""
    timings: list[SceneTiming] = []
    cursor = 0.0
    for i, scene in enumerate(scenes):
        narration = max(0.4, scene.duration)
        total = LEAD_IN + narration + TAIL
        timings.append(
            SceneTiming(
                index=i,
                start=cursor,
                duration=total,
                narration_start=cursor + LEAD_IN,
                narration_duration=narration,
            )
        )
        cursor += total
    return timings


def fit_to_budget(
    timings: list[SceneTiming], max_seconds: float
) -> tuple[list[SceneTiming], float]:
    """Speed the whole edit up if it overruns the platform's limit.

    Returns the adjusted timings and the tempo factor the audio must match.
    A single global tempo keeps voice and picture locked; per-scene trims
    would drift.
    """
    total = sum(t.duration for t in timings)
    if total <= max_seconds or total <= 0:
        return timings, 1.0

    tempo = total / max_seconds
    # atempo above ~1.2 starts to sound chipmunky; past that, the script is
    # simply too long and the caller should be told rather than papered over.
    tempo = min(tempo, 1.2)

    scaled: list[SceneTiming] = []
    cursor = 0.0
    for t in timings:
        narration = t.narration_duration / tempo
        duration = LEAD_IN + narration + TAIL / tempo
        scaled.append(
            SceneTiming(
                index=t.index,
                start=cursor,
                duration=duration,
                narration_start=cursor + LEAD_IN,
                narration_duration=narration,
            )
        )
        cursor += duration
    return scaled, tempo


# --------------------------------------------------------------------------
# Visual stage
# --------------------------------------------------------------------------


def _cover_filter(width: int, height: int) -> str:
    """Scale-and-crop so any input aspect ratio fills the canvas."""
    return (
        f"scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height}"
    )


def ken_burns_clip(
    image_path: str | Path,
    out_path: str | Path,
    duration: float,
    platform: Platform,
    options: RenderOptions,
    index: int,
) -> str:
    """Render one still into a slowly moving clip."""
    frames = max(2, int(round(duration * options.fps)))
    big_w = platform.width * KEN_BURNS_SCALE
    big_h = platform.height * KEN_BURNS_SCALE

    # Alternate push-in and pull-out so a multi-scene ad doesn't feel like
    # one continuous zoom, and drift sideways a little for parallax.
    zoom_in = index % 2 == 0
    z = options.zoom
    progress = f"(on/{frames - 1})"
    if zoom_in:
        zoom_expr = f"1+{z:.4f}*{progress}"
    else:
        zoom_expr = f"{1 + z:.4f}-{z:.4f}*{progress}"

    drift = 0.5 + (0.16 if index % 4 in (1, 2) else -0.16)
    x_expr = f"(iw-iw/zoom)*({drift:.3f})"
    y_expr = f"(ih-ih/zoom)*(0.5+0.10*({progress}-0.5))"

    filtergraph = (
        f"{_cover_filter(big_w, big_h)},"
        f"zoompan=z='{zoom_expr}':d=1:x='{x_expr}':y='{y_expr}'"
        f":s={platform.width}x{platform.height}:fps={options.fps},"
        f"format=yuv420p"
    )

    ffmpeg.run(
        [
            # -framerate must precede the input: a looped image otherwise
            # decodes at ffmpeg's 25fps default and every clip comes out short.
            "-loop", "1",
            "-framerate", str(options.fps),
            "-t", f"{duration:.3f}",
            "-i", str(image_path),
            "-vf", filtergraph,
            "-r", str(options.fps),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(options.crf),
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        desc=f"Ken Burns clip {index + 1}",
    )
    return str(out_path)


def normalize_video_clip(
    src: str | Path,
    out_path: str | Path,
    duration: float,
    platform: Platform,
    options: RenderOptions,
) -> str:
    """Conform a generated video clip to the canvas, fps and exact duration."""
    ffmpeg.run(
        [
            "-stream_loop", "-1",  # loop if the model returned a short clip
            "-i", str(src),
            "-t", f"{duration:.3f}",
            "-an",
            "-vf", f"{_cover_filter(platform.width, platform.height)},"
                   f"fps={options.fps},format=yuv420p",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(options.crf),
            str(out_path),
        ],
        desc="normalize generated clip",
    )
    return str(out_path)


def join_clips(
    clips: Sequence[str],
    durations: Sequence[float],
    out_path: str | Path,
    options: RenderOptions,
) -> str:
    """Join scene clips, optionally crossfading between them.

    Each clip except the last is rendered `transition_seconds` longer than
    its scene so the crossfade overlap consumes the surplus. That keeps every
    scene boundary at its intended timestamp, which is what the captions and
    the voice track are aligned to.
    """
    clips = list(clips)
    if len(clips) == 1:
        return _remux(clips[0], out_path)

    if options.transition == "none" or options.transition_seconds <= 0:
        return _concat_demuxer(clips, out_path)

    t = options.transition_seconds
    inputs: list[str] = []
    for clip in clips:
        inputs += ["-i", str(clip)]

    steps: list[str] = []
    current = "[0:v]"
    offset = 0.0
    for i in range(1, len(clips)):
        offset += durations[i - 1]
        label = f"[x{i}]" if i < len(clips) - 1 else "[vout]"
        steps.append(
            f"{current}[{i}:v]xfade=transition={options.transition}"
            f":duration={t:.3f}:offset={offset:.3f}{label}"
        )
        current = label

    ffmpeg.run(
        [
            *inputs,
            "-filter_complex", ";".join(steps),
            "-map", "[vout]",
            "-r", str(options.fps),
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", str(options.crf),
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        desc="join scenes with transitions",
    )
    return str(out_path)


def _concat_demuxer(clips: Sequence[str], out_path: str | Path) -> str:
    listing = Path(out_path).with_suffix(".txt")
    listing.write_text(
        "\n".join(f"file '{Path(c).resolve().as_posix()}'" for c in clips),
        encoding="utf-8",
    )
    ffmpeg.run(
        [
            "-f", "concat", "-safe", "0", "-i", str(listing),
            "-c", "copy",
            str(out_path),
        ],
        desc="concatenate scenes",
    )
    return str(out_path)


def _remux(src: str | Path, out_path: str | Path) -> str:
    ffmpeg.run(["-i", str(src), "-c", "copy", str(out_path)], desc="remux")
    return str(out_path)


def cta_card(
    out_path: str | Path,
    duration: float,
    platform: Platform,
    options: RenderOptions,
    source_image: str | Path | None = None,
) -> str:
    """A short darkened end card the CTA text sits on."""
    if source_image and Path(source_image).exists():
        vf = (
            f"{_cover_filter(platform.width, platform.height)},"
            f"gblur=sigma=18,eq=brightness=-0.22:saturation=0.7,"
            f"fps={options.fps},format=yuv420p"
        )
        args = [
            "-loop", "1",
            "-framerate", str(options.fps),
            "-t", f"{duration:.3f}",
            "-i", str(source_image),
            "-vf", vf,
        ]
    else:
        args = [
            "-f", "lavfi",
            "-i", f"color=c=0x101014:s={platform.width}x{platform.height}"
                  f":r={options.fps}:d={duration:.3f}",
            "-vf", "format=yuv420p",
        ]
    ffmpeg.run(
        [
            *args,
            "-r", str(options.fps),
            "-c:v", "libx264", "-preset", "veryfast", "-crf", str(options.crf),
            "-pix_fmt", "yuv420p",
            str(out_path),
        ],
        desc="CTA card",
    )
    return str(out_path)


# --------------------------------------------------------------------------
# Audio stage
# --------------------------------------------------------------------------


def build_voice_track(
    narration_files: Sequence[str | None],
    timings: Sequence[SceneTiming],
    out_path: str | Path,
    total_duration: float,
    tempo: float = 1.0,
) -> str:
    """Place each narration clip at its scene's start time on one track.

    Built by delaying each clip onto a silent bed rather than by
    concatenating, so a narration that runs slightly long or short cannot
    shift everything after it out of sync with the picture.
    """
    inputs: list[str] = [
        "-f", "lavfi",
        "-t", f"{total_duration:.3f}",
        "-i", "anullsrc=channel_layout=stereo:sample_rate=44100",
    ]
    chains: list[str] = []
    mix_labels: list[str] = ["[0:a]"]

    stream = 1
    for path, timing in zip(narration_files, timings):
        if not path:
            continue
        inputs += ["-i", str(path)]
        label = f"[v{stream}]"
        delay_ms = int(timing.narration_start * 1000)
        tempo_filter = f"atempo={tempo:.4f}," if abs(tempo - 1.0) > 0.01 else ""
        chains.append(
            f"[{stream}:a]{tempo_filter}aresample=44100,aformat=channel_layouts=stereo,"
            f"adelay={delay_ms}|{delay_ms}{label}"
        )
        mix_labels.append(label)
        stream += 1

    if stream == 1:  # nothing but silence
        ffmpeg.run(
            [*inputs, "-c:a", "aac", "-b:a", "192k", str(out_path)],
            desc="empty voice track",
        )
        return str(out_path)

    graph = ";".join(
        chains
        + [
            f"{''.join(mix_labels)}amix=inputs={len(mix_labels)}"
            f":duration=first:normalize=0[vout]"
        ]
    )
    ffmpeg.run(
        [
            *inputs,
            "-filter_complex", graph,
            "-map", "[vout]",
            "-t", f"{total_duration:.3f}",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ],
        desc="build voice track",
    )
    return str(out_path)


def mix_with_music(
    voice_path: str | Path,
    music_path: str | Path,
    out_path: str | Path,
    total_duration: float,
    options: RenderOptions,
) -> str:
    """Lay a music bed under the voice, ducked and faded at both ends."""
    fade_out_start = max(0.0, total_duration - 1.2)
    graph = (
        f"[1:a]aformat=channel_layouts=stereo,aresample=44100,"
        f"volume={options.music_volume:.3f},"
        f"afade=t=in:st=0:d=1.0,afade=t=out:st={fade_out_start:.3f}:d=1.2[bed];"
        f"[0:a]volume={options.voice_volume:.3f}[voice];"
        f"[voice][bed]amix=inputs=2:duration=first:normalize=0,"
        f"alimiter=limit=0.95[aout]"
    )
    ffmpeg.run(
        [
            "-i", str(voice_path),
            "-stream_loop", "-1", "-i", str(music_path),
            "-filter_complex", graph,
            "-map", "[aout]",
            "-t", f"{total_duration:.3f}",
            "-c:a", "aac", "-b:a", "192k",
            str(out_path),
        ],
        desc="mix music bed",
    )
    return str(out_path)


# --------------------------------------------------------------------------
# Final assembly
# --------------------------------------------------------------------------


def burn_and_mux(
    video_path: str | Path,
    audio_path: str | Path,
    ass_path: str | Path | None,
    out_path: str | Path,
    options: RenderOptions,
    total_duration: float,
) -> str:
    """Burn captions over the picture and attach the finished audio."""
    filters = []
    if ass_path:
        _, fonts_dir = ass_subs.resolve_font(options.caption_font)
        escaped = ffmpeg.escape_filter_path(ass_path)
        sub = f"subtitles={escaped}"
        if fonts_dir:
            sub += f":fontsdir={ffmpeg.escape_filter_path(fonts_dir)}"
        filters.append(sub)
    filters.append("format=yuv420p")

    ffmpeg.run(
        [
            "-i", str(video_path),
            "-i", str(audio_path),
            "-vf", ",".join(filters),
            "-map", "0:v:0", "-map", "1:a:0",
            "-t", f"{total_duration:.3f}",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", str(options.crf),
            "-profile:v", "high",
            "-level", "4.1",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", "44100",
            "-movflags", "+faststart",  # so the file starts playing while it uploads
            str(out_path),
        ],
        desc="burn captions and mux",
        timeout=1800,
    )
    return str(out_path)


def build_cues(
    storyboard: Storyboard, timings: Sequence[SceneTiming]
) -> list[CaptionCue]:
    """Caption cues for every scene, timed against the voice track."""
    cues: list[CaptionCue] = []
    for scene, timing in zip(storyboard.scenes, timings):
        cues.extend(
            cues_for_scene(
                scene.caption or scene.narration,
                start=timing.narration_start,
                duration=timing.narration_duration,
            )
        )
    return cues
