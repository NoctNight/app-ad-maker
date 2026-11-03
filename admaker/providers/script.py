"""Script providers: turn a product brief into a shot-by-shot storyboard.

This is the creative core of the pipeline. Every provider here returns the
same `Storyboard`, so the rest of the app never learns which model wrote it.
"""

from __future__ import annotations

from typing import Any

from ..models import Brief, Scene, Storyboard
from .base import (
    SCRIPT,
    ProviderError,
    ProviderInfo,
    ScriptProvider,
    http_retry,
    register,
    request_json,
)

# --------------------------------------------------------------------------
# Shared prompt
# --------------------------------------------------------------------------

SYSTEM_PROMPT = """You are a direct-response creative director who writes \
short-form video ads that stop the scroll on TikTok, Instagram Reels and \
YouTube Shorts. You write for the ear, not the page: short sentences, \
concrete nouns, zero corporate filler.

You always reply with a single JSON object and nothing else."""


def build_user_prompt(brief: Brief, seconds: float) -> str:
    """Compose the storyboard request.

    The constraints here exist for downstream reasons: narration length
    controls video length, and the image-prompt rules stop the image model
    from rendering garbled fake text into the frame.
    """
    return f"""Write a {seconds:.0f}-second vertical video ad.

{brief.to_prompt_block()}

Return JSON in exactly this shape:
{{
  "title": "internal name for this ad",
  "hook": "the first on-screen line, max 8 words, makes someone stop scrolling",
  "scenes": [
    {{
      "narration": "one voiceover sentence, 10-18 words, conversational",
      "caption": "the same beat as 3-6 punchy on-screen words",
      "image_prompt": "a vivid description of a single still image"
    }}
  ],
  "cta": "final call to action, max 6 words",
  "hashtags": ["five", "relevant", "hashtags", "without", "hashes"]
}}

Rules:
- Exactly {brief.scene_count} scenes.
- Scene 1 is the hook: lead with the problem or a bold claim, never a greeting.
- The middle scenes each land ONE benefit. Be specific, not adjectival.
- The last scene drives the call to action.
- Total narration must read aloud in about {seconds:.0f} seconds \
(roughly {int(seconds * 2.4)} words across all scenes).
- image_prompt describes a PHOTOGRAPH: subject, setting, lighting, camera \
angle. Style them all as "{brief.visual_style}" so the ad feels like one shoot.
- image_prompt must NEVER ask for text, words, letters, logos or UI in the \
image. Image models render text as gibberish; all text is added later.
- Write in {brief.language}.
- Output only the JSON object."""


def _fallback_storyboard(brief: Brief) -> Storyboard:
    """A serviceable, model-free storyboard.

    Used by the offline provider and as a last resort so a failing script
    model degrades to a working video rather than to no video at all.
    """
    product = brief.product
    points = brief.key_points or [
        "It actually works",
        "It takes seconds, not hours",
        "People keep coming back to it",
    ]
    cta = brief.call_to_action or f"Try {product} today"

    scenes = [
        Scene(
            narration=f"Still putting up with the old way of doing this? {product} fixes that.",
            caption="Stop settling.",
            image_prompt=(
                f"A frustrated person at a cluttered desk, moody window light, "
                f"shallow depth of field, {brief.visual_style}"
            ),
        )
    ]
    for point in points[: max(1, brief.scene_count - 2)]:
        scenes.append(
            Scene(
                narration=f"{point}. That is the whole promise of {product}.",
                caption=point,
                image_prompt=(
                    f"A clean hero shot of {product} on a minimal surface, soft "
                    f"rim light, premium product photography, {brief.visual_style}"
                ),
            )
        )
    scenes.append(
        Scene(
            narration=f"{cta}. You will wonder why you waited.",
            caption=cta,
            image_prompt=(
                f"A satisfied person using {product} in warm golden hour light, "
                f"candid lifestyle photography, {brief.visual_style}"
            ),
        )
    )
    return Storyboard(
        hook="You are doing this the hard way.",
        scenes=[s.sanitized() for s in scenes[: brief.scene_count]],
        cta=cta,
        hashtags=[
            product.split()[0].lower().strip(".,"),
            "tiktokmademebuyit",
            "smallbusiness",
            "musthave",
            "fyp",
        ],
        title=f"{product} — short form ad",
    )


# --------------------------------------------------------------------------
# Anthropic (Claude)
# --------------------------------------------------------------------------


STORYBOARD_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "scenes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "narration": {"type": "string"},
                    "caption": {"type": "string"},
                    "image_prompt": {"type": "string"},
                },
                "required": ["narration", "caption", "image_prompt"],
                "additionalProperties": False,
            },
        },
        "cta": {"type": "string"},
        "hashtags": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["title", "hook", "scenes", "cta", "hashtags"],
    "additionalProperties": False,
}


@register
class AnthropicScript(ScriptProvider):
    """Claude via the official Anthropic SDK.

    Uses structured outputs so the response is schema-valid JSON rather than
    JSON-shaped prose, which removes the most common retry loop.
    """

    info = ProviderInfo(
        name="anthropic",
        kind=SCRIPT,
        label="Claude (Anthropic)",
        requires_key=True,
        env_var="ANTHROPIC_API_KEY",
        cost="~$0.01-0.03 per ad script on Opus; far less on Haiku",
        signup_url="https://console.anthropic.com/settings/keys",
        notes="Best-in-class ad copy. Needs `pip install anthropic`.",
        tier="paid",
        models=["claude-opus-5", "claude-sonnet-5", "claude-haiku-4-5"],
        default_model="claude-opus-5",
    )

    def generate(self, brief: Brief) -> Storyboard:
        try:
            import anthropic
        except ImportError as exc:
            raise ProviderError(
                "The Anthropic provider needs the SDK: pip install anthropic"
            ) from exc

        client = anthropic.Anthropic(api_key=self.api_key)
        seconds = float(self.options.get("seconds", 27))

        def call() -> str:
            message = client.messages.create(
                model=self.model,
                max_tokens=4000,
                system=SYSTEM_PROMPT,
                output_config={
                    "effort": "medium",
                    "format": {"type": "json_schema", "schema": STORYBOARD_SCHEMA},
                },
                messages=[
                    {"role": "user", "content": build_user_prompt(brief, seconds)}
                ],
            )
            if message.stop_reason == "refusal":
                raise ProviderError(
                    "Claude declined this brief. Rephrase the product description."
                )
            return "".join(b.text for b in message.content if b.type == "text")

        return Storyboard.from_llm_json(http_retry(call, desc="Claude storyboard"))


# --------------------------------------------------------------------------
# OpenAI-compatible (OpenAI, Groq, OpenRouter, Together, DeepSeek, Ollama...)
# --------------------------------------------------------------------------


class _OpenAICompatible(ScriptProvider):
    """Any service that speaks the /chat/completions shape.

    One implementation covers a large slice of the market, including the
    generous free tiers (Groq, OpenRouter) that make this app usable at $0.
    """

    base_url: str = "https://api.openai.com/v1"

    def generate(self, brief: Brief) -> Storyboard:
        seconds = float(self.options.get("seconds", 27))
        url = self.options.get("base_url", self.base_url).rstrip("/") + "/chat/completions"

        def call() -> str:
            data = request_json(
                "POST",
                url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json_body={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(brief, seconds)},
                    ],
                    "response_format": {"type": "json_object"},
                    "max_tokens": 4000,
                },
            )
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as exc:
                raise ProviderError(f"Unexpected response shape: {data}") from exc

        return Storyboard.from_llm_json(
            http_retry(call, desc=f"{self.info.label} storyboard")
        )


@register
class OpenAIScript(_OpenAICompatible):
    info = ProviderInfo(
        name="openai",
        kind=SCRIPT,
        label="OpenAI",
        requires_key=True,
        env_var="OPENAI_API_KEY",
        cost="Pay as you go, a few cents per script",
        signup_url="https://platform.openai.com/api-keys",
        tier="paid",
        models=["gpt-4o-mini", "gpt-4o"],
        default_model="gpt-4o-mini",
    )


@register
class GroqScript(_OpenAICompatible):
    base_url = "https://api.groq.com/openai/v1"
    info = ProviderInfo(
        name="groq",
        kind=SCRIPT,
        label="Groq",
        requires_key=True,
        env_var="GROQ_API_KEY",
        cost="FREE tier with generous daily limits. No card required.",
        signup_url="https://console.groq.com/keys",
        notes="Fastest free option for scripting. Open-weight models.",
        tier="free",
        models=[
            "llama-3.3-70b-versatile",
            "llama-3.1-8b-instant",
            "openai/gpt-oss-120b",
        ],
        default_model="llama-3.3-70b-versatile",
    )


@register
class OpenRouterScript(_OpenAICompatible):
    base_url = "https://openrouter.ai/api/v1"
    info = ProviderInfo(
        name="openrouter",
        kind=SCRIPT,
        label="OpenRouter",
        requires_key=True,
        env_var="OPENROUTER_API_KEY",
        cost="Has genuinely free models (`:free` suffix). Card optional.",
        signup_url="https://openrouter.ai/keys",
        notes="One key, hundreds of models, including free ones.",
        tier="free",
        models=[
            "meta-llama/llama-3.3-70b-instruct:free",
            "deepseek/deepseek-chat-v3-0324:free",
            "anthropic/claude-sonnet-4.5",
        ],
        default_model="meta-llama/llama-3.3-70b-instruct:free",
    )


# --------------------------------------------------------------------------
# Google Gemini
# --------------------------------------------------------------------------


@register
class GeminiScript(ScriptProvider):
    info = ProviderInfo(
        name="gemini",
        kind=SCRIPT,
        label="Google Gemini",
        requires_key=True,
        env_var="GEMINI_API_KEY",
        cost="Free tier: generous daily request quota on Flash models.",
        signup_url="https://aistudio.google.com/apikey",
        notes="Free API key from AI Studio, no credit card.",
        tier="free",
        models=["gemini-2.5-flash", "gemini-2.5-pro"],
        default_model="gemini-2.5-flash",
    )

    def generate(self, brief: Brief) -> Storyboard:
        seconds = float(self.options.get("seconds", 27))
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
                    "system_instruction": {"parts": [{"text": SYSTEM_PROMPT}]},
                    "contents": [
                        {"parts": [{"text": build_user_prompt(brief, seconds)}]}
                    ],
                    "generationConfig": {"response_mime_type": "application/json"},
                },
            )
            try:
                return data["candidates"][0]["content"]["parts"][0]["text"]
            except (KeyError, IndexError) as exc:
                raise ProviderError(f"Unexpected Gemini response: {data}") from exc

        return Storyboard.from_llm_json(http_retry(call, desc="Gemini storyboard"))


# --------------------------------------------------------------------------
# Pollinations — free, no key, no signup
# --------------------------------------------------------------------------


@register
class PollinationsScript(ScriptProvider):
    """Free text generation with no account at all.

    The zero-friction default: someone can render a real ad before deciding
    whether any of this is worth an API key.
    """

    info = ProviderInfo(
        name="pollinations",
        kind=SCRIPT,
        label="Pollinations (free, no key)",
        requires_key=False,
        cost="$0 — no signup, no key, no card.",
        signup_url="https://pollinations.ai",
        notes="Rate limited to roughly one request per 15s when anonymous.",
        tier="free",
        models=["openai", "mistral"],
        default_model="openai",
    )

    def generate(self, brief: Brief) -> Storyboard:
        seconds = float(self.options.get("seconds", 27))

        def call() -> str:
            data = request_json(
                "POST",
                "https://text.pollinations.ai/openai",
                headers={"Content-Type": "application/json"},
                json_body={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": build_user_prompt(brief, seconds)},
                    ],
                    "response_format": {"type": "json_object"},
                },
                timeout=180,
            )
            try:
                return data["choices"][0]["message"]["content"]
            except (KeyError, IndexError) as exc:
                raise ProviderError(f"Unexpected Pollinations response: {data}") from exc

        try:
            return Storyboard.from_llm_json(
                http_retry(call, desc="Pollinations storyboard")
            )
        except (ProviderError, ValueError):
            # The free endpoint has no SLA; a template ad beats a crash.
            return _fallback_storyboard(brief)


# --------------------------------------------------------------------------
# Offline — deterministic, no network
# --------------------------------------------------------------------------


@register
class OfflineScript(ScriptProvider):
    info = ProviderInfo(
        name="offline",
        kind=SCRIPT,
        label="Offline template (no network)",
        requires_key=False,
        cost="$0",
        notes="Template-based storyboard. Use to test the renderer end to end.",
        tier="free",
    )

    def generate(self, brief: Brief) -> Storyboard:
        return _fallback_storyboard(brief)
