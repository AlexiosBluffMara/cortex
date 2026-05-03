"""Frame extraction and audio transcription for Gemma multimodal calls.

The actual Ollama call for video description lives in `media_gate.py`,
since it shares the same JSON-mode classify call. This module provides:
  - extract_keyframes()      — ffmpeg JPEG keyframe helper
  - extract_audio_segment()  — ffmpeg 16 kHz mono WAV helper
  - transcribe_audio()       — local Whisper-tiny ASR (openai/whisper-tiny via transformers)

Note: Ollama does not expose native audio input for Gemma 4 yet. Audio
understanding is achieved by transcribing locally and injecting the
transcript into Gemma's text prompt alongside the visual keyframes.
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


def extract_audio_segment(
    media_path: Path,
    max_secs: float = 30.0,
    out_dir: Path | None = None,
) -> Path | None:
    """Extract up to max_secs of audio as 16 kHz mono WAV. Returns None on failure."""
    out_dir = out_dir or config.OUT_DIR / "audio"
    out_dir.mkdir(exist_ok=True)
    out_wav = out_dir / f"audio_{media_path.stem}.wav"
    ffmpeg = _require_ffmpeg()
    result = subprocess.run(
        [ffmpeg, "-y", "-i", str(media_path),
         "-t", str(max_secs),
         "-ar", "16000", "-ac", "1",
         "-f", "wav", str(out_wav)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_NOWWIN,
    )
    if result.returncode != 0 or not out_wav.exists() or out_wav.stat().st_size < 1000:
        return None
    return out_wav


_whisper_pipe = None


def transcribe_audio(wav_path: Path, max_chars: int = 600) -> str:
    """Transcribe a WAV file with whisper-tiny. Returns transcript or empty string on failure."""
    global _whisper_pipe
    try:
        if _whisper_pipe is None:
            from transformers import pipeline as hf_pipeline
            from cortex import device as _device
            # HF pipeline accepts torch.device or "cuda"/"mps"/"cpu" string.
            # Use the abstracted device — works for cuda (RTX 5090), mps (M-series), or cpu.
            _whisper_pipe = hf_pipeline(
                "automatic-speech-recognition",
                model="openai/whisper-tiny",
                device=_device.DEVICE,
            )
        result = _whisper_pipe(str(wav_path), chunk_length_s=30)
        text = (result.get("text") or "").strip()
        return text[:max_chars] if len(text) > max_chars else text
    except Exception as exc:
        print(f"[gemma_vision] Whisper transcription failed: {exc}")
        return ""
