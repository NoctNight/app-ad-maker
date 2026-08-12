"""admaker — generate short-form product ads for TikTok, Reels and Shorts."""

from .models import Brief, PLATFORMS, Platform, RenderOptions, Scene, Storyboard
from .pipeline import AdRequest, AdResult, ProviderChoice, generate_ad

__version__ = "1.0.0"

__all__ = [
    "Brief",
    "Platform",
    "PLATFORMS",
    "RenderOptions",
    "Scene",
    "Storyboard",
    "AdRequest",
    "AdResult",
    "ProviderChoice",
    "generate_ad",
    "__version__",
]
