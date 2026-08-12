"""Image providers: one still frame per scene.

Every provider writes a JPEG/PNG at the requested size and returns its path.
The renderer takes it from there.
"""

from __future__ import annotations

import base64
import hashlib
import math
import random
import time
import urllib.parse
from pathlib import Path

from .base import (
    IMAGE,
    ImageProvider,
    ProviderError,
    ProviderInfo,
    download,
    http_retry,
    register,
    request_json,
)

# Appended to every prompt. Image models render text as convincing gibberish,
# which looks broken next to real burned-in captions.
NO_TEXT = (
    "no text, no words, no letters, no logos, no watermark, no captions, "
    "no user interface"
)


def _augment(prompt: str) -> str:
    return f"{prompt}. Vertical 9:16 composition. {NO_TEXT}"


# --------------------------------------------------------------------------
# Pollinations — free, no key
# --------------------------------------------------------------------------


@register
class PollinationsImage(ImageProvider):
    info = ProviderInfo(
        name="pollinations",
        kind=IMAGE,
        label="Pollinations / Flux (free, no key)",
        requires_key=False,
        cost="$0 — no signup, no key.",
        signup_url="https://pollinations.ai",
        notes="Flux-quality stills for free. Anonymous use is rate limited.",
        tier="free",
        models=["flux", "turbo"],
        default_model="flux",
    )

    def generate(
        self, prompt: str, out_path: str | Path, width: int, height: int, seed: int = 0
    ) -> str:
        encoded = urllib.parse.quote(_augment(prompt), safe="")
        params = urllib.parse.urlencode(
            {
                "width": width,
                "height": height,
                "model": self.model or "flux",
                "seed": seed,
                "nologo": "true",
                "enhance": "true",
            }
        )
        url = f"https://image.pollinations.ai/prompt/{encoded}?{params}"
        if self.api_key:
            url += f"&token={urllib.parse.quote(self.api_key)}"
        return http_retry(
            lambda: download(url, out_path, timeout=300),
            desc="Pollinations image",
            base_delay=6.0,  # the anonymous tier throttles hard
        )


# --------------------------------------------------------------------------
# OpenAI images
# --------------------------------------------------------------------------


@register
class OpenAIImage(ImageProvider):
    info = ProviderInfo(
        name="openai",
        kind=IMAGE,
        label="OpenAI (gpt-image-1)",
        requires_key=True,
        env_var="OPENAI_API_KEY",
        cost="~$0.02-0.19 per image depending on quality",
        signup_url="https://platform.openai.com/api-keys",
        tier="paid",
        models=["gpt-image-1", "dall-e-3"],
        default_model="gpt-image-1",
    )

    def generate(
        self, prompt: str, out_path: str | Path, width: int, height: int, seed: int = 0
    ) -> str:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        def call() -> str:
            data = request_json(
                "POST",
                "https://api.openai.com/v1/images/generations",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "model": self.model,
                    "prompt": _augment(prompt),
                    # The API only offers fixed sizes; 1024x1536 is the
                    # closest portrait ratio and the renderer rescales.
                    "size": "1024x1536",
                    "n": 1,
                },
                timeout=300,
            )
            item = (data.get("data") or [{}])[0]
            if item.get("b64_json"):
                out_path.write_bytes(base64.b64decode(item["b64_json"]))
                return str(out_path)
            if item.get("url"):
                return download(item["url"], out_path)
            raise ProviderError(f"No image in OpenAI response: {data}")

        return http_retry(call, desc="OpenAI image")


# --------------------------------------------------------------------------
# Google Gemini / Imagen
# --------------------------------------------------------------------------


@register
class GeminiImage(ImageProvider):
    info = ProviderInfo(
        name="gemini",
        kind=IMAGE,
        label="Google Gemini image",
        requires_key=True,
        env_var="GEMINI_API_KEY",
        cost="Free tier available on AI Studio keys; paid tier is per-image.",
        signup_url="https://aistudio.google.com/apikey",
        tier="freemium",
        models=["gemini-2.5-flash-image"],
        default_model="gemini-2.5-flash-image",
    )

    def generate(
        self, prompt: str, out_path: str | Path, width: int, height: int, seed: int = 0
    ) -> str:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{self.model}:generateContent"
        )

        def call() -> str:
            data = request_json(
                "POST",
                url,
                headers={
                    "x-goog-api-key": self.api_key or "",
                    "Content-Type": "application/json",
                },
                json_body={
                    "contents": [{"parts": [{"text": _augment(prompt)}]}],
                    "generationConfig": {"responseModalities": ["IMAGE"]},
                },
                timeout=300,
            )
            for candidate in data.get("candidates", []):
                for part in candidate.get("content", {}).get("parts", []):
                    blob = part.get("inlineData") or part.get("inline_data")
                    if blob and blob.get("data"):
                        out_path.write_bytes(base64.b64decode(blob["data"]))
                        return str(out_path)
            raise ProviderError(f"No image data in Gemini response: {str(data)[:400]}")

        return http_retry(call, desc="Gemini image")


# --------------------------------------------------------------------------
# fal.ai — cheap Flux
# --------------------------------------------------------------------------


@register
class FalImage(ImageProvider):
    info = ProviderInfo(
        name="fal",
        kind=IMAGE,
        label="fal.ai (Flux)",
        requires_key=True,
        env_var="FAL_KEY",
        cost="~$0.003-0.025 per image. Pay as you go, no subscription.",
        signup_url="https://fal.ai/dashboard/keys",
        notes="Cheapest good-quality paid stills; ~$1 covers dozens of ads.",
        tier="paid",
        models=["fal-ai/flux/schnell", "fal-ai/flux/dev"],
        default_model="fal-ai/flux/schnell",
    )

    def generate(
        self, prompt: str, out_path: str | Path, width: int, height: int, seed: int = 0
    ) -> str:
        def call() -> str:
            data = request_json(
                "POST",
                f"https://fal.run/{self.model}",
                headers={
                    "Authorization": f"Key {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "prompt": _augment(prompt),
                    "image_size": "portrait_16_9",
                    "num_images": 1,
                    "seed": seed or None,
                },
                timeout=300,
            )
            images = data.get("images") or []
            if not images or not images[0].get("url"):
                raise ProviderError(f"No image in fal response: {str(data)[:400]}")
            return download(images[0]["url"], out_path)

        return http_retry(call, desc="fal.ai image")


# --------------------------------------------------------------------------
# Offline — procedural, no network
# --------------------------------------------------------------------------


@register
class OfflineImage(ImageProvider):
    """Deterministic abstract art derived from the prompt text.

    Not a stand-in for a real image model, but it produces attractive,
    scene-distinct backgrounds so the full pipeline can be exercised (and
    tested) with no network and no keys.
    """

    info = ProviderInfo(
        name="offline",
        kind=IMAGE,
        label="Offline gradient art (no network)",
        requires_key=False,
        cost="$0",
        notes="Procedural backgrounds. For testing the renderer, not for shipping.",
        tier="free",
    )

    def generate(
        self, prompt: str, out_path: str | Path, width: int, height: int, seed: int = 0
    ) -> str:
        from PIL import Image, ImageDraw, ImageFilter

        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)

        digest = hashlib.sha256(f"{prompt}{seed}".encode()).digest()
        rng = random.Random(digest)

        # Two harmonious hues a third of the wheel apart.
        base_hue = rng.random()
        hue_b = (base_hue + rng.uniform(0.18, 0.42)) % 1.0
        top = _hsv_to_rgb(base_hue, 0.62, 0.32)
        bottom = _hsv_to_rgb(hue_b, 0.70, 0.78)

        img = Image.new("RGB", (width, height))
        draw = ImageDraw.Draw(img)
        for y in range(height):
            t = y / max(1, height - 1)
            # Ease the ramp so the gradient reads as light, not as a ramp.
            e = t * t * (3 - 2 * t)
            draw.line(
                [(0, y), (width, y)],
                fill=tuple(int(top[i] + (bottom[i] - top[i]) * e) for i in range(3)),
            )

        # Soft light blooms give the frame a focal point for the Ken Burns move.
        overlay = Image.new("RGB", (width, height), (0, 0, 0))
        odraw = ImageDraw.Draw(overlay)
        for _ in range(rng.randint(3, 5)):
            cx = rng.uniform(0.15, 0.85) * width
            cy = rng.uniform(0.15, 0.85) * height
            radius = rng.uniform(0.18, 0.42) * width
            color = _hsv_to_rgb((base_hue + rng.uniform(-0.1, 0.1)) % 1.0, 0.45, 0.95)
            odraw.ellipse(
                [cx - radius, cy - radius, cx + radius, cy + radius], fill=color
            )
        overlay = overlay.filter(ImageFilter.GaussianBlur(radius=width // 8))
        img = Image.blend(img, overlay, 0.38)

        # Vignette keeps the eye centered and stops captions fighting the bg.
        vignette = Image.new("L", (width, height), 0)
        vdraw = ImageDraw.Draw(vignette)
        inset = int(width * 0.08)
        vdraw.ellipse(
            [-inset, -inset, width + inset, int(height * 1.05)], fill=255
        )
        vignette = vignette.filter(ImageFilter.GaussianBlur(radius=width // 6))
        img = Image.composite(img, Image.new("RGB", (width, height), (8, 8, 12)), vignette)

        img.save(out_path, quality=92)
        return str(out_path)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    i = int(h * 6.0)
    f = h * 6.0 - i
    p, q, t = v * (1 - s), v * (1 - s * f), v * (1 - s * (1 - f))
    r, g, b = [
        (v, t, p),
        (q, v, p),
        (p, v, t),
        (p, q, v),
        (t, p, v),
        (v, p, q),
    ][i % 6]
    return int(r * 255), int(g * 255), int(b * 255)
