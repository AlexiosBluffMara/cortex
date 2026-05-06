"""Tests for cli/main.py — Typer CLI commands.

All cortex internals are mocked via monkeypatch fixtures so they take effect
for every test regardless of import order across the test session.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest
from typer.testing import CliRunner

pytestmark = pytest.mark.unit

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _make_inference_result(shape=(5, 20484)):
    preds = np.zeros(shape, dtype=np.float32)
    result = MagicMock()
    result.preds = preds
    result.preds.shape = shape
    result.seconds_elapsed = 1.23
    result.peak_t = 2
    result.roi_df = pd.DataFrame()
    result.top_rois = ["DMN_1", "DMN_2"]
    result.model_dtype = "bf16"
    return result


def _make_brain_mock():
    brain = MagicMock()
    brain.dominant_network = "DMN"
    brain.temporal = {"peak_s": 4.2, "peak_z": 3.14}
    brain.analysis_seconds = 0.8
    brain.gemma_context.return_value = {"key": "value"}
    brain.to_dict.return_value = {"result": "analysis"}
    return brain


# ---------------------------------------------------------------------------
# Fixtures — re-inject mocks for each test so import-order doesn't matter
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def mock_cortex_modules(monkeypatch):
    """Patch all cortex internals the CLI commands import lazily."""
    inf = _make_inference_result()
    brain = _make_brain_mock()

    # cortex.pipeline
    mock_pipeline = MagicMock()
    mock_pipeline.run_inference.return_value = inf
    mock_pipeline.run_inference_text_only.return_value = _make_inference_result()
    mock_pipeline.load_model.return_value = MagicMock(name="tribe-model")
    mock_pipeline.vram_report.return_value = {
        "gpu": "NVIDIA RTX 5090",
        "allocated": 4.12,
        "reserved": 6.00,
        "total": 32.00,
        "free": 26.00,
    }
    monkeypatch.setitem(sys.modules, "cortex.pipeline", mock_pipeline)

    # cortex.analysis
    mock_analysis = MagicMock()
    mock_analysis.analyse.return_value = brain
    monkeypatch.setitem(sys.modules, "cortex.analysis", mock_analysis)

    # cortex.tiers
    mock_tiers = MagicMock()
    mock_tiers.narrate_tier.return_value = "Single tier narration."
    mock_tiers.narrate_all_tiers.return_value = {i: f"Tier {i}" for i in range(7)}
    mock_tiers.narrate_quick.return_value = "Quick narration."
    mock_tiers._TIER_MODEL_MAP = {i: "gemma4:26b" for i in range(7)}
    monkeypatch.setitem(sys.modules, "cortex.tiers", mock_tiers)

    # cortex.model_manager
    mm_instance = MagicMock()
    mm_instance.status_report.return_value = "Models: [E4B warm]"
    mm_instance.warm_fast_model = MagicMock()
    mock_mm_mod = MagicMock()
    mock_mm_mod.get_manager.return_value = mm_instance
    monkeypatch.setitem(sys.modules, "cortex.model_manager", mock_mm_mod)

    # cortex.prompts
    mock_prompts = MagicMock()
    mock_prompts.EXPERTISE_LEVELS = {i: f"Level_{i}" for i in range(7)}
    monkeypatch.setitem(sys.modules, "cortex.prompts", mock_prompts)

    # cortex.visualize
    mock_viz = MagicMock()
    mock_viz.render_peak_cortex.return_value = Path("/tmp/brain_peak.png")
    mock_viz.render_network_summary.return_value = Path("/tmp/network_summary.png")
    mock_viz.render_timeseries_panel.return_value = Path("/tmp/roi_timeseries.png")
    mock_viz.render_roi_heatmap.return_value = Path("/tmp/roi_heatmap.png")
    monkeypatch.setitem(sys.modules, "cortex.visualize", mock_viz)

    # cortex.config — keep the real one but expose OUT_DIR as a tmp path
    # (we don't monkeypatch this; the real config is fine for CLI tests)

    return {
        "pipeline": mock_pipeline,
        "analysis": mock_analysis,
        "tiers": mock_tiers,
        "model_manager": mock_mm_mod,
        "mm_instance": mm_instance,
        "prompts": mock_prompts,
        "visualize": mock_viz,
        "brain": brain,
        "inference_result": inf,
    }


# Convenience: import the app after the module-level mocks are in place.
# We import it at module load time — that's fine because cli/main.py only
# imports typer at the top level. All cortex imports are lazy (inside the
# command functions), so they pick up the per-test monkeypatched modules.
from cli.main import app  # noqa: E402


def invoke(*args):
    return runner.invoke(app, list(args), catch_exceptions=False)


# ---------------------------------------------------------------------------
# --help for every command
# ---------------------------------------------------------------------------

class TestHelp:
    def test_root_help(self):
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0
        assert "cortex" in result.output.lower()

    def test_infer_help(self):
        result = runner.invoke(app, ["infer", "--help"])
        assert result.exit_code == 0
        assert "media" in result.output.lower()

    def test_text_help(self):
        result = runner.invoke(app, ["text", "--help"])
        assert result.exit_code == 0
        assert "description" in result.output.lower()

    def test_vram_help(self):
        result = runner.invoke(app, ["vram", "--help"])
        assert result.exit_code == 0

    def test_warmup_help(self):
        result = runner.invoke(app, ["warmup", "--help"])
        assert result.exit_code == 0

    def test_models_help(self):
        result = runner.invoke(app, ["models", "--help"])
        assert result.exit_code == 0

    def test_render_help(self):
        result = runner.invoke(app, ["render", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# vram command
# ---------------------------------------------------------------------------

class TestVramCommand:
    def test_vram_success_prints_gpu_name(self, mock_cortex_modules):
        result = runner.invoke(app, ["vram"])
        assert result.exit_code == 0
        assert "RTX 5090" in result.output

    def test_vram_success_prints_allocated(self, mock_cortex_modules):
        result = runner.invoke(app, ["vram"])
        assert result.exit_code == 0
        assert "4.12" in result.output

    def test_vram_no_gpu_exits_1(self, mock_cortex_modules):
        mock_cortex_modules["pipeline"].vram_report.return_value = {}
        result = runner.invoke(app, ["vram"])
        assert result.exit_code == 1
        assert "No CUDA GPU" in result.output


# ---------------------------------------------------------------------------
# warmup command
# ---------------------------------------------------------------------------

class TestWarmupCommand:
    def test_warmup_calls_load_model(self, mock_cortex_modules):
        mock_cortex_modules["pipeline"].load_model.reset_mock()
        result = runner.invoke(app, ["warmup"])
        assert result.exit_code == 0
        mock_cortex_modules["pipeline"].load_model.assert_called_once()
        assert "Model ready" in result.output


# ---------------------------------------------------------------------------
# models command
# ---------------------------------------------------------------------------

class TestModelsCommand:
    def test_models_calls_warm_fast_and_prints_report(self, mock_cortex_modules):
        mm = mock_cortex_modules["mm_instance"]
        mm.warm_fast_model.reset_mock()
        result = runner.invoke(app, ["models"])
        assert result.exit_code == 0
        mm.warm_fast_model.assert_called_once()
        assert "Models:" in result.output


# ---------------------------------------------------------------------------
# text command
# ---------------------------------------------------------------------------

class TestTextCommand:
    def test_text_basic_inference(self, mock_cortex_modules):
        mock_cortex_modules["pipeline"].run_inference_text_only.reset_mock()
        mock_cortex_modules["tiers"].narrate_quick.return_value = "Quick narration."
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference_text_only.return_value = inf

        result = runner.invoke(app, ["text", "A documentary about ocean life"])
        assert result.exit_code == 0
        mock_cortex_modules["pipeline"].run_inference_text_only.assert_called_once_with(
            "A documentary about ocean life"
        )
        assert "Quick narration" in result.output

    def test_text_all_tiers_flag(self, mock_cortex_modules):
        mock_cortex_modules["tiers"].narrate_all_tiers.return_value = {
            i: f"Tier {i}" for i in range(7)
        }
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference_text_only.return_value = inf
        mock_cortex_modules["analysis"].analyse.return_value = _make_brain_mock()

        result = runner.invoke(app, ["text", "ocean life", "--all-tiers"])
        assert result.exit_code == 0
        for i in range(7):
            assert f"TIER {i}" in result.output


# ---------------------------------------------------------------------------
# infer command
# ---------------------------------------------------------------------------

class TestInferCommand:
    def test_infer_missing_file_exits_with_error(self, mock_cortex_modules):
        result = runner.invoke(app, ["infer", "/nonexistent/video.mp4"])
        assert result.exit_code != 0

    def test_infer_existing_file_completes(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference.return_value = inf
        mock_cortex_modules["analysis"].analyse.return_value = _make_brain_mock()

        result = runner.invoke(app, ["infer", str(media), "--no-narrate"])
        assert result.exit_code == 0
        assert "Inference complete" in result.output

    def test_infer_saves_npy_output(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        out_file = tmp_path / "preds.npy"
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference.return_value = inf
        mock_cortex_modules["analysis"].analyse.return_value = _make_brain_mock()

        result = runner.invoke(
            app, ["infer", str(media), "--output", str(out_file), "--no-narrate"]
        )
        assert result.exit_code == 0
        assert out_file.exists()

    def test_infer_saves_json_output(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        json_file = tmp_path / "analysis.json"
        inf = _make_inference_result()
        brain = _make_brain_mock()
        brain.to_dict.return_value = {"result": "ok"}
        mock_cortex_modules["pipeline"].run_inference.return_value = inf
        mock_cortex_modules["analysis"].analyse.return_value = brain

        result = runner.invoke(
            app, ["infer", str(media), "--json", str(json_file), "--no-narrate"]
        )
        assert result.exit_code == 0
        assert json_file.exists()
        data = json.loads(json_file.read_text())
        assert data["result"] == "ok"

    def test_infer_narrates_single_tier(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference.return_value = inf
        mock_cortex_modules["analysis"].analyse.return_value = _make_brain_mock()
        mock_cortex_modules["tiers"].narrate_tier.return_value = "Single tier narration."

        result = runner.invoke(app, ["infer", str(media), "--tier", "3"])
        assert result.exit_code == 0
        assert "Single tier narration" in result.output

    def test_infer_narrates_all_tiers(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference.return_value = inf
        mock_cortex_modules["analysis"].analyse.return_value = _make_brain_mock()
        mock_cortex_modules["tiers"].narrate_all_tiers.return_value = {
            i: f"T{i}" for i in range(7)
        }

        result = runner.invoke(app, ["infer", str(media), "--all-tiers"])
        assert result.exit_code == 0
        for i in range(7):
            assert f"TIER {i}" in result.output


# ---------------------------------------------------------------------------
# render command
# ---------------------------------------------------------------------------

class TestRenderCommand:
    def test_render_success(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference.return_value = inf

        result = runner.invoke(
            app, ["render", str(media), "--out-dir", str(tmp_path)]
        )
        assert result.exit_code == 0
        assert "All renders complete" in result.output

    def test_render_invokes_all_four_renderers(self, tmp_path, mock_cortex_modules):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake")
        inf = _make_inference_result()
        mock_cortex_modules["pipeline"].run_inference.return_value = inf
        viz = mock_cortex_modules["visualize"]

        runner.invoke(app, ["render", str(media), "--out-dir", str(tmp_path)])
        viz.render_peak_cortex.assert_called_once()
        viz.render_network_summary.assert_called_once()
        viz.render_timeseries_panel.assert_called_once()
        viz.render_roi_heatmap.assert_called_once()
