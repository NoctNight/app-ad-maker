"""FastAPI app: paste an API key, describe a product, get an ad.

API keys arrive per-request, live in memory for the life of the job, and are
never written to disk or logged. Jobs are held in memory too, so this is a
single-machine tool — run it locally, not as a public service.
"""

from __future__ import annotations

import threading
import time
import traceback
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from . import __version__
from .config import PRESETS, resolve_key
from .models import Brief, PLATFORMS, RenderOptions, get_platform
from .pipeline import AdRequest, ProviderChoice, generate_ad
from .providers import IMAGE, SCRIPT, TTS, VIDEO, list_providers

WEB_DIR = Path(__file__).resolve().parent.parent / "web"
OUTPUT_DIR = Path.cwd() / "output"

app = FastAPI(title="admaker", version=__version__)


# --------------------------------------------------------------------------
# Job store
# --------------------------------------------------------------------------


@dataclass
class Job:
    id: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    message: str = "Queued"
    created_at: float = field(default_factory=time.time)
    video_path: str | None = None
    error: str | None = None
    result: dict[str, Any] | None = None

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 4),
            "message": self.message,
            "error": self.error,
            "result": self.result,
            "video_url": f"/api/jobs/{self.id}/video" if self.video_path else None,
        }


JOBS: dict[str, Job] = {}
JOBS_LOCK = threading.Lock()


# --------------------------------------------------------------------------
# Request schema
# --------------------------------------------------------------------------


class StageChoice(BaseModel):
    provider: str | None = None
    api_key: str | None = None
    model: str | None = None
    voice: str | None = None


class GenerateRequest(BaseModel):
    product: str = Field(min_length=1)
    description: str = ""
    audience: str = ""
    key_points: list[str] = []
    call_to_action: str = ""
    tone: str = "energetic"
    visual_style: str = (
        "cinematic product photography, soft studio light, shallow depth of field"
    )
    language: str = "English"
    scene_count: int = Field(default=5, ge=2, le=8)
    platform: str = "universal"
    preset: str = "free"
    script: StageChoice = StageChoice()
    image: StageChoice = StageChoice()
    tts: StageChoice = StageChoice()
    video: StageChoice = StageChoice()
    captions: bool = True
    caption_accent: str = "#FFE44D"
    caption_size: int = Field(default=84, ge=40, le=140)
    transition: str = "fade"
    music_path: str | None = None


# --------------------------------------------------------------------------
# Routes
# --------------------------------------------------------------------------


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    page = WEB_DIR / "index.html"
    if not page.exists():
        raise HTTPException(500, f"UI not found at {page}")
    return page.read_text(encoding="utf-8")


@app.get("/api/meta")
def meta() -> dict[str, Any]:
    """Everything the UI needs to render its form."""
    return {
        "version": __version__,
        "platforms": [
            {
                "key": p.key,
                "label": p.label,
                "max_seconds": p.max_seconds,
                "ideal_seconds": p.ideal_seconds,
            }
            for p in _unique_platforms()
        ],
        "presets": [
            {"key": k, **v} for k, v in PRESETS.items()
        ],
        "providers": {
            kind: [i.to_dict() for i in list_providers(kind)]
            for kind in (SCRIPT, IMAGE, TTS, VIDEO)
        },
        "env_keys": {
            info.env_var: bool(resolve_key(info.kind, info.name))
            for kind in (SCRIPT, IMAGE, TTS, VIDEO)
            for info in list_providers(kind)
            if info.env_var
        },
    }


def _unique_platforms() -> list:
    seen: set[str] = set()
    out = []
    for key, platform in PLATFORMS.items():
        if key in seen:
            continue
        seen.add(key)
        out.append(platform)
    return out


@app.post("/api/generate")
def start(request: GenerateRequest) -> dict[str, Any]:
    try:
        ad_request = _build_ad_request(request)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    job = Job(id=uuid.uuid4().hex[:12])
    with JOBS_LOCK:
        JOBS[job.id] = job

    thread = threading.Thread(target=_run_job, args=(job, ad_request), daemon=True)
    thread.start()
    return job.public()


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict[str, Any]:
    job = JOBS.get(job_id)
    if not job:
        raise HTTPException(404, "No such job")
    return job.public()


@app.get("/api/jobs/{job_id}/video")
def job_video(job_id: str) -> FileResponse:
    job = JOBS.get(job_id)
    if not job or not job.video_path or not Path(job.video_path).exists():
        raise HTTPException(404, "Video not ready")
    return FileResponse(
        job.video_path, media_type="video/mp4", filename=Path(job.video_path).name
    )


# --------------------------------------------------------------------------
# Wiring
# --------------------------------------------------------------------------


def _build_ad_request(request: GenerateRequest) -> AdRequest:
    preset = PRESETS.get(request.preset)
    if preset is None:
        raise ValueError(f"Unknown preset {request.preset!r}")

    platform = get_platform(request.platform)
    brief = Brief(
        product=request.product,
        description=request.description,
        audience=request.audience,
        tone=request.tone,
        call_to_action=request.call_to_action,
        key_points=[p for p in request.key_points if p.strip()],
        visual_style=request.visual_style,
        language=request.language,
        scene_count=request.scene_count,
    )
    options = RenderOptions(
        platform=platform,
        captions=request.captions,
        caption_accent=request.caption_accent,
        caption_size=request.caption_size,
        transition=request.transition,
        music_path=request.music_path,
    )

    def stage(kind: str, choice: StageChoice) -> ProviderChoice | None:
        name = choice.provider or preset.get(kind)
        if not name:
            return None
        return ProviderChoice(
            name=name,
            # A pasted key wins; otherwise fall back to the server's env.
            api_key=resolve_key(kind, name, choice.api_key),
            model=choice.model or None,
            voice=choice.voice or None,
        )

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    return AdRequest(
        brief=brief,
        platform=platform,
        options=options,
        script=stage(SCRIPT, request.script),
        image=stage(IMAGE, request.image),
        tts=stage(TTS, request.tts),
        video=stage(VIDEO, request.video),
        workdir=str(OUTPUT_DIR / f"job_{int(time.time())}"),
    )


def _run_job(job: Job, ad_request: AdRequest) -> None:
    def report(message: str, fraction: float) -> None:
        job.message = message
        job.progress = fraction

    job.status = "running"
    try:
        result = generate_ad(ad_request, progress=report)
        job.video_path = result.video_path
        job.result = result.to_dict()
        job.status = "done"
        job.progress = 1.0
        job.message = "Done"
    except Exception as exc:  # noqa: BLE001 - surface any failure to the UI
        job.status = "error"
        job.error = str(exc)
        job.message = "Failed"
        traceback.print_exc()
