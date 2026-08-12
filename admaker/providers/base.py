"""Provider contracts and the registry the CLI and web UI both read from."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..models import Brief, Storyboard

# Kinds of work a provider can do.
SCRIPT = "script"
IMAGE = "image"
TTS = "tts"
VIDEO = "video"


class ProviderError(RuntimeError):
    """A provider could not fulfil a request (bad key, rate limit, outage)."""


class MissingKeyError(ProviderError):
    """The provider needs an API key and none was supplied."""


@dataclass
class ProviderInfo:
    """Everything the UI needs to describe and price a provider."""

    name: str
    kind: str
    label: str
    requires_key: bool
    env_var: str | None = None
    cost: str = ""
    signup_url: str = ""
    notes: str = ""
    # Free-tier quality/vibe, surfaced as a badge in the UI.
    tier: str = "paid"  # free | freemium | paid
    voices: list[str] = field(default_factory=list)
    models: list[str] = field(default_factory=list)
    default_model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "label": self.label,
            "requires_key": self.requires_key,
            "env_var": self.env_var,
            "cost": self.cost,
            "signup_url": self.signup_url,
            "notes": self.notes,
            "tier": self.tier,
            "voices": self.voices,
            "models": self.models,
            "default_model": self.default_model,
        }


class Provider:
    """Base for every provider. Subclasses set `info` and implement their kind."""

    info: ProviderInfo

    def __init__(self, api_key: str | None = None, model: str | None = None, **kwargs: Any):
        self.api_key = (api_key or "").strip() or None
        self.model = model or self.info.default_model
        self.options = kwargs
        if self.info.requires_key and not self.api_key:
            raise MissingKeyError(
                f"{self.info.label} needs an API key. "
                f"Set {self.info.env_var} or paste one in the app. "
                f"Get one at {self.info.signup_url}"
            )


class ScriptProvider(Provider):
    def generate(self, brief: Brief) -> Storyboard:  # pragma: no cover - interface
        raise NotImplementedError


class ImageProvider(Provider):
    def generate(
        self, prompt: str, out_path: str | Path, width: int, height: int, seed: int = 0
    ) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class TTSProvider(Provider):
    def synthesize(
        self, text: str, out_path: str | Path, voice: str | None = None
    ) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class VideoProvider(Provider):
    def generate(
        self,
        prompt: str,
        out_path: str | Path,
        seconds: float,
        width: int,
        height: int,
        image_path: str | None = None,
    ) -> str:  # pragma: no cover - interface
        raise NotImplementedError


# --------------------------------------------------------------------------
# Registry
# --------------------------------------------------------------------------

_REGISTRY: dict[str, dict[str, type[Provider]]] = {
    SCRIPT: {},
    IMAGE: {},
    TTS: {},
    VIDEO: {},
}


def register(cls: type[Provider]) -> type[Provider]:
    """Class decorator: add a provider to the registry."""
    _REGISTRY[cls.info.kind][cls.info.name] = cls
    return cls


def get_provider_class(kind: str, name: str) -> type[Provider]:
    try:
        return _REGISTRY[kind][name]
    except KeyError:
        available = ", ".join(sorted(_REGISTRY.get(kind, {}))) or "(none)"
        raise ProviderError(
            f"Unknown {kind} provider {name!r}. Available: {available}"
        ) from None


def build(kind: str, name: str, api_key: str | None = None, **kwargs: Any) -> Provider:
    return get_provider_class(kind, name)(api_key=api_key, **kwargs)


def list_providers(kind: str | None = None) -> list[ProviderInfo]:
    kinds: Iterable[str] = [kind] if kind else _REGISTRY.keys()
    out: list[ProviderInfo] = []
    for k in kinds:
        out.extend(cls.info for cls in _REGISTRY[k].values())
    # Free options first: they're what most people should start on.
    order = {"free": 0, "freemium": 1, "paid": 2}
    return sorted(out, key=lambda i: (i.kind, order.get(i.tier, 3), i.label))


# --------------------------------------------------------------------------
# Shared HTTP helper
# --------------------------------------------------------------------------


def http_retry(
    fn: Callable[[], Any],
    *,
    attempts: int = 4,
    base_delay: float = 2.0,
    desc: str = "request",
) -> Any:
    """Run `fn`, retrying transient failures with exponential backoff.

    Free tiers rate-limit aggressively, so backing off is the normal path
    rather than an error path.
    """
    last: Exception | None = None
    for attempt in range(attempts):
        try:
            return fn()
        except ProviderError:
            raise  # auth/config problems will not fix themselves
        except Exception as exc:  # noqa: BLE001 - network layer raises broadly
            last = exc
            if attempt == attempts - 1:
                break
            time.sleep(base_delay * (2**attempt))
    raise ProviderError(f"{desc} failed after {attempts} attempts: {last}") from last


def request_json(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json_body: dict[str, Any] | None = None,
    timeout: int = 120,
) -> dict[str, Any]:
    """POST/GET JSON with helpful errors for the usual API-key mistakes."""
    import requests

    resp = requests.request(
        method, url, headers=headers or {}, json=json_body, timeout=timeout
    )
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"{url} rejected the API key (HTTP {resp.status_code}). "
            f"Check the key is correct and has credit. Response: {resp.text[:300]}"
        )
    if resp.status_code == 429:
        raise RuntimeError(f"Rate limited by {url} (HTTP 429)")  # retryable
    if resp.status_code >= 400:
        raise ProviderError(f"{url} returned HTTP {resp.status_code}: {resp.text[:400]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError(f"{url} returned non-JSON: {resp.text[:300]}") from exc


def download(url: str, out_path: str | Path, timeout: int = 300) -> str:
    """Stream a URL to disk, failing loudly on empty or error responses."""
    import requests

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=timeout) as resp:
        if resp.status_code >= 400:
            raise RuntimeError(f"Download failed HTTP {resp.status_code}: {url}")
        written = 0
        with open(out_path, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=65536):
                if chunk:
                    fh.write(chunk)
                    written += len(chunk)
    if written < 1024:
        raise RuntimeError(f"Download from {url} produced only {written} bytes")
    return str(out_path)
