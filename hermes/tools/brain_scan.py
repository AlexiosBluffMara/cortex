"""
Hermes Agent tool: Brain Scan

Orchestrates the full TRIBE v2 pipeline:
  1. Validate and preprocess media (FFmpeg → 224×224 H.265 + 16kHz WAV)
  2. Swap GPU to TRIBE v2 (unload Gemma, load TRIBE)
  3. Run inference (outputs 20,484-vertex BOLD predictions)
  4. Swap GPU back to Gemma
  5. Extract top ROIs from Schaefer-400 atlas
  6. Optionally generate multi-tier narration via Gemma

This is the primary user-facing tool. WhatsApp users send a video,
this tool processes it end-to-end and returns results.
"""
from __future__ import annotations

import json
from pathlib import Path

TOOL_SCHEMA = {
    "name": "brain_scan",
    "description": (
        "Analyze a video or audio file using TRIBE v2 brain foundation model. "
        "Predicts which cortical regions activate in response to the stimulus. "
        "Returns top active brain regions, peak activation time, and optional "
        "narration at any expertise level from toddler to researcher."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "media_path": {
                "type": "string",
                "description": "Path to video (.mp4, .mkv, .webm, .mov, .avi) or audio (.wav, .mp3, .flac) file",
            },
            "include_narration": {
                "type": "boolean",
                "description": "Whether to generate text narrations of the brain response",
                "default": True,
            },
            "narration_tier": {
                "type": "integer",
                "description": (
                    "Narration complexity: 0=toddler, 1=grandparent, 2=general public, "
                    "3=high-school, 4=college, 5=clinician, 6=researcher"
                ),
                "default": 2,
                "minimum": 0,
                "maximum": 6,
            },
            "extract_keyframes": {
                "type": "boolean",
                "description": "Whether to extract keyframes for Gemma vision gate",
                "default": True,
            },
        },
        "required": ["media_path"],
    },
}


async def execute(
    media_path: str,
    include_narration: bool = True,
    narration_tier: int = 2,
    extract_keyframes: bool = True,
) -> dict:
    """Execute a brain scan with full GPU lifecycle management."""
    from cortex.gpu_scheduler import get_scheduler
    from cortex.logger import log
    from cortex.media_processor import MediaProcessingError, preprocess_for_tribe, probe

    path = Path(media_path)

    # Validate input
    if not path.exists():
        return {"ok": False, "error": "file_not_found", "message": f"File not found: {path}"}

    valid_extensions = {".mp4", ".mkv", ".webm", ".mov", ".avi", ".wav", ".mp3", ".flac", ".ogg", ".m4a", ".txt"}
    if path.suffix.lower() not in valid_extensions:
        return {
            "ok": False,
            "error": "unsupported_format",
            "message": f"Unsupported format: {path.suffix}. Supported: {', '.join(sorted(valid_extensions))}",
        }

    # Step 1: Probe and preprocess
    try:
        info = probe(path)
        if info.duration_s > 300:
            return {
                "ok": False,
                "error": "too_long",
                "message": f"Video is {info.duration_s:.0f}s. Max supported: 300s (last 50s will be used for TRIBE).",
            }

        processed_dir = path.parent / "processed"
        processed = preprocess_for_tribe(path, processed_dir)
        log.info("[brain_scan] Preprocessed: %s", json.dumps(processed, indent=2))
    except MediaProcessingError as exc:
        return {"ok": False, "error": "preprocessing_failed", "message": str(exc)}

    # Step 2: Run TRIBE inference (handles GPU swap automatically)
    scheduler = get_scheduler()
    try:
        # Use the processed video (or original if audio-only)
        inference_path = processed.get("video") or processed.get("audio") or str(path)
        result = await scheduler.run_brain_scan(inference_path)
    except RuntimeError as exc:
        if "out of memory" in str(exc).lower():
            return {
                "ok": False,
                "error": "oom",
                "message": "GPU out of memory during TRIBE inference. Try a shorter clip or restart Ollama.",
            }
        return {"ok": False, "error": "inference_failed", "message": str(exc)}

    # Build output
    output = {
        "ok": True,
        "top_rois": result.top_rois,
        "peak_frame": result.peak_t,
        "peak_time_s": round(result.peak_t / 2.0, 2),
        "num_timepoints": result.preds.shape[0],
        "num_vertices": result.preds.shape[1],
        "duration_s": round(result.preds.shape[0] / 2.0, 2),
        "processing_time_s": round(result.seconds_elapsed, 2),
        "media_info": {
            "original_duration_s": processed["original_duration_s"],
            "processed_duration_s": processed["processed_duration_s"],
            "original_resolution": processed["original_resolution"],
            "n_keyframes": processed.get("n_keyframes", 0),
        },
    }

    # Step 3: Vision gate (optional, runs on Gemma after swap back)
    if extract_keyframes and processed.get("keyframes_dir"):
        try:
            from cortex.gemma_vision import vision_gate
            keyframes_dir = Path(processed["keyframes_dir"])
            keyframes = sorted(keyframes_dir.glob("frame_*.jpg"))[:10]
            if keyframes:
                vision_result = await vision_gate(keyframes, scheduler._ollama_url)
                output["vision_analysis"] = vision_result
        except Exception as exc:
            log.warning("[brain_scan] Vision gate failed: %s", exc)
            output["vision_analysis"] = {"error": str(exc)}

    # Step 4: Narrate (runs on Gemma, already loaded after step 2)
    if include_narration:
        try:
            import asyncio

            from cortex.tiers import narrate_tier

            label = output.get("vision_analysis", {}).get("analyses", ["unknown stimulus"])[0]
            if isinstance(label, dict):
                label = label.get("description", "video stimulus")

            loop = asyncio.get_event_loop()
            narration = await loop.run_in_executor(
                None,
                lambda: narrate_tier(result, label=str(label), tier=narration_tier),
            )
            output["narration"] = narration
            output["narration_tier"] = narration_tier
        except Exception as exc:
            log.warning("[brain_scan] Narration failed: %s", exc)
            output["narration"] = f"(Narration unavailable: {exc})"

    return output
