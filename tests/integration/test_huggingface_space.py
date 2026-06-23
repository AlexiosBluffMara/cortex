"""Coverage for the Hugging Face ZeroGPU Gradio adapter."""
from __future__ import annotations

import json
import shutil
import subprocess
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


def test_huggingface_space_bundle_manifest_is_exportable():
    root = Path(__file__).resolve().parents[2]
    requirements = (root / "cloud/huggingface_space/requirements.txt").read_text(encoding="utf-8")
    export_script = (root / "scripts/export_huggingface_space.ps1").read_text(encoding="utf-8")
    space_readme = (root / "cloud/huggingface_space/SPACE_README.md").read_text(encoding="utf-8")

    assert "sdk: gradio" in space_readme
    assert "hardware: zerogpu" in space_readme
    assert "gradio" in requirements
    assert "spaces" in requirements
    assert "numpy" in requirements
    assert "cloud\\huggingface_space\\app.py" in export_script
    assert "cloud\\huggingface_space\\SPACE_README.md" in export_script
    assert "cloud\\huggingface_space\\requirements.txt" in export_script
    assert 'Copy-TreeFiltered (Join-Path $RepoRoot "cloud")' in export_script
    assert 'Copy-TreeFiltered (Join-Path $RepoRoot "cortex")' in export_script
    assert "Refusing to clean" in export_script


def test_huggingface_space_export_script_builds_upload_root(tmp_path):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    if shell is None:
        pytest.skip("PowerShell is required for the Space export script")

    root = Path(__file__).resolve().parents[2]
    out_dir = tmp_path / "hf-space"
    result = subprocess.run(
        [
            shell,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(root / "scripts/export_huggingface_space.ps1"),
            "-OutputDir",
            str(out_dir),
        ],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (out_dir / "app.py").exists()
    assert (out_dir / "README.md").read_text(encoding="utf-8").startswith("---")
    assert (out_dir / "requirements.txt").exists()
    assert (out_dir / "cloud/tribe_worker/app.py").exists()
    assert (out_dir / "cortex/__init__.py").exists()
