"""Text-to-speech providers: one narration track per scene.

Each returns a path to an audio file. The renderer probes its real duration
and lets that drive the scene's length, so pacing always matches the voice.
"""

from __future__ import annotations

import base64
from pathlib import Path

from .base import (
    TTS,
    ProviderError,
    ProviderInfo,
    TTSProvider,
    download,
    http_retry,
    register,
    request_json,
)

# Speech is read at roughly 2.5 words/second in an energetic ad read.
WORDS_PER_SECOND = 2.5


def estimate_seconds(text: str) -> float:
    """Estimate spoken duration, used only when real audio is unavailable."""
    words = max(1, len(text.split()))
    return max(1.2, words / WORDS_PER_SECOND)


# --------------------------------------------------------------------------
# Microsoft Edge TTS — free, no key
# --------------------------------------------------------------------------


@register
class EdgeTTS(TTSProvider):
    """Neural voices from the Edge read-aloud service.

    Genuinely free with no account, and the quality sits close to paid
    services — which makes it the right default for this app.
    """

    info = ProviderInfo(
        name="edge",
        kind=TTS,
        label="Edge Neural TTS (free, no key)",
        requires_key=False,
        cost="$0 — no signup, no key, unlimited in practice.",
        signup_url="https://github.com/rany2/edge-tts",
        notes="Needs `pip install edge-tts`. Best free voice quality available.",
        tier="free",
        voices=[
            "en-US-AriaNeural",
            "en-US-GuyNeural",
            "en-US-JennyNeural",
            "en-US-AndrewMultilingualNeural",
            "en-US-AvaMultilingualNeural",
            "en-GB-SoniaNeural",
            "en-AU-NatashaNeural",
        ],
        default_model="en-US-AvaMultilingualNeural",
    )

    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:
        try:
            import asyncio

            import edge_tts
        except ImportError as exc:
            raise ProviderError(
                "Edge TTS needs its package: pip install edge-tts"
            ) from exc

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        chosen = voice or self.model or "en-US-AvaMultilingualNeural"
        rate = self.options.get("rate", "+8%")  # ads read slightly hot

        async def run() -> None:
            communicate = edge_tts.Communicate(text, chosen, rate=rate)
            await communicate.save(str(out_path))

        def call() -> str:
            asyncio.run(run())
            if not out_path.exists() or out_path.stat().st_size < 512:
                raise RuntimeError("Edge TTS produced an empty file")
            return str(out_path)

        return http_retry(call, desc="Edge TTS")


# --------------------------------------------------------------------------
# ElevenLabs
# --------------------------------------------------------------------------


@register
class ElevenLabsTTS(TTSProvider):
    info = ProviderInfo(
        name="elevenlabs",
        kind=TTS,
        label="ElevenLabs",
        requires_key=True,
        env_var="ELEVENLABS_API_KEY",
        cost="Free tier ~10k characters/month; paid from $5/month.",
        signup_url="https://elevenlabs.io/app/settings/api-keys",
        notes="The most expressive voices. 10k chars is roughly 25 short ads.",
        tier="freemium",
        voices=["21m00Tcm4TlvDq8ikWAM", "pNInz6obpgDQGcFmaJgB", "EXAVITQu4vr4xnSDxMaL"],
        default_model="eleven_turbo_v2_5",
    )

    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:
        import requests

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        voice_id = voice or self.options.get("voice") or "21m00Tcm4TlvDq8ikWAM"

        def call() -> str:
            resp = requests.post(
                f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}",
                headers={
                    "xi-api-key": self.api_key or "",
                    "Content-Type": "application/json",
                },
                json={
                    "text": text,
                    "model_id": self.model,
                    "voice_settings": {"stability": 0.4, "similarity_boost": 0.75},
                },
                timeout=180,
            )
            if resp.status_code in (401, 403):
                raise ProviderError(
                    f"ElevenLabs rejected the API key (HTTP {resp.status_code})."
                )
            if resp.status_code >= 400:
                raise ProviderError(
                    f"ElevenLabs HTTP {resp.status_code}: {resp.text[:300]}"
                )
            out_path.write_bytes(resp.content)
            return str(out_path)

        return http_retry(call, desc="ElevenLabs TTS")


# --------------------------------------------------------------------------
# OpenAI TTS
# --------------------------------------------------------------------------


@register
class OpenAITTS(TTSProvider):
    info = ProviderInfo(
        name="openai",
        kind=TTS,
        label="OpenAI TTS",
        requires_key=True,
        env_var="OPENAI_API_KEY",
        cost="~$0.015 per 1k characters — well under a cent per ad.",
        signup_url="https://platform.openai.com/api-keys",
        tier="paid",
        voices=["alloy", "echo", "fable", "nova", "onyx", "shimmer"],
        default_model="gpt-4o-mini-tts",
    )

    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:
        import requests

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def call() -> str:
            resp = requests.post(
                "https://api.openai.com/v1/audio/speech",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "input": text,
                    "voice": voice or self.options.get("voice") or "nova",
                    "response_format": "mp3",
                },
                timeout=180,
            )
            if resp.status_code in (401, 403):
                raise ProviderError("OpenAI rejected the API key.")
            if resp.status_code >= 400:
                raise ProviderError(f"OpenAI TTS HTTP {resp.status_code}: {resp.text[:300]}")
            out_path.write_bytes(resp.content)
            return str(out_path)

        return http_retry(call, desc="OpenAI TTS")


# --------------------------------------------------------------------------
# Google Cloud TTS
# --------------------------------------------------------------------------


@register
class GoogleTTS(TTSProvider):
    info = ProviderInfo(
        name="google",
        kind=TTS,
        label="Google Cloud TTS",
        requires_key=True,
        env_var="GOOGLE_TTS_API_KEY",
        cost="1M free WaveNet characters/month, then ~$16/M.",
        signup_url="https://console.cloud.google.com/apis/credentials",
        notes="The most generous free tier of any paid-grade TTS.",
        tier="freemium",
        voices=["en-US-Neural2-F", "en-US-Neural2-D", "en-GB-Neural2-A"],
        default_model="en-US-Neural2-F",
    )

    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        name = voice or self.model or "en-US-Neural2-F"
        language = "-".join(name.split("-")[:2])

        def call() -> str:
            data = request_json(
                "POST",
                f"https://texttospeech.googleapis.com/v1/text:synthesize?key={self.api_key}",
                headers={"Content-Type": "application/json"},
                json_body={
                    "input": {"text": text},
                    "voice": {"languageCode": language, "name": name},
                    "audioConfig": {"audioEncoding": "MP3", "speakingRate": 1.08},
                },
                timeout=180,
            )
            if not data.get("audioContent"):
                raise ProviderError(f"No audio in Google response: {str(data)[:300]}")
            out_path.write_bytes(base64.b64decode(data["audioContent"]))
            return str(out_path)

        return http_retry(call, desc="Google TTS")


# --------------------------------------------------------------------------
# Pollinations audio — free, no key
# --------------------------------------------------------------------------


@register
class PollinationsTTS(TTSProvider):
    info = ProviderInfo(
        name="pollinations",
        kind=TTS,
        label="Pollinations TTS (free, no key)",
        requires_key=False,
        cost="$0 — no signup.",
        signup_url="https://pollinations.ai",
        notes="Fallback when edge-tts cannot be installed. Rate limited.",
        tier="free",
        voices=["nova", "alloy", "echo", "onyx", "shimmer"],
        default_model="nova",
    )

    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:
        import urllib.parse

        chosen = voice or self.model or "nova"
        params = urllib.parse.urlencode(
            {"model": "openai-audio", "voice": chosen}
        )
        url = (
            "https://text.pollinations.ai/"
            f"{urllib.parse.quote(text, safe='')}?{params}"
        )
        return http_retry(
            lambda: download(url, out_path, timeout=300),
            desc="Pollinations TTS",
            base_delay=6.0,
        )


# --------------------------------------------------------------------------
# Offline — silent placeholder
# --------------------------------------------------------------------------


@register
class OfflineTTS(TTSProvider):
    """Silence of the right length, so timing can be tested without network.

    This deliberately produces no speech: it exists to exercise the render
    pipeline offline, not to stand in for a voice.
    """

    info = ProviderInfo(
        name="offline",
        kind=TTS,
        label="Offline silence (no network)",
        requires_key=False,
        cost="$0",
        notes="SILENT placeholder track sized to the script. Testing only.",
        tier="free",
    )

    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:
        from ..ffmpeg import run

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        seconds = estimate_seconds(text)
        run(
            [
                "-f", "lavfi",
                "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
                "-t", f"{seconds:.3f}",
                "-c:a", "libmp3lame", "-q:a", "6",
                str(out_path),
            ],
            desc="offline silent narration",
        )
        return str(out_path)
