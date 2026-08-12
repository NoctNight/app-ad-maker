"""Locating and driving ffmpeg.

We deliberately avoid depending on a system ffmpeg install: `imageio-ffmpeg`
ships a static build, so `pip install` is the only setup step a user needs.
ffprobe is not part of that wheel, so durations are read back out of ffmpeg
itself.
"""

from __future__ import annotations

import functools
import os
import re
import shutil
import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    """An ffmpeg invocation failed. Carries the tail of stderr."""

    def __init__(self, message: str, stderr: str = "", cmd: list[str] | None = None):
        self.stderr = stderr
        self.cmd = cmd or []
        tail = "\n".join(stderr.strip().splitlines()[-15:])
        super().__init__(f"{message}\n{tail}" if tail else message)


@functools.lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    """Return a usable ffmpeg binary, preferring the bundled static build."""
    override = os.environ.get("ADMAKER_FFMPEG")
    if override:
        if not Path(override).exists():
            raise FFmpegError(f"ADMAKER_FFMPEG points at a missing file: {override}")
        return override
    try:
        import imageio_ffmpeg

        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:  # noqa: BLE001 - fall through to a system binary
        pass
    system = shutil.which("ffmpeg")
    if system:
        return system
    raise FFmpegError(
        "No ffmpeg found. Install it with `pip install imageio-ffmpeg`, "
        "or set ADMAKER_FFMPEG to an ffmpeg binary."
    )


def run(args: list[str], *, desc: str = "ffmpeg step", timeout: int = 900) -> str:
    """Run ffmpeg with the given args (binary and -y are supplied for you)."""
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-loglevel", "error", "-y", *args]
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as exc:
        raise FFmpegError(f"{desc} timed out after {timeout}s", cmd=cmd) from exc
    if proc.returncode != 0:
        raise FFmpegError(f"{desc} failed (exit {proc.returncode})", proc.stderr, cmd)
    return proc.stderr


_DURATION_RE = re.compile(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)")
_TIME_RE = re.compile(r"time=\s*(\d+):(\d+):(\d+(?:\.\d+)?)")


def probe_duration(path: str | Path) -> float:
    """Return a media file's duration in seconds.

    Reads the header first; for containers that omit it (raw mp3 streams,
    some TTS output) we decode to null and take the last reported timestamp,
    which is exact.
    """
    path = str(path)
    if not Path(path).exists():
        raise FFmpegError(f"Cannot probe missing file: {path}")
    cmd = [ffmpeg_path(), "-hide_banner", "-nostdin", "-i", path, "-f", "null", "-"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300, check=False)
    err = proc.stderr

    best = 0.0
    for match in _TIME_RE.finditer(err):
        h, m, s = match.groups()
        best = max(best, int(h) * 3600 + int(m) * 60 + float(s))
    if best > 0:
        return best

    header = _DURATION_RE.search(err)
    if header:
        h, m, s = header.groups()
        return int(h) * 3600 + int(m) * 60 + float(s)
    raise FFmpegError(f"Could not determine duration of {path}", err, cmd)


def escape_filter_path(path: str | Path) -> str:
    """Escape a path for use *inside* an ffmpeg filtergraph argument.

    Filtergraph parsing eats backslashes, colons, commas and quotes, so a
    Windows path or a path with a colon silently corrupts the graph.
    """
    text = str(path).replace("\\", "/")
    for ch in (":", "'", "[", "]", ",", ";"):
        text = text.replace(ch, "\\" + ch)
    return text


def has_filter(name: str) -> bool:
    """Check whether this ffmpeg build exposes a given filter."""
    proc = subprocess.run(
        [ffmpeg_path(), "-hide_banner", "-filters"],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    return bool(re.search(rf"^\s*\S+\s+{re.escape(name)}\s", proc.stdout, re.MULTILINE))
