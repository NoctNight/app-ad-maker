"""Tests that run with no network and no API keys."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from admaker import ass_subs, ffmpeg, render
from admaker.models import (
    PLATFORMS,
    Brief,
    RenderOptions,
    Scene,
    Storyboard,
    _extract_json_object,
    get_platform,
)
from admaker.pipeline import AdRequest, ProviderChoice, generate_ad
from admaker.providers import IMAGE, SCRIPT, TTS, VIDEO, build, list_providers


# --------------------------------------------------------------------------
# Model parsing
# --------------------------------------------------------------------------


def test_extracts_json_from_fenced_response():
    raw = 'Sure!\n```json\n{"hook": "hi", "scenes": []}\n```\nHope that helps.'
    assert _extract_json_object(raw)["hook"] == "hi"


def test_extracts_json_with_trailing_prose():
    raw = '{"a": {"b": "}"}, "c": 1} and then some chatter'
    assert _extract_json_object(raw)["c"] == 1


def test_storyboard_from_llm_json_normalizes_scenes():
    raw = json.dumps(
        {
            "hook": "**Bold hook**",
            "scenes": [
                {"narration": "One two three.", "caption": "One", "image_prompt": "a"},
                {"voiceover": "Alt key name.", "visual": "b"},
                {"caption": "no narration, dropped"},
            ],
            "hashtags": "#one #two",
        }
    )
    sb = Storyboard.from_llm_json(raw)
    assert sb.hook == "Bold hook"
    assert len(sb.scenes) == 2  # the narration-less scene is dropped
    assert sb.scenes[1].narration == "Alt key name."
    assert sb.hashtags == ["one", "two"]  # leading hashes stripped


def test_storyboard_rejects_empty_scene_list():
    with pytest.raises(ValueError):
        Storyboard.from_llm_json('{"hook": "x", "scenes": []}')


def test_unknown_platform_is_a_clear_error():
    with pytest.raises(ValueError, match="Unknown platform"):
        get_platform("myspace")


def test_caption_safe_area_clears_platform_ui():
    for platform in PLATFORMS.values():
        y = platform.caption_center_y
        assert platform.safe_top < y < platform.height - platform.safe_bottom


# --------------------------------------------------------------------------
# Captions
# --------------------------------------------------------------------------


def test_cues_span_exactly_the_scene():
    cues = ass_subs.cues_for_scene("One two three four five six.", start=2.0, duration=3.0)
    assert cues
    assert cues[0].start == pytest.approx(2.0)
    assert cues[-1].end == pytest.approx(5.0)
    for a, b in zip(cues, cues[1:]):
        assert a.end == pytest.approx(b.start)


def test_short_scenes_merge_chunks_rather_than_flashing():
    many = ass_subs.cues_for_scene("a b c d e f g h i j k l", start=0, duration=0.9)
    assert len(many) <= 2


def test_numbers_get_the_accent_colour():
    cues = ass_subs.cues_for_scene("Saves you 40% every month.", start=0, duration=3)
    assert any(c.emphasis for c in cues)


def test_hex_to_ass_reverses_byte_order():
    assert ass_subs.hex_to_ass("#FF0000") == "&H000000FF"  # red -> BBGGRR
    assert ass_subs.hex_to_ass("#FFF") == "&H00FFFFFF"


def test_ass_escapes_braces_so_text_is_not_read_as_markup():
    doc = ass_subs.build_ass(
        [ass_subs.CaptionCue("save {50}% now", 0.0, 1.0)],
        PLATFORMS["universal"],
        RenderOptions(),
    )
    assert r"save \{50\}% now" in doc


def test_ass_document_has_the_required_sections():
    doc = ass_subs.build_ass(
        [ass_subs.CaptionCue("hello", 0.0, 1.0)],
        PLATFORMS["tiktok"],
        RenderOptions(),
        hook="Stop scrolling",
    )
    for section in ("[Script Info]", "[V4+ Styles]", "[Events]", "PlayResY: 1920"):
        assert section in doc
    assert "Stop scrolling" in doc


# --------------------------------------------------------------------------
# Timing
# --------------------------------------------------------------------------


def _scenes(durations):
    out = []
    for d in durations:
        s = Scene(narration="word " * 8, caption="c", image_prompt="p")
        s.duration = d
        out.append(s)
    return out


def test_timings_are_contiguous_and_include_padding():
    timings = render.compute_timings(_scenes([2.0, 3.0]))
    assert timings[0].start == 0.0
    assert timings[1].start == pytest.approx(timings[0].duration)
    assert timings[0].duration > 2.0  # lead-in and tail added


def test_fit_to_budget_leaves_short_ads_alone():
    timings = render.compute_timings(_scenes([2.0, 2.0]))
    fitted, tempo = render.fit_to_budget(timings, 60)
    assert tempo == 1.0
    assert fitted == timings


def test_fit_to_budget_speeds_up_long_ads():
    timings = render.compute_timings(_scenes([30.0, 30.0]))
    fitted, tempo = render.fit_to_budget(timings, 40)
    assert tempo > 1.0
    assert sum(t.duration for t in fitted) < sum(t.duration for t in timings)


def test_fit_to_budget_never_makes_speech_unlistenable():
    timings = render.compute_timings(_scenes([300.0]))
    _, tempo = render.fit_to_budget(timings, 10)
    assert tempo <= 1.2


# --------------------------------------------------------------------------
# Providers
# --------------------------------------------------------------------------


def test_every_provider_declares_cost_and_signup_when_a_key_is_needed():
    for kind in (SCRIPT, IMAGE, TTS, VIDEO):
        for info in list_providers(kind):
            assert info.cost, f"{info.name} has no cost note"
            if info.requires_key:
                assert info.env_var and info.signup_url, info.name


def test_free_providers_are_listed_first():
    tiers = [i.tier for i in list_providers(SCRIPT)]
    assert tiers.index("free") < tiers.index("paid")


def test_missing_key_names_the_env_var_and_signup_url():
    from admaker.providers import MissingKeyError

    with pytest.raises(MissingKeyError) as exc:
        build(SCRIPT, "anthropic", api_key=None)
    assert "ANTHROPIC_API_KEY" in str(exc.value)
    assert "console.anthropic.com" in str(exc.value)


def test_unknown_provider_lists_the_alternatives():
    from admaker.providers import ProviderError

    with pytest.raises(ProviderError, match="Available:"):
        build(SCRIPT, "not-a-real-provider")


def test_offline_script_respects_the_requested_scene_count():
    sb = build(SCRIPT, "offline").generate(Brief(product="Widget", scene_count=3))
    assert len(sb.scenes) == 3
    assert sb.cta and sb.hook


def test_offline_images_are_deterministic_and_prompt_distinct(tmp_path):
    provider = build(IMAGE, "offline")
    a = Path(provider.generate("a red bottle", tmp_path / "a.jpg", 216, 384, seed=1))
    b = Path(provider.generate("a red bottle", tmp_path / "b.jpg", 216, 384, seed=1))
    c = Path(provider.generate("a blue chair", tmp_path / "c.jpg", 216, 384, seed=1))
    assert a.read_bytes() == b.read_bytes()
    assert a.read_bytes() != c.read_bytes()


# --------------------------------------------------------------------------
# ffmpeg helpers
# --------------------------------------------------------------------------


def test_ffmpeg_binary_is_available():
    assert Path(ffmpeg.ffmpeg_path()).exists()


def test_filter_paths_are_escaped_for_the_filtergraph():
    escaped = ffmpeg.escape_filter_path(r"C:\ads\my:file.ass")
    assert "\\:" in escaped and "\\" in escaped


def test_probe_duration_matches_generated_length(tmp_path):
    out = tmp_path / "tone.mp3"
    ffmpeg.run(
        ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo", "-t", "1.5",
         "-c:a", "libmp3lame", str(out)],
        desc="test tone",
    )
    assert ffmpeg.probe_duration(out) == pytest.approx(1.5, abs=0.15)


def test_probe_missing_file_raises():
    with pytest.raises(ffmpeg.FFmpegError):
        ffmpeg.probe_duration("/nope/missing.mp3")


# --------------------------------------------------------------------------
# End to end (offline, no network, no keys)
# --------------------------------------------------------------------------


@pytest.mark.slow
def test_offline_pipeline_produces_a_playable_ad(tmp_path):
    request = AdRequest(
        brief=Brief(
            product="Nomad Brew",
            description="a collapsible pour-over dripper",
            key_points=["Folds flat"],
            scene_count=2,
        ),
        platform=PLATFORMS["universal"],
        options=RenderOptions(platform=PLATFORMS["universal"], cta_seconds=1.0),
        script=ProviderChoice("offline"),
        image=ProviderChoice("offline"),
        tts=ProviderChoice("offline"),
        output_path=str(tmp_path / "ad.mp4"),
        workdir=str(tmp_path / "work"),
    )
    result = generate_ad(request)

    out = Path(result.video_path)
    assert out.exists() and out.stat().st_size > 50_000
    # The rendered file must match the timeline the captions were built against.
    assert ffmpeg.probe_duration(out) == pytest.approx(result.duration, abs=0.2)
    assert result.duration <= PLATFORMS["universal"].max_seconds
    assert (Path(result.workdir) / "storyboard.json").exists()
    assert (Path(result.workdir) / "captions.ass").exists()


def test_dotenv_ignores_inline_comments(tmp_path, monkeypatch):
    from admaker.config import load_dotenv

    env = tmp_path / ".env"
    env.write_text(
        "BLANK_KEY=            # free tier https://example.com\n"
        "SET_KEY=abc123  # note\n"
        'QUOTED_KEY="has # hash"\n',
        encoding="utf-8",
    )
    for name in ("BLANK_KEY", "SET_KEY", "QUOTED_KEY"):
        monkeypatch.delenv(name, raising=False)
    load_dotenv(env)
    import os

    assert os.environ["BLANK_KEY"] == ""
    assert os.environ["SET_KEY"] == "abc123"
    assert os.environ["QUOTED_KEY"] == "has # hash"
