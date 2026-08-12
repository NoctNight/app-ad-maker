"""Core data structures shared across the pipeline."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict
from typing import Any


# --------------------------------------------------------------------------
# Platform presets
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Platform:
    """Delivery target. Drives canvas size, length and text safe zones.

    All three major short-form surfaces are 9:16 1080x1920; what actually
    differs is the max length and how much of the frame the app's own UI
    covers up (like buttons and the caption tray).
    """

    key: str
    label: str
    width: int
    height: int
    max_seconds: float
    ideal_seconds: float
    # Pixels of the frame obscured by platform UI. Captions must clear these.
    safe_top: int
    safe_bottom: int

    @property
    def caption_center_y(self) -> int:
        """Vertical center for the caption block, inside the safe area."""
        usable_top = self.safe_top
        usable_bottom = self.height - self.safe_bottom
        # Sit at ~72% down the usable band: low enough to feel native,
        # high enough to never collide with the platform's own chrome.
        return int(usable_top + (usable_bottom - usable_top) * 0.72)


PLATFORMS: dict[str, Platform] = {
    "tiktok": Platform(
        key="tiktok",
        label="TikTok",
        width=1080,
        height=1920,
        max_seconds=60.0,
        ideal_seconds=27.0,
        safe_top=220,
        safe_bottom=520,
    ),
    "reels": Platform(
        key="reels",
        label="Instagram Reels",
        width=1080,
        height=1920,
        max_seconds=90.0,
        ideal_seconds=25.0,
        safe_top=180,
        safe_bottom=560,
    ),
    "shorts": Platform(
        key="shorts",
        label="YouTube Shorts",
        width=1080,
        height=1920,
        max_seconds=60.0,
        ideal_seconds=30.0,
        safe_top=160,
        safe_bottom=420,
    ),
}

# Rendering once for the strictest safe area means one file works everywhere.
UNIVERSAL = Platform(
    key="universal",
    label="All three (safe everywhere)",
    width=1080,
    height=1920,
    max_seconds=60.0,
    ideal_seconds=27.0,
    safe_top=220,
    safe_bottom=560,
)
PLATFORMS["universal"] = UNIVERSAL


def get_platform(key: str) -> Platform:
    try:
        return PLATFORMS[key]
    except KeyError:
        raise ValueError(
            f"Unknown platform {key!r}. Choose from: {', '.join(PLATFORMS)}"
        ) from None


# --------------------------------------------------------------------------
# Creative brief -> storyboard -> render
# --------------------------------------------------------------------------


@dataclass
class Brief:
    """What the user tells us about the thing they're selling."""

    product: str
    description: str = ""
    audience: str = ""
    tone: str = "energetic"
    call_to_action: str = ""
    key_points: list[str] = field(default_factory=list)
    brand_colors: list[str] = field(default_factory=list)
    # Visual direction handed to the image model, e.g. "cinematic product photography".
    visual_style: str = "cinematic product photography, soft studio light, shallow depth of field"
    language: str = "English"
    scene_count: int = 5

    def to_prompt_block(self) -> str:
        lines = [f"PRODUCT: {self.product}"]
        if self.description:
            lines.append(f"WHAT IT IS: {self.description}")
        if self.audience:
            lines.append(f"TARGET AUDIENCE: {self.audience}")
        if self.key_points:
            lines.append("KEY SELLING POINTS:")
            lines += [f"  - {p}" for p in self.key_points]
        if self.call_to_action:
            lines.append(f"CALL TO ACTION: {self.call_to_action}")
        lines.append(f"TONE: {self.tone}")
        lines.append(f"LANGUAGE: {self.language}")
        lines.append(f"VISUAL STYLE: {self.visual_style}")
        return "\n".join(lines)


@dataclass
class Scene:
    """One beat of the ad: a visual, a line of voiceover, a caption."""

    narration: str
    caption: str
    image_prompt: str
    # Populated as the pipeline runs.
    image_path: str | None = None
    audio_path: str | None = None
    video_path: str | None = None  # set when a true text-to-video model is used
    duration: float = 0.0

    def sanitized(self) -> "Scene":
        self.narration = _clean_text(self.narration)
        self.caption = _clean_text(self.caption)
        self.image_prompt = _clean_text(self.image_prompt)
        return self


@dataclass
class Storyboard:
    hook: str
    scenes: list[Scene]
    cta: str = ""
    hashtags: list[str] = field(default_factory=list)
    title: str = ""

    @property
    def duration(self) -> float:
        return sum(s.duration for s in self.scenes)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_llm_json(cls, raw: str) -> "Storyboard":
        """Parse a model response into a storyboard, tolerating chatty output."""
        data = _extract_json_object(raw)
        scenes_raw = data.get("scenes") or []
        if not scenes_raw:
            raise ValueError("Model returned a storyboard with no scenes")
        scenes = []
        for item in scenes_raw:
            if not isinstance(item, dict):
                continue
            narration = str(item.get("narration") or item.get("voiceover") or "").strip()
            if not narration:
                continue
            scenes.append(
                Scene(
                    narration=narration,
                    caption=str(item.get("caption") or narration).strip(),
                    image_prompt=str(
                        item.get("image_prompt") or item.get("visual") or narration
                    ).strip(),
                ).sanitized()
            )
        if not scenes:
            raise ValueError("Model returned scenes but none had narration text")
        hashtags = data.get("hashtags") or []
        if isinstance(hashtags, str):
            hashtags = [h for h in re.split(r"[\s,]+", hashtags) if h]
        return cls(
            hook=_clean_text(str(data.get("hook") or "")),
            scenes=scenes,
            cta=_clean_text(str(data.get("cta") or data.get("call_to_action") or "")),
            hashtags=[str(h).lstrip("#") for h in hashtags][:12],
            title=_clean_text(str(data.get("title") or "")),
        )


@dataclass
class RenderOptions:
    platform: Platform = field(default_factory=lambda: PLATFORMS["universal"])
    fps: int = 30
    # Ken Burns strength: fraction of extra zoom applied across a scene.
    zoom: float = 0.18
    transition: str = "fade"  # fade | slideleft | wipeup | none
    transition_seconds: float = 0.35
    captions: bool = True
    caption_font: str | None = None
    caption_size: int = 84
    caption_primary: str = "#FFFFFF"
    caption_accent: str = "#FFE44D"
    show_hook: bool = True
    music_path: str | None = None
    music_volume: float = 0.12
    voice_volume: float = 1.0
    # Trailing seconds of CTA card after the last scene.
    cta_seconds: float = 1.8
    crf: int = 20


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------


def _clean_text(text: str) -> str:
    """Strip markdown scaffolding and normalize whitespace/quotes."""
    text = text.strip()
    text = re.sub(r"^[#*\->\s]+", "", text)
    text = text.replace("**", "").replace("__", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip().strip('"').strip()


def _extract_json_object(raw: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model response.

    Models wrap JSON in prose or fences often enough that retrying on a
    strict parse failure is far more expensive than just digging it out.
    """
    raw = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)```", raw, re.DOTALL)
    if fenced:
        raw = fenced.group(1).strip()
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    # Fall back to brace matching so trailing prose doesn't break us.
    start = raw.find("{")
    if start == -1:
        raise ValueError(f"No JSON object found in model response: {raw[:200]!r}")
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(raw)):
        ch = raw[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return json.loads(raw[start : i + 1])
    raise ValueError(f"Unbalanced JSON in model response: {raw[:200]!r}")
