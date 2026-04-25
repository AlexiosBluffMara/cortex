"""Frame extraction for Gemma vision calls.

The actual Ollama call for video description lives in `media_gate.py`,
since it shares the same JSON-mode classify call. This module is kept
only for the ffmpeg keyframe helper.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Suppress console window popup when spawning subprocesses on Windows.
_NOWWIN = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

from . import config


def _require_ffmpeg() -> str:
    ff = shutil.which("ffmpeg")
    if not ff:
        sys.exit("ffmpeg not found on PATH.")
    return ff


def _probe_duration(video_path: Path) -> float:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return 20.0
    try:
        out = subprocess.check_output(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", str(video_path)],
            text=True,
            creationflags=_NOWWIN,
        ).strip()
        return float(out)
    except (subprocess.CalledProcessError, ValueError):
        return 20.0


def extract_keyframes(video_path: Path, n: int = 4, out_dir: Path | None = None) -> list[Path]:
    out_dir = out_dir or config.OUT_DIR / "frames"
    out_dir.mkdir(exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()

    ffmpeg = _require_ffmpeg()
    duration = _probe_duration(video_path)
    step = duration / (n + 1)
    frames: list[Path] = []
    for i in range(n):
        t = step * (i + 1)
        out = out_dir / f"frame_{i:02d}.jpg"
        result = subprocess.run(
            [
                ffmpeg, "-y",
                "-ss", f"{t:.2f}",
                "-i", str(video_path),
                "-frames:v", "1",
                "-q:v", "3",
                "-vf", "scale=512:trunc(ow/a/2)*2",
                str(out),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            creationflags=_NOWWIN,
        )
        if result.returncode != 0 or not out.exists():
            print(f"[ffmpeg] frame {i} failed (rc={result.returncode}):\n"
                  + result.stderr.decode(errors="replace")[-400:])
            continue
        frames.append(out)
    return frames
