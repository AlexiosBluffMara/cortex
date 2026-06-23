"""Coverage for the Hugging Face ZeroGPU Gradio adapter."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pytest

pytestmark = pytest.mark.integration


def test_huggingface_space_fake_scan_emits_scan_record_and_bold(tmp_path, monkeypatch):
    from cloud.huggingface_space import app as hf_app

    upload_dir = tmp_path / "uploads"
    scan_dir = tmp_path / "scans"
    upload_dir.mkdir()
    scan_dir.mkdir()
    monkeypatch.setattr(hf_app, "SPACE_UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(hf_app, "SPACE_SCAN_DIR", scan_dir)
    monkeypatch.setattr(hf_app.worker, "WORKER_MODE", "fake")

    stimulus = tmp_path / "clip.mp4"
    stimulus.write_bytes(b"fake video bytes")

    scan_json, bold_path = hf_app.run_tribe_scan(str(stimulus), tier=4, narration_model="openrouter/free")
    payload = json.loads(scan_json)

    assert payload["ok"] is True
    assert payload["status"] == "complete"
    assert payload["source"] == "huggingface-zerogpu-gradio"
    assert payload["analysis_mode"] == "tribe_video"
    assert payload["has_bold_vertex"] is True
    assert payload["top_rois"]

    bold_file = Path(bold_path)
    assert bold_file.exists()
    bold = np.load(bold_file)
    assert bold.shape[1] == hf_app.worker.N_VERTICES
    assert bold.shape[0] == payload["n_t"]
