"""
Media preprocessing for TRIBE v2 and Gemma 4 multimodal input.

All processing uses FFmpeg via subprocess for maximum control and
zero Python-library dependencies (no moviepy, no cv2).

Pipeline:
  Input → probe() → preprocess_for_tribe() → {video, audio, keyframes}
                                                  ↓
                                            TRIBE v2 inference
                                                  ↓
                                            Gemma narration

Output formats:
  - TRIBE video:  224×224, H.265 CRF 6, 30fps, ≤50s (near-lossless)
  - TRIBE audio:  16kHz mono 16-bit PCM WAV (wav2vec-BERT 2.0 native)
  - Gemma frames: 768px keyframes at 1fps for vision gate classification
  - Web video:    720p H.264 CRF 23 for gallery browser playback

Why these settings:
  - 224×224: V-JEPA2 ViT-g native resolution (2×16×16 patches, divisible by 16)
  - CRF 6:  Near-lossless (0=lossless, 18=visually lossless, 23=web quality)
  - H.265:  30-40% smaller than H.264 at identical quality
  - 16kHz:  wav2vec-BERT 2.0 expects 16kHz mono; higher rates waste compute
  - 768px:  Gemma 4 vision encoder handles up to 896px; 768 is a good balance
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from cortex.logger import log


@dataclass
class MediaInfo:
    """Metadata extracted from a media file via FFprobe."""
    duration_s: float
    width: int
    height: int
    fps: float
    has_audio: bool
    codec: str
    file_size_mb: float
    audio_codec: str = ""
    audio_sample_rate: int = 0


class MediaProcessingError(Exception):
    """Raised when FFmpeg/FFprobe fails."""


def _run_ffmpeg(cmd: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Run an FFmpeg command with error handling."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=True,
        )
        return result
    except subprocess.CalledProcessError as exc:
        log.error("[media] FFmpeg failed: %s\nstderr: %s", " ".join(cmd), exc.stderr)
        raise MediaProcessingError(f"FFmpeg error: {exc.stderr[:500]}") from exc
    except subprocess.TimeoutExpired:
        raise MediaProcessingError(f"FFmpeg timeout after {timeout}s")
    except FileNotFoundError:
        raise MediaProcessingError(
            "FFmpeg not found. Install via: winget install FFmpeg "
            "or download from https://ffmpeg.org/download.html"
        )


def probe(path: Path) -> MediaInfo:
    """
    Extract media metadata with FFprobe.

    Returns MediaInfo with duration, resolution, fps, codec, and audio info.
    Raises MediaProcessingError if the file can't be probed.
    """
    if not path.exists():
        raise FileNotFoundError(f"Media file not found: {path}")

    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format", "-show_streams",
        str(path),
    ]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        data = json.loads(result.stdout)
    except (subprocess.TimeoutExpired, json.JSONDecodeError) as exc:
        raise MediaProcessingError(f"FFprobe failed for {path}: {exc}") from exc

    if "format" not in data:
        raise MediaProcessingError(f"FFprobe returned no format data for {path}")

    video_stream = next(
        (s for s in data.get("streams", []) if s["codec_type"] == "video"), None
    )
    audio_stream = next(
        (s for s in data.get("streams", []) if s["codec_type"] == "audio"), None
    )

    # Parse fps safely (r_frame_rate is a fraction like "30000/1001")
    fps = 0.0
    if video_stream and "r_frame_rate" in video_stream:
        try:
            num, den = video_stream["r_frame_rate"].split("/")
            fps = float(num) / float(den)
        except (ValueError, ZeroDivisionError):
            fps = 30.0

    return MediaInfo(
        duration_s=float(data["format"].get("duration", 0)),
        width=int(video_stream["width"]) if video_stream else 0,
        height=int(video_stream["height"]) if video_stream else 0,
        fps=fps,
        has_audio=audio_stream is not None,
        codec=video_stream["codec_name"] if video_stream else "",
        file_size_mb=float(data["format"].get("size", 0)) / (1024 * 1024),
        audio_codec=audio_stream["codec_name"] if audio_stream else "",
        audio_sample_rate=int(audio_stream.get("sample_rate", 0)) if audio_stream else 0,
    )


def preprocess_for_tribe(
    input_path: Path,
    output_dir: Path,
    max_duration_s: float = 50.0,
    target_resolution: int = 224,
) -> dict:
    """
    Preprocess media for TRIBE v2 inference.

    Produces:
      - video: 224×224, H.265 CRF 6, 30fps, ≤50s
      - audio: 16kHz mono WAV (if input has audio)
      - keyframes: 768px JPEGs at 1fps (for Gemma vision gate)

    Near-lossless: CRF 6 preserves >99% visual quality for V-JEPA2.
    Audio extracted separately at wav2vec-BERT 2.0 native sample rate.

    Args:
        input_path: Path to source video/audio file
        output_dir: Directory for processed outputs
        max_duration_s: Maximum duration (TRIBE v2 hard limit: 50s)
        target_resolution: Target video resolution (must be divisible by 16)

    Returns:
        Dict with paths to processed files and metadata.

    Raises:
        MediaProcessingError: If FFmpeg fails
        FileNotFoundError: If input file doesn't exist
    """
    input_path = Path(input_path)
    output_dir = Path(output_dir)

    info = probe(input_path)
    stem = input_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    video_out = output_dir / f"{stem}_tribe.mp4"
    audio_out = output_dir / f"{stem}_tribe.wav"
    keyframes_dir = output_dir / f"{stem}_keyframes"

    # Trim arguments: take the LAST max_duration_s seconds (most interesting)
    trim_args = []
    if info.duration_s > max_duration_s:
        start = info.duration_s - max_duration_s
        trim_args = ["-ss", f"{start:.3f}", "-to", f"{info.duration_s:.3f}"]

    # -- Video: scale to 224×224, H.265 CRF 6 --
    if info.width > 0:  # has video stream
        video_cmd = [
            "ffmpeg", "-y",
            *trim_args,
            "-i", str(input_path),
            "-vf", (
                f"fps=30,"
                f"scale={target_resolution}:{target_resolution}:"
                f"force_original_aspect_ratio=decrease,"
                f"pad={target_resolution}:{target_resolution}:"
                f"(ow-iw)/2:(oh-ih)/2:black"
            ),
            "-c:v", "libx265",
            "-crf", "6",
            "-preset", "fast",
            "-tag:v", "hvc1",  # Apple/browser compatibility
            "-an",
            "-movflags", "+faststart",
            str(video_out),
        ]
        _run_ffmpeg(video_cmd, timeout=300)
        log.info("[media] Video processed: %s → %s (%.1fMB)",
                 input_path.name, video_out.name,
                 video_out.stat().st_size / (1024 * 1024))
    else:
        video_out = None

    # -- Audio: 16kHz mono WAV for wav2vec-BERT 2.0 --
    if info.has_audio:
        audio_cmd = [
            "ffmpeg", "-y",
            *trim_args,
            "-i", str(input_path),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            "-c:a", "pcm_s16le",
            str(audio_out),
        ]
        _run_ffmpeg(audio_cmd, timeout=120)
        log.info("[media] Audio extracted: %s (16kHz mono WAV)", audio_out.name)
    else:
        audio_out = None

    # -- Keyframes: 768px JPEGs at 1fps for Gemma vision gate --
    if info.width > 0:
        keyframes_dir.mkdir(exist_ok=True)
        keyframe_cmd = [
            "ffmpeg", "-y",
            *trim_args,
            "-i", str(input_path),
            "-vf", "fps=1,scale=768:768:force_original_aspect_ratio=decrease",
            "-q:v", "2",
            str(keyframes_dir / "frame_%04d.jpg"),
        ]
        _run_ffmpeg(keyframe_cmd, timeout=120)
        n_frames = len(list(keyframes_dir.glob("frame_*.jpg")))
        log.info("[media] Extracted %d keyframes for vision gate", n_frames)
    else:
        keyframes_dir = None

    # -- Verify audio-video sync --
    drift = 0.0
    if video_out and audio_out:
        try:
            video_info = probe(video_out)
            audio_probe = subprocess.run(
                ["ffprobe", "-v", "quiet", "-print_format", "json",
                 "-show_format", str(audio_out)],
                capture_output=True, text=True, timeout=10,
            )
            audio_data = json.loads(audio_probe.stdout)
            audio_dur = float(audio_data["format"]["duration"])
            drift = abs(audio_dur - video_info.duration_s)
            if drift > 0.1:
                log.warning("[media] A/V drift of %.3fs detected", drift)
        except Exception:
            pass

    processed_duration = min(info.duration_s, max_duration_s)
    result = {
        "video": str(video_out) if video_out else None,
        "audio": str(audio_out) if audio_out else None,
        "keyframes_dir": str(keyframes_dir) if keyframes_dir else None,
        "original_duration_s": info.duration_s,
        "processed_duration_s": processed_duration,
        "original_resolution": f"{info.width}x{info.height}",
        "processed_resolution": f"{target_resolution}x{target_resolution}",
        "original_size_mb": round(info.file_size_mb, 2),
        "processed_size_mb": round(
            video_out.stat().st_size / (1024 * 1024), 2
        ) if video_out else 0.0,
        "av_drift_s": round(drift, 4),
        "n_keyframes": len(list(keyframes_dir.glob("frame_*.jpg"))) if keyframes_dir else 0,
    }

    log.info("[media] Preprocessing complete: %s", json.dumps(result, indent=2))
    return result


def preprocess_for_web(
    input_path: Path,
    output_dir: Path,
    max_duration_s: float = 50.0,
) -> Path:
    """
    Create a web-optimized version for the gallery viewer.
    720p H.264 for maximum browser compatibility.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{Path(input_path).stem}_web.mp4"

    cmd = [
        "ffmpeg", "-y",
        "-i", str(input_path),
        "-t", str(max_duration_s),
        "-vf", "scale=-2:720",
        "-c:v", "libx264",
        "-crf", "23",
        "-preset", "medium",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(output),
    ]
    _run_ffmpeg(cmd, timeout=300)
    log.info("[media] Web version: %s (%.1fMB)",
             output.name, output.stat().st_size / (1024 * 1024))
    return output


def extract_thumbnail(input_path: Path, output_dir: Path, time_s: float = 1.0) -> Path:
    """Extract a single thumbnail frame for gallery cards."""
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"{Path(input_path).stem}_thumb.jpg"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(time_s),
        "-i", str(input_path),
        "-vframes", "1",
        "-vf", "scale=480:-2",
        "-q:v", "3",
        str(output),
    ]
    _run_ffmpeg(cmd, timeout=30)
    return output
