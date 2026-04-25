"""
Hermes Agent tool: Describe Input

Use Gemma 4 multimodal vision to classify and describe a video's content
before sending it through TRIBE v2. This is the "vision gate" — it tells
the narration system what kind of content the brain is responding to.

Runs entirely on Gemma E4B (always warm). Fast path: ~2-5 seconds for
10 keyframes.
"""
from __future__ import annotations

from pathlib import Path

TOOL_SCHEMA = {
    "name": "describe_input",
    "description": (
        "Describe the visual content of a video using Gemma 4 multimodal vision. "
        "Extracts keyframes and classifies scene type, subjects, mood, and dominant "
        "sensory modality. Use before brain_scan to understand what content the "
        "brain will be responding to."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "media_path": {
                "type": "string",
                "description": "Path to video file (.mp4, .mkv, .webm, .mov)",
            },
            "max_frames": {
                "type": "integer",
                "description": "Maximum keyframes to analyze (1 per second)",
                "default": 10,
                "minimum": 1,
                "maximum": 30,
            },
        },
        "required": ["media_path"],
    },
}


async def execute(media_path: str, max_frames: int = 10) -> dict:
    """Classify video content using Gemma 4 vision."""
    import subprocess

    from cortex.gpu_scheduler import get_scheduler
    from cortex.logger import log
    from cortex.media_processor import MediaProcessingError, probe

    path = Path(media_path)
    if not path.exists():
        return {"ok": False, "error": "file_not_found", "message": f"File not found: {path}"}

    # Ensure Gemma is loaded
    scheduler = get_scheduler()
    await scheduler.ensure_gemma()

    # Extract keyframes
    try:
        info = probe(path)
        keyframes_dir = path.parent / f"{path.stem}_describe_frames"
        keyframes_dir.mkdir(exist_ok=True)

        cmd = [
            "ffmpeg", "-y",
            "-i", str(path),
            "-vf", "fps=1,scale=768:768:force_original_aspect_ratio=decrease",
            "-q:v", "2",
            "-frames:v", str(max_frames),
            str(keyframes_dir / "frame_%04d.jpg"),
        ]
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
        keyframes = sorted(keyframes_dir.glob("frame_*.jpg"))
    except (subprocess.CalledProcessError, MediaProcessingError) as exc:
        return {"ok": False, "error": "keyframe_extraction_failed", "message": str(exc)}

    if not keyframes:
        return {"ok": False, "error": "no_frames", "message": "No keyframes extracted"}

    # Run Gemma vision gate
    try:
        from cortex.gemma_vision import vision_gate
        result = await vision_gate(keyframes[:max_frames], scheduler._ollama_url)

        return {
            "ok": True,
            "duration_s": info.duration_s,
            "frames_analyzed": result["frames_analyzed"],
            "analyses": result["analyses"],
            "has_audio": info.has_audio,
            "resolution": f"{info.width}x{info.height}",
        }
    except Exception as exc:
        log.error("[describe_input] Vision gate failed: %s", exc)
        return {"ok": False, "error": "vision_failed", "message": str(exc)}
