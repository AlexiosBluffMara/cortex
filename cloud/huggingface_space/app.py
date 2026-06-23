"""Gradio adapter for running TRIBE v2 on Hugging Face ZeroGPU.

ZeroGPU currently requires the Gradio SDK, so this adapter is deliberately
separate from the FastAPI worker in `cloud.tribe_worker`. It reuses the same
fake/real TRIBE execution helpers and emits the same core scan payload shape,
but it exposes a Gradio function named `scan` that can be called through the
Space UI or Gradio client.
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

import numpy as np

from cloud.tribe_worker import app as worker

try:  # Gradio is required on Hugging Face, optional in local unit tests.
    import gradio as gr
except ImportError:  # pragma: no cover - exercised by import-only tests.
    gr = None  # type: ignore[assignment]

try:
    import spaces
except ImportError:  # pragma: no cover - local dev without HF ZeroGPU package.
    class _SpacesShim:
        @staticmethod
        def GPU(*_args: Any, **_kwargs: Any):
            def _decorator(fn):
                return fn
            return _decorator

    spaces = _SpacesShim()  # type: ignore[assignment]


SPACE_ROOT = Path(os.environ.get("CORTEX_SPACE_ROOT", tempfile.gettempdir())) / "cortex-hf-space"
SPACE_UPLOAD_DIR = SPACE_ROOT / "uploads"
SPACE_SCAN_DIR = SPACE_ROOT / "scans"
SPACE_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
SPACE_SCAN_DIR.mkdir(parents=True, exist_ok=True)
ZERO_GPU_DURATION_S = int(os.environ.get("CORTEX_ZEROGPU_DURATION_S", "120"))


def _space_readiness() -> dict[str, Any]:
    readiness = worker.tribe_readiness()
    return {
        "provider": "huggingface-zerogpu-gradio",
        "worker_mode": readiness.get("mode") or worker.WORKER_MODE,
        "contract_ready": bool(readiness.get("contract_ready")),
        "real_mode_ready": bool(readiness.get("real_mode_ready")),
        "real_mode_required": bool(readiness.get("real_mode_required")),
        "readiness_missing": readiness.get("missing") or [],
    }


def _upload_path(upload: Any, scan_id: str) -> Path:
    if upload is None:
        raise ValueError("Upload a media or text file before running TRIBE.")
    source = Path(getattr(upload, "name", upload))
    if not source.exists():
        raise ValueError(f"Uploaded file does not exist: {source}")
    suffix = source.suffix or ".bin"
    target = SPACE_UPLOAD_DIR / f"{scan_id}{suffix}"
    shutil.copyfile(source, target)
    return target


async def _run_space_inference(scan_id: str, media_path: Path) -> tuple[np.ndarray, list[str], int, float]:
    if worker.WORKER_MODE == "real":
        return await worker._run_real(scan_id, media_path)

    started = time.time()
    bold = worker._fake_bold(scan_id)
    rois = worker._top_rois(scan_id)
    peak_t = int(np.argmax(np.abs(bold).mean(axis=1)))
    return bold, rois, peak_t, round(time.time() - started, 2)


@spaces.GPU(duration=ZERO_GPU_DURATION_S)
def run_tribe_scan(
    media_file: Any,
    tier: int = 4,
    narration_model: str = "openrouter/free",
) -> tuple[str, str]:
    """Run one TRIBE scan and return `(scan_json, bold_npy_path)`.

    In `CORTEX_WORKER_MODE=fake`, this produces deterministic fake BOLD for
    smoke tests. In `real`, it delegates to `cortex.pipeline.run_inference`.
    """
    scan_id = uuid.uuid4().hex[:12]
    media_path = _upload_path(media_file, scan_id)
    started = time.time()
    bold, top_rois, peak_t, seconds_elapsed = asyncio.run(_run_space_inference(scan_id, media_path))
    bold = np.ascontiguousarray(bold, dtype=np.float32)
    bold_path = SPACE_SCAN_DIR / f"{scan_id}.npy"
    np.save(bold_path, bold)
    record = {
        "ok": True,
        "id": scan_id,
        "scan_id": scan_id,
        "status": "complete",
        "filename": media_path.name,
        "tier": int(tier),
        "source": "huggingface-zerogpu-gradio",
        "narration_model": narration_model,
        "analysis_mode": worker._analysis_mode(media_path.name),
        "compute_target": "cloud_hf_zerogpu",
        "top_rois": top_rois,
        "peak_t": peak_t,
        "seconds_elapsed": seconds_elapsed,
        "tribe_seconds": round(time.time() - started, 2),
        "n_t": int(bold.shape[0]),
        "tr_seconds": worker.TR_SECONDS,
        "has_bold_vertex": True,
        "bold_vertex_file": str(bold_path),
        **_space_readiness(),
    }
    json_path = SPACE_SCAN_DIR / f"{scan_id}.json"
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    return json.dumps(record, indent=2), str(bold_path)


def build_demo():
    if gr is None:
        raise RuntimeError("gradio is required to launch the Hugging Face Space UI")

    with gr.Blocks(title="Cortex TRIBE v2 ZeroGPU") as demo:
        gr.Markdown(
            "# Cortex TRIBE v2 ZeroGPU\n"
            "Upload a small stimulus file to produce a TRIBE BOLD prediction. "
            "Fake mode is for deployment smoke tests; real mode requires TRIBE weights."
        )
        media = gr.File(label="Stimulus file")
        tier = gr.Slider(0, 6, value=4, step=1, label="Narration tier metadata")
        narration_model = gr.Textbox(value="openrouter/free", label="Narration model metadata")
        run = gr.Button("Run TRIBE scan")
        scan_json = gr.Code(label="Scan JSON", language="json")
        bold_file = gr.File(label="BOLD .npy")
        run.click(
            fn=run_tribe_scan,
            inputs=[media, tier, narration_model],
            outputs=[scan_json, bold_file],
            api_name="scan",
        )
    return demo


demo = build_demo() if gr is not None else None


if __name__ == "__main__":  # pragma: no cover - manual local launch.
    if demo is None:
        raise SystemExit("Install gradio to launch this Space locally.")
    demo.launch()
