"""API key resolution and the recommended provider presets."""

from __future__ import annotations

import os
from pathlib import Path

from .providers import IMAGE, SCRIPT, TTS, VIDEO, get_provider_class

# Named stacks so a user can pick an intent instead of four providers.
PRESETS: dict[str, dict[str, str]] = {
    "free": {
        "label": "Completely free — no signup at all",
        "script": "pollinations",
        "image": "pollinations",
        "tts": "edge",
        "description": (
            "Costs nothing and needs no account. Pollinations writes the script "
            "and paints the stills; Edge Neural TTS voices it."
        ),
    },
    "free-plus": {
        "label": "Free, one signup, much better writing",
        "script": "groq",
        "image": "pollinations",
        "tts": "edge",
        "description": (
            "A free Groq key sharpens the ad copy, which is where quality is "
            "won. Visuals and voice stay free and key-less."
        ),
    },
    "cheap": {
        "label": "Under $5 one-off — best quality per dollar",
        "script": "anthropic",
        "image": "fal",
        "tts": "edge",
        "description": (
            "Claude writes the script and fal.ai renders Flux stills for about "
            "a cent each. A $5 top-up covers roughly a hundred ads."
        ),
    },
    "premium": {
        "label": "Real generated motion (costs real money)",
        "script": "anthropic",
        "image": "fal",
        "tts": "elevenlabs",
        "video": "fal",
        "description": (
            "True text-to-video instead of Ken Burns. Budget $1.50-$9 per ad "
            "depending on the model — this is the expensive path."
        ),
    },
    "offline": {
        "label": "Offline test — no network, no keys",
        "script": "offline",
        "image": "offline",
        "tts": "offline",
        "description": (
            "Renders a real video file with template copy, procedural art and "
            "a SILENT audio track. For verifying the install, not for posting."
        ),
    },
}


def load_dotenv(path: str | Path = ".env") -> None:
    """Load KEY=VALUE lines from a .env file without overriding real env vars."""
    file = Path(path)
    if not file.exists():
        return
    for line in file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip("'").strip('"')
        if key and key not in os.environ:
            os.environ[key] = value


def resolve_key(kind: str, provider: str, explicit: str | None = None) -> str | None:
    """Find an API key: explicit value first, then the provider's env var."""
    if explicit:
        return explicit.strip() or None
    try:
        info = get_provider_class(kind, provider).info
    except Exception:  # noqa: BLE001 - unknown providers are reported later
        return None
    if not info.env_var:
        return None
    return os.environ.get(info.env_var) or None


def preset_choices(name: str) -> dict[str, str]:
    try:
        preset = PRESETS[name]
    except KeyError:
        raise ValueError(
            f"Unknown preset {name!r}. Choose from: {', '.join(PRESETS)}"
        ) from None
    return {
        k: v for k, v in preset.items() if k in {SCRIPT, IMAGE, TTS, VIDEO}
    }
