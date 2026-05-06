"""Tests for cortex.visualize — publication-quality chart rendering.

All tests use the Agg backend so no display server is needed.
nilearn atlas functions are mocked to avoid network calls.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# Pre-import seaborn at collection time (before test_pipeline.py mocks torch).
# render_roi_heatmap imports seaborn lazily; if torch is a MagicMock at that
# point, scipy's issubclass check raises TypeError.  Caching seaborn now
# (when torch is real or absent) avoids the re-import at test run time.
import seaborn  # noqa: F401

# Capture REAL InferenceResult at module-level (collection time), before
# test_tiers.py replaces cortex.pipeline with a fake lacking `events_df`.
from cortex.pipeline import InferenceResult as _RealInferenceResult  # noqa: E402


# ---------------------------------------------------------------------------
# Fake InferenceResult
# ---------------------------------------------------------------------------

def _make_result(T: int = 10, peak_t: int = 5):
    InferenceResult = _RealInferenceResult
    preds = np.random.randn(T, 20484).astype(np.float32)
    top_rois = [
        f"7Networks_LH_Vis_{i}" for i in range(1, 7)
    ] + [
        f"7Networks_RH_Default_{i}" for i in range(1, 7)
    ]
    roi_df = pd.DataFrame(
        np.random.randn(T, len(top_rois)),
        columns=top_rois,
    )
    events_df = pd.DataFrame({"onset": [0.0], "duration": [float(T) / 2]})
    return InferenceResult(
        preds=preds,
        roi_df=roi_df,
        top_rois=top_rois[:12],
        peak_t=peak_t,
        events_df=events_df,
        seconds_elapsed=1.0,
    )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

class TestRoiHelpers:
    def test_roi_network_extracts_code(self):
        from cortex.visualize import _roi_network
        assert _roi_network("7Networks_LH_Vis_1") == "Vis"

    def test_roi_network_unknown_for_short_label(self):
        from cortex.visualize import _roi_network
        assert _roi_network("ABC") == "Unknown"

    def test_roi_color_known_network(self):
        from cortex.visualize import _roi_color
        color = _roi_color("7Networks_LH_Vis_1")
        assert color.startswith("#")

    def test_roi_color_unknown_network(self):
        from cortex.visualize import _roi_color
        color = _roi_color("UnknownROI")
        assert color == "#888888"

    def test_fig_footer_with_label(self):
        from cortex.visualize import _fig_footer
        footer = _fig_footer("nature doc")
        assert "nature doc" in footer

    def test_fig_footer_empty_label(self):
        from cortex.visualize import _fig_footer
        footer = _fig_footer("")
        assert "TRIBE" in footer


# ---------------------------------------------------------------------------
# render_peak_cortex
# ---------------------------------------------------------------------------

class TestRenderPeakCortex:
    def test_returns_path(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")

        fake_fsavg5 = MagicMock()
        fake_fsavg5.infl_left = MagicMock()
        fake_fsavg5.infl_right = MagicMock()
        fake_fsavg5.sulc_left = MagicMock()
        fake_fsavg5.sulc_right = MagicMock()

        with patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.plotting.plot_surf_stat_map", return_value=MagicMock()):
            from cortex.visualize import render_peak_cortex
            result = _make_result()
            out = render_peak_cortex(result, label="test", out_path=tmp_path / "peak.png")
        assert isinstance(out, Path)

    def test_uses_default_out_path(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")

        fake_fsavg5 = MagicMock()
        fake_fsavg5.infl_left = MagicMock()
        fake_fsavg5.infl_right = MagicMock()
        fake_fsavg5.sulc_left = MagicMock()
        fake_fsavg5.sulc_right = MagicMock()

        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)
        with patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.plotting.plot_surf_stat_map", return_value=MagicMock()):
            from cortex.visualize import render_peak_cortex
            result = _make_result()
            out = render_peak_cortex(result)
        assert isinstance(out, Path)


# ---------------------------------------------------------------------------
# render_roi_stream
# ---------------------------------------------------------------------------

class TestRenderRoiStream:
    def test_returns_path_mp4(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")
        from matplotlib.animation import FuncAnimation

        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        with patch("matplotlib.animation.FFMpegWriter") as mock_writer:
            mock_writer.return_value.__enter__ = MagicMock(return_value=None)
            mock_writer.return_value.__exit__ = MagicMock(return_value=False)
            # FuncAnimation.save needs to be a no-op
            with patch.object(FuncAnimation, "save", return_value=None):
                from cortex.visualize import render_roi_stream
                result = _make_result()
                out = render_roi_stream(result, out_path=tmp_path / "stream.mp4")
        assert isinstance(out, Path)


# ---------------------------------------------------------------------------
# render_network_summary
# ---------------------------------------------------------------------------

class TestRenderNetworkSummary:
    def test_returns_png_path(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        from cortex.visualize import render_network_summary
        result = _make_result()
        out = render_network_summary(result, out_path=tmp_path / "networks.png")
        assert isinstance(out, Path)

    def test_creates_file(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        from cortex.visualize import render_network_summary
        result = _make_result()
        out_path = tmp_path / "net_chart.png"
        render_network_summary(result, out_path=out_path)
        assert out_path.exists()


# ---------------------------------------------------------------------------
# render_timeseries_panel
# ---------------------------------------------------------------------------

class TestRenderTimeseriesPanel:
    def test_returns_png_path(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        from cortex.visualize import render_timeseries_panel
        result = _make_result()
        out = render_timeseries_panel(result, out_path=tmp_path / "ts.png")
        assert isinstance(out, Path)


# ---------------------------------------------------------------------------
# render_roi_heatmap
# ---------------------------------------------------------------------------

class TestRenderRoiHeatmap:
    def test_returns_png_path(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        from cortex.visualize import render_roi_heatmap
        result = _make_result()
        out = render_roi_heatmap(result, out_path=tmp_path / "heatmap.png")
        assert isinstance(out, Path)

    def test_creates_file(self, tmp_path, monkeypatch):
        import matplotlib
        matplotlib.use("Agg")
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        from cortex.visualize import render_roi_heatmap
        result = _make_result()
        out_path = tmp_path / "hm.png"
        render_roi_heatmap(result, out_path=out_path)
        assert out_path.exists()
