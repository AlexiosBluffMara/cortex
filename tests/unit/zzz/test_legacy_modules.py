"""Tests for legacy/small cortex modules:
  - cortex.cat_gate     (deprecated shim)
  - cortex.gemma        (legacy narrate + _roi_summary)
  - cortex.train_tribe  (fine-tune stub)
"""
from __future__ import annotations

import sys
import types
import warnings
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# cortex.cat_gate
# ---------------------------------------------------------------------------

class TestCatGate:
    def test_import_issues_deprecation_warning(self):
        # Re-import to force the deprecation warning
        if "cortex.cat_gate" in sys.modules:
            del sys.modules["cortex.cat_gate"]
        with warnings.catch_warnings(record=True) as w:
            warnings.simplefilter("always")
            import cortex.cat_gate  # noqa: F401
            assert any(issubclass(x.category, DeprecationWarning) for x in w)

    def test_cat_gate_exports_classify(self):
        import cortex.cat_gate as cg  # noqa: F401
        assert hasattr(cg, "classify")

    def test_cat_gate_exports_media_description(self):
        import cortex.cat_gate as cg  # noqa: F401
        assert hasattr(cg, "MediaDescription")

    def test_cat_gate_exports_default(self):
        import cortex.cat_gate as cg  # noqa: F401
        assert hasattr(cg, "DEFAULT")


# ---------------------------------------------------------------------------
# cortex.gemma (legacy narrate)
# ---------------------------------------------------------------------------

class TestLegacyNarrate:
    """Tests for cortex.gemma.narrate / _roi_summary.

    The root problem with sys.modules patching here is that test_hermes_agent.py
    and test_tiers.py (collected earlier / later) both set cortex.ollama_client
    as a *package attribute* (not just in sys.modules).  Python's `from . import
    ollama_client` inside a freshly imported cortex.gemma checks the package
    attribute first and bypasses sys.modules substitution.

    Fix: import cortex.gemma directly and then use patch.object to replace its
    `ollama_client` attribute for the duration of each test.  This is independent
    of sys.modules and always targets the exact object narrate() reads.
    """

    def _get_gemma(self):
        """Return fresh cortex.gemma module (re-import each call)."""
        sys.modules.pop("cortex.gemma", None)
        import cortex.gemma as _gm
        return _gm

    def test_narrate_returns_string(self):
        _gm = self._get_gemma()
        with patch.object(_gm, "ollama_client") as mock_oc:
            mock_oc.generate.return_value = "Brain response summary"
            result = _gm.narrate(
                top_rois=["7Networks_LH_Vis_1", "7Networks_RH_Default_2"],
                roi_means={"7Networks_LH_Vis_1": 1.23, "7Networks_RH_Default_2": 0.87},
                stimulus_label="nature documentary",
                duration_s=30.0,
                peak_time_s=12.5,
            )
        assert result == "Brain response summary"

    def test_narrate_calls_generate_once(self):
        _gm = self._get_gemma()
        with patch.object(_gm, "ollama_client") as mock_oc:
            mock_oc.generate.return_value = "ok"
            _gm.narrate(
                top_rois=["ROI_1"],
                roi_means={"ROI_1": 0.5},
                stimulus_label="test",
                duration_s=10.0,
                peak_time_s=5.0,
            )
            mock_oc.generate.assert_called_once()

    def test_narrate_truncates_to_8_rois(self):
        _gm = self._get_gemma()
        captured = []

        def _gen(user, system, num_predict=400):
            captured.append(user)
            return "ok"

        with patch.object(_gm, "ollama_client") as mock_oc:
            mock_oc.generate.side_effect = _gen
            rois = [f"ROI_{i}" for i in range(15)]
            _gm.narrate(
                top_rois=rois,
                roi_means={r: float(i) * 0.1 for i, r in enumerate(rois)},
                stimulus_label="x",
                duration_s=5.0,
                peak_time_s=2.5,
            )
        # The prompt header contains "mean |z|" once, plus 8 ROI lines each
        # containing "mean |z| = X.XXX".  Total occurrences = 9.
        assert captured[0].count("  - ROI_") == 8

    def test_narrate_empty_rois(self):
        _gm = self._get_gemma()
        with patch.object(_gm, "ollama_client") as mock_oc:
            mock_oc.generate.return_value = "empty response"
            result = _gm.narrate(
                top_rois=[],
                roi_means={},
                stimulus_label="empty test",
                duration_s=0.0,
                peak_time_s=0.0,
            )
        assert result == "empty response"

    def test_roi_summary_format(self):
        _gm = self._get_gemma()
        result = _gm._roi_summary(
            top_rois=["ROI_A", "ROI_B"],
            roi_means={"ROI_A": 1.5, "ROI_B": 0.8},
        )
        assert "ROI_A" in result
        assert "ROI_B" in result
        assert "1.500" in result
        assert "0.800" in result

    def test_roi_summary_truncates_at_8(self):
        _gm = self._get_gemma()
        rois = [f"ROI_{i}" for i in range(12)]
        result = _gm._roi_summary(rois, {r: 1.0 for r in rois})
        lines = [ln for ln in result.splitlines() if ln.strip()]
        assert len(lines) == 8


# ---------------------------------------------------------------------------
# cortex.train_tribe
# ---------------------------------------------------------------------------

class TestTrainTribe:
    def test_plan_returns_dict_with_model_key(self):
        from cortex.train_tribe import _plan
        p = _plan(["scan-abc", "scan-def"])
        assert p["model"] == "tribe-v2"

    def test_plan_contains_scan_ids(self):
        from cortex.train_tribe import _plan
        p = _plan(["id1", "id2", "id3"])
        assert p["scans"] == ["id1", "id2", "id3"]
        assert p["n_scans"] == 3

    def test_plan_has_expected_hyperparameters(self):
        from cortex.train_tribe import _plan
        p = _plan(["x"])
        assert p["lr"] == 1e-5
        assert p["epochs"] == 3
        assert p["lora_r"] == 16
        assert p["fp_dtype"] == "bfloat16"

    def test_plan_stage_is_fine_tune(self):
        from cortex.train_tribe import _plan
        assert _plan([])["stage"] == "fine-tune"

    def test_run_returns_zero(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CORTEX_TRIBE_CACHE", str(tmp_path))
        # Reload to pick up env var
        if "cortex.train_tribe" in sys.modules:
            del sys.modules["cortex.train_tribe"]
        from cortex.train_tribe import _run
        result = _run(["scan1", "scan2"])
        assert result == 0

    def test_run_creates_cache_dir(self, tmp_path, monkeypatch):
        cache = tmp_path / "tribe_cache"
        monkeypatch.setenv("CORTEX_TRIBE_CACHE", str(cache))
        if "cortex.train_tribe" in sys.modules:
            del sys.modules["cortex.train_tribe"]
        from cortex.train_tribe import _run
        _run(["scan1"])
        assert cache.exists()

    def test_run_prints_plan(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("CORTEX_TRIBE_CACHE", str(tmp_path))
        if "cortex.train_tribe" in sys.modules:
            del sys.modules["cortex.train_tribe"]
        from cortex.train_tribe import _run
        _run(["scan-xyz"])
        captured = capsys.readouterr()
        assert "training-plan" in captured.out
        assert "scan-xyz" in captured.out

    def test_main_returns_zero_with_scan_ids(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CORTEX_TRIBE_CACHE", str(tmp_path))
        if "cortex.train_tribe" in sys.modules:
            del sys.modules["cortex.train_tribe"]
        from cortex.train_tribe import main
        result = main(["--scan-ids", "s1", "s2", "s3"])
        assert result == 0

    def test_main_without_args_raises_system_exit(self):
        from cortex.train_tribe import main
        with pytest.raises(SystemExit):
            main([])
