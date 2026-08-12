"""Build ASS subtitle files for burned-in captions and hook titles.

Every piece of on-screen text goes through libass rather than ffmpeg's
`drawtext`. libass gives us per-word animation, reliable word wrapping and
outline/shadow control, and it is present in far more ffmpeg builds than a
freetype-enabled drawtext is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from .models import Platform, RenderOptions

# Words that carry the punch of a line and deserve the accent color.
_EMPHASIS_RE = re.compile(r"[\d$%]|^[A-Z]{2,}$")


@dataclass
class CaptionCue:
    text: str
    start: float
    end: float
    emphasis: bool = False


def hex_to_ass(color: str, alpha: int = 0) -> str:
    """Convert #RRGGBB to ASS's &HAABBGGRR (note: reversed byte order)."""
    value = color.strip().lstrip("#")
    if len(value) == 3:
        value = "".join(c * 2 for c in value)
    if len(value) != 6:
        raise ValueError(f"Expected a #RRGGBB color, got {color!r}")
    r, g, b = value[0:2], value[2:4], value[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ts(seconds: float) -> str:
    """Format seconds as ASS's H:MM:SS.cc timestamp."""
    seconds = max(0.0, seconds)
    hours, rem = divmod(seconds, 3600)
    minutes, secs = divmod(rem, 60)
    return f"{int(hours)}:{int(minutes):02d}:{secs:05.2f}"


def _escape(text: str) -> str:
    """Escape ASS markup characters in user/model text."""
    return (
        text.replace("\\", "\\\\")
        .replace("{", "\\{")
        .replace("}", "\\}")
        .replace("\n", "\\N")
    )


def chunk_narration(text: str, words_per_chunk: int = 3) -> list[str]:
    """Split narration into short caption chunks.

    Short-form captions read as 2-4 word bursts, not full sentences.
    Punctuation forces a break so chunks land on natural phrase boundaries.
    """
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    current: list[str] = []
    for word in text.split():
        current.append(word)
        ends_phrase = word[-1] in ",.!?;:—" if word else False
        if len(current) >= words_per_chunk or ends_phrase:
            chunks.append(" ".join(current))
            current = []
    if current:
        # Fold a lonely trailing word into the previous chunk.
        if len(current) == 1 and chunks:
            chunks[-1] += " " + current[0]
        else:
            chunks.append(" ".join(current))
    return chunks


def cues_for_scene(
    narration: str,
    start: float,
    duration: float,
    words_per_chunk: int = 3,
    min_cue: float = 0.45,
) -> list[CaptionCue]:
    """Time caption chunks across a scene proportionally to their length.

    Without forced alignment we can't get true word timings, but narration
    audio is read at a near-constant character rate, so distributing by
    character count tracks the voice closely enough to feel synced.
    """
    chunks = chunk_narration(narration, words_per_chunk)
    if not chunks or duration <= 0:
        return []

    weights = [max(len(c), 1) for c in chunks]
    total = sum(weights)
    # Don't let cues run shorter than the eye can read.
    if duration / len(chunks) < min_cue:
        keep = max(1, int(duration / min_cue))
        merged: list[str] = []
        step = max(1, len(chunks) // keep)
        for i in range(0, len(chunks), step):
            merged.append(" ".join(chunks[i : i + step]))
        chunks = merged
        weights = [max(len(c), 1) for c in chunks]
        total = sum(weights)

    cues: list[CaptionCue] = []
    cursor = start
    for chunk, weight in zip(chunks, weights):
        span = duration * (weight / total)
        emphasis = any(_EMPHASIS_RE.search(w.strip(".,!?")) for w in chunk.split())
        cues.append(
            CaptionCue(text=chunk, start=cursor, end=cursor + span, emphasis=emphasis)
        )
        cursor += span
    # Absorb rounding drift into the final cue so captions end with the scene.
    if cues:
        cues[-1].end = start + duration
    return cues


def resolve_font(font: str | None) -> tuple[str, str | None]:
    """Resolve a font spec to (family_name, fonts_dir).

    Accepts either a font family name already installed on the system, or a
    path to a .ttf/.otf file, in which case we hand libass the containing
    directory and read the real family name out of the file.
    """
    if not font:
        return "DejaVu Sans", None
    path = Path(font)
    if path.exists() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
        family = path.stem
        try:
            from PIL import ImageFont

            name = ImageFont.truetype(str(path)).getname()
            if name and name[0]:
                family = name[0]
        except Exception:  # noqa: BLE001 - filename stem is a fine fallback
            pass
        return family, str(path.parent)
    return font, None


def build_ass(
    cues: list[CaptionCue],
    platform: Platform,
    options: RenderOptions,
    hook: str = "",
    hook_duration: float = 2.6,
    cta: str = "",
    cta_start: float | None = None,
    cta_duration: float = 0.0,
) -> str:
    """Render the full ASS document for one video."""
    family, _ = resolve_font(options.caption_font)
    white = hex_to_ass(options.caption_primary)
    accent = hex_to_ass(options.caption_accent)
    outline = hex_to_ass("#000000")
    shadow = hex_to_ass("#000000", alpha=0x60)

    size = options.caption_size
    margin_h = int(platform.width * 0.09)

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {platform.width}
PlayResY: {platform.height}
WrapStyle: 0
ScaledBorderAndShadow: yes
YCbCr Matrix: TV.709

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{family},{size},{white},{white},{outline},{shadow},-1,0,0,0,100,100,0,0,1,{max(4, size // 14)},{max(2, size // 24)},5,{margin_h},{margin_h},0,1
Style: Accent,{family},{size},{accent},{accent},{outline},{shadow},-1,0,0,0,100,100,0,0,1,{max(4, size // 14)},{max(2, size // 24)},5,{margin_h},{margin_h},0,1
Style: Hook,{family},{int(size * 1.12)},{white},{white},{outline},{shadow},-1,0,0,0,100,100,0,0,1,{max(5, size // 12)},{max(2, size // 22)},5,{margin_h},{margin_h},0,1
Style: Cta,{family},{int(size * 1.25)},{accent},{accent},{outline},{shadow},-1,0,0,0,100,100,0,0,1,{max(6, size // 11)},{max(3, size // 20)},5,{margin_h},{margin_h},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

    events: list[str] = []
    caption_y = platform.caption_center_y
    center_x = platform.width // 2

    # Hook: sits high in the frame during the opening seconds, where the
    # thumb-stopping text belongs.
    if hook and options.show_hook:
        hook_y = platform.safe_top + int(platform.height * 0.11)
        tags = (
            rf"{{\an5\pos({center_x},{hook_y})\fad(220,260)"
            rf"\fscx86\fscy86\t(0,220,\fscx100\fscy100)}}"
        )
        events.append(
            f"Dialogue: 0,{_ts(0.12)},{_ts(hook_duration)},Hook,,0,0,0,,{tags}{_escape(hook)}"
        )

    for cue in cues:
        style = "Accent" if cue.emphasis else "Caption"
        # A quick scale-up on entry gives the captions their signature snap.
        tags = (
            rf"{{\an5\pos({center_x},{caption_y})"
            rf"\fscx84\fscy84\t(0,130,\fscx100\fscy100)\fad(90,70)}}"
        )
        events.append(
            f"Dialogue: 0,{_ts(cue.start)},{_ts(cue.end)},{style},,0,0,0,,"
            f"{tags}{_escape(cue.text)}"
        )

    if cta and cta_start is not None and cta_duration > 0:
        cta_y = platform.height // 2
        tags = (
            rf"{{\an5\pos({center_x},{cta_y})\fad(260,200)"
            rf"\fscx78\fscy78\t(0,300,\fscx104\fscy104)\t(300,420,\fscx100\fscy100)}}"
        )
        events.append(
            f"Dialogue: 1,{_ts(cta_start)},{_ts(cta_start + cta_duration)},Cta,,0,0,0,,"
            f"{tags}{_escape(cta)}"
        )

    return header + "\n".join(events) + "\n"


def write_ass(path: str | Path, content: str) -> str:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)
