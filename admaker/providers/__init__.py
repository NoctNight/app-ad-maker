"""Provider registry.

Importing this package registers every built-in provider. The submodule
imports below look unused but are what populate the registry.
"""

from .base import (  # noqa: F401
    IMAGE,
    SCRIPT,
    TTS,
    VIDEO,
    ImageProvider,
    MissingKeyError,
    Provider,
    ProviderError,
    ProviderInfo,
    ScriptProvider,
    TTSProvider,
    VideoProvider,
    build,
    get_provider_class,
    list_providers,
    register,
)
from . import image, script, tts, video  # noqa: F401,E402

__all__ = [
    "SCRIPT",
    "IMAGE",
    "TTS",
    "VIDEO",
    "Provider",
    "ScriptProvider",
    "ImageProvider",
    "TTSProvider",
    "VideoProvider",
    "ProviderInfo",
    "ProviderError",
    "MissingKeyError",
    "build",
    "get_provider_class",
    "list_providers",
    "register",
]
