"""Optional true text-to-video providers.

These replace the Ken Burns still with real generated motion. They are the
expensive path — at roughly $0.05-0.30 per second of output, a single 30s ad
costs more than a month of everything else in this app — so the pipeline
treats them as an upgrade, never a requirement.
"""

from __future__ import annotations

import time
from pathlib import Path

from .base import (
    VIDEO,
    ProviderError,
    ProviderInfo,
    VideoProvider,
    download,
    http_retry,
    register,
    request_json,
)


@register
class FalVideo(VideoProvider):
    """fal.ai hosts most of the current video models behind one API."""

    info = ProviderInfo(
        name="fal",
        kind=VIDEO,
        label="fal.ai video (Kling / Wan / Seedance)",
        requires_key=True,
        env_var="FAL_KEY",
        cost="~$0.05/s (Wan) to ~$0.30/s (Seedance). Budget per clip, not per month.",
        signup_url="https://fal.ai/dashboard/keys",
        notes="Pay as you go with no subscription. Start with Wan for cost.",
        tier="paid",
        models=[
            "fal-ai/wan/v2.2-5b/text-to-video",
            "fal-ai/kling-video/v2/master/text-to-video",
            "fal-ai/minimax/hailuo-02/standard/text-to-video",
        ],
        default_model="fal-ai/wan/v2.2-5b/text-to-video",
    )

    def generate(
        self,
        prompt: str,
        out_path: str | Path,
        seconds: float,
        width: int,
        height: int,
        image_path: str | None = None,
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
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "duration": str(max(5, int(round(seconds)))),
                },
                timeout=900,
            )
            url = (data.get("video") or {}).get("url")
            if not url:
                raise ProviderError(f"No video in fal response: {str(data)[:400]}")
            return download(url, out_path, timeout=900)

        return http_retry(call, desc="fal.ai video", attempts=2, base_delay=10.0)


@register
class ReplicateVideo(VideoProvider):
    """Replicate exposes the same model families with async prediction polling."""

    info = ProviderInfo(
        name="replicate",
        kind=VIDEO,
        label="Replicate video",
        requires_key=True,
        env_var="REPLICATE_API_TOKEN",
        cost="Per-second billing, comparable to fal.ai.",
        signup_url="https://replicate.com/account/api-tokens",
        tier="paid",
        models=["wan-video/wan-2.5-t2v", "kwaivgi/kling-v2.1"],
        default_model="wan-video/wan-2.5-t2v",
    )

    def generate(
        self,
        prompt: str,
        out_path: str | Path,
        seconds: float,
        width: int,
        height: int,
        image_path: str | None = None,
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        created = request_json(
            "POST",
            f"https://api.replicate.com/v1/models/{self.model}/predictions",
            headers=headers,
            json_body={
                "input": {
                    "prompt": prompt,
                    "aspect_ratio": "9:16",
                    "duration": max(5, int(round(seconds))),
                }
            },
            timeout=120,
        )

        # Replicate is async: poll the prediction until it settles.
        prediction_url = (created.get("urls") or {}).get("get")
        if not prediction_url:
            raise ProviderError(f"Replicate did not return a poll URL: {created}")

        deadline = time.monotonic() + 900
        while time.monotonic() < deadline:
            state = request_json("GET", prediction_url, headers=headers, timeout=60)
            status = state.get("status")
            if status == "succeeded":
                output = state.get("output")
                url = output[0] if isinstance(output, list) else output
                if not url:
                    raise ProviderError("Replicate succeeded but returned no output")
                return download(str(url), out_path, timeout=900)
            if status in ("failed", "canceled"):
                raise ProviderError(f"Replicate {status}: {state.get('error')}")
            time.sleep(5)
        raise ProviderError("Replicate prediction timed out after 15 minutes")
