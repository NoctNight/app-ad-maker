"""Command line interface.

    python -m admaker make --product "Ceramic pour-over" --preset free
    python -m admaker providers
    python -m admaker serve
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__
from .config import PRESETS, load_dotenv, preset_choices, resolve_key
from .ffmpeg import FFmpegError, ffmpeg_path
from .models import Brief, PLATFORMS, RenderOptions, get_platform
from .pipeline import AdRequest, ProviderChoice, generate_ad
from .providers import IMAGE, SCRIPT, TTS, VIDEO, ProviderError, list_providers

BAR_WIDTH = 34


def _progress(message: str, fraction: float) -> None:
    filled = int(BAR_WIDTH * max(0.0, min(1.0, fraction)))
    bar = "#" * filled + "." * (BAR_WIDTH - filled)
    sys.stderr.write(f"\r  [{bar}] {fraction * 100:5.1f}%  {message[:44]:<44}")
    sys.stderr.flush()
    if fraction >= 1.0:
        sys.stderr.write("\n")


def cmd_providers(args: argparse.Namespace) -> int:
    kinds = [args.kind] if args.kind else [SCRIPT, IMAGE, TTS, VIDEO]
    for kind in kinds:
        print(f"\n{kind.upper()} providers")
        print("-" * 78)
        for info in list_providers(kind):
            badge = {"free": "FREE  ", "freemium": "FREEMIUM", "paid": "PAID  "}.get(
                info.tier, "      "
            )
            key = f"needs {info.env_var}" if info.requires_key else "no key needed"
            print(f"  {info.name:<14} [{badge}] {info.label}")
            print(f"  {'':<14} {info.cost}")
            print(f"  {'':<14} {key}" + (f" — {info.signup_url}" if info.signup_url else ""))
            if info.notes:
                print(f"  {'':<14} {info.notes}")
            if info.models:
                print(f"  {'':<14} models: {', '.join(info.models[:4])}")
            print()
    print("\nPRESETS")
    print("-" * 78)
    for name, preset in PRESETS.items():
        print(f"  {name:<12} {preset['label']}")
        print(f"  {'':<12} {preset['description']}")
        print()
    return 0


def cmd_make(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file)

    choices = preset_choices(args.preset)
    for stage in (SCRIPT, IMAGE, TTS, VIDEO):
        override = getattr(args, stage, None)
        if override:
            choices[stage] = override

    brief = Brief(
        product=args.product,
        description=args.description or "",
        audience=args.audience or "",
        tone=args.tone,
        call_to_action=args.cta or "",
        key_points=list(args.point or []),
        visual_style=args.style,
        language=args.language,
        scene_count=args.scenes,
    )
    platform = get_platform(args.platform)
    options = RenderOptions(
        platform=platform,
        fps=args.fps,
        transition=args.transition,
        captions=not args.no_captions,
        caption_font=args.font,
        caption_size=args.caption_size,
        caption_accent=args.accent,
        music_path=args.music,
        music_volume=args.music_volume,
        crf=args.crf,
    )

    def choice(kind: str, key_arg: str | None = None) -> ProviderChoice | None:
        name = choices.get(kind)
        if not name:
            return None
        return ProviderChoice(
            name=name,
            api_key=resolve_key(kind, name, key_arg),
            model=getattr(args, f"{kind}_model", None),
            voice=args.voice if kind == TTS else None,
        )

    request = AdRequest(
        brief=brief,
        platform=platform,
        options=options,
        script=choice(SCRIPT, args.script_key),
        image=choice(IMAGE, args.image_key),
        tts=choice(TTS, args.tts_key),
        video=choice(VIDEO, args.video_key),
        output_path=args.out,
        keep_workdir=args.keep_workdir,
    )

    print(f"admaker {__version__}")
    print(f"  product   {brief.product}")
    print(f"  platform  {platform.label} ({platform.width}x{platform.height})")
    print(
        f"  stack     script={choices.get(SCRIPT)} image={choices.get(IMAGE)} "
        f"tts={choices.get(TTS)}"
        + (f" video={choices[VIDEO]}" if choices.get(VIDEO) else "")
    )
    print()

    try:
        result = generate_ad(request, progress=_progress)
    except (ProviderError, FFmpegError) as exc:
        sys.stderr.write(f"\n\nFailed: {exc}\n")
        return 1

    print(f"\nWrote {result.video_path}  ({result.duration:.1f}s)")
    if result.storyboard.hashtags:
        print("Hashtags: " + " ".join("#" + h for h in result.storyboard.hashtags))
    for warning in result.warnings:
        print(f"  ! {warning}")
    if args.json:
        Path(args.json).write_text(
            json.dumps(result.to_dict(), indent=2), encoding="utf-8"
        )
        print(f"Wrote {args.json}")
    return 0


def cmd_serve(args: argparse.Namespace) -> int:
    load_dotenv(args.env_file)
    try:
        import uvicorn
    except ImportError:
        sys.stderr.write("The web app needs: pip install 'fastapi' 'uvicorn[standard]'\n")
        return 1
    from .server import app

    print(f"admaker web UI:  http://{args.host}:{args.port}")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    ok = True
    try:
        print(f"ffmpeg     OK   {ffmpeg_path()}")
    except FFmpegError as exc:
        print(f"ffmpeg     FAIL {exc}")
        ok = False
    for module, why in [
        ("PIL", "image fallbacks"),
        ("requests", "all HTTP providers"),
        ("edge_tts", "free voiceover"),
        ("fastapi", "web UI"),
        ("anthropic", "Claude scripting"),
    ]:
        try:
            __import__(module)
            print(f"{module:<10} OK   ({why})")
        except ImportError:
            print(f"{module:<10} --   missing, needed for {why}")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="admaker",
        description="Generate AI short-form product ads for TikTok, Reels and Shorts.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    make = sub.add_parser("make", help="Generate an ad")
    make.add_argument("--product", required=True, help="What you are advertising")
    make.add_argument("--description", help="What the product actually is")
    make.add_argument("--audience", help="Who it is for")
    make.add_argument("--point", action="append", help="A selling point (repeatable)")
    make.add_argument("--cta", help="Call to action")
    make.add_argument("--tone", default="energetic")
    make.add_argument(
        "--style",
        default="cinematic product photography, soft studio light, shallow depth of field",
        help="Visual direction handed to the image model",
    )
    make.add_argument("--language", default="English")
    make.add_argument("--scenes", type=int, default=5)
    make.add_argument(
        "--platform", default="universal", choices=sorted(PLATFORMS), help="Target app"
    )
    make.add_argument(
        "--preset", default="free", choices=sorted(PRESETS), help="Provider stack"
    )
    for stage in (SCRIPT, IMAGE, TTS, VIDEO):
        make.add_argument(f"--{stage}", help=f"Override the {stage} provider")
        make.add_argument(f"--{stage}-model", help=f"Model for the {stage} provider")
        make.add_argument(f"--{stage}-key", help=f"API key for the {stage} provider")
    make.add_argument("--voice", help="Voice id/name for the TTS provider")
    make.add_argument("--music", help="Path to a background music file")
    make.add_argument("--music-volume", type=float, default=0.12)
    make.add_argument("--font", help="Caption font family or path to a .ttf")
    make.add_argument("--caption-size", type=int, default=84)
    make.add_argument("--accent", default="#FFE44D", help="Caption highlight colour")
    make.add_argument(
        "--transition",
        default="fade",
        choices=["fade", "slideleft", "slideup", "wipeleft", "circleopen", "none"],
    )
    make.add_argument("--fps", type=int, default=30)
    make.add_argument("--crf", type=int, default=20, help="Lower is higher quality")
    make.add_argument("--no-captions", action="store_true")
    make.add_argument("--out", help="Output .mp4 path")
    make.add_argument("--json", help="Also write the storyboard/result JSON here")
    make.add_argument("--keep-workdir", action="store_true")
    make.add_argument("--env-file", default=".env")
    make.set_defaults(func=cmd_make)

    providers = sub.add_parser("providers", help="List providers, costs and signup links")
    providers.add_argument("--kind", choices=[SCRIPT, IMAGE, TTS, VIDEO])
    providers.set_defaults(func=cmd_providers)

    serve = sub.add_parser("serve", help="Run the web app")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--env-file", default=".env")
    serve.set_defaults(func=cmd_serve)

    doctor = sub.add_parser("doctor", help="Check the install")
    doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
