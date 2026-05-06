"""Tests for cortex.analysis — BrainAnalysis and helper functions.

Strategy:
  - _temporal_dynamics, _yeo7_network_code, BrainAnalysis helpers are tested directly
  - analyse() is tested with a mocked InferenceResult that has roi_df already computed,
    which skips the nilearn atlas download paths. For the atlas-calling paths we test
    with mocked nilearn functions.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# Capture the REAL InferenceResult at module-level (collection time), before
# test_tiers.py replaces sys.modules["cortex.pipeline"] with a fake that lacks
# the `events_df` parameter.  All helpers below use _RealInferenceResult directly.
from cortex.pipeline import InferenceResult as _RealInferenceResult  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_result(T: int = 10, peak_t: int = 5):
    """InferenceResult-like object with pre-computed roi_df."""
    InferenceResult = _RealInferenceResult
    top_rois = [
        "7Networks_LH_Vis_1",
        "7Networks_LH_Default_2",
        "7Networks_RH_SomMot_3",
        "7Networks_RH_Cont_4",
        "7Networks_LH_DorsAttn_5",
        "7Networks_RH_Limbic_6",
    ]
    preds = np.random.randn(T, 20484).astype(np.float32) * 0.5
    roi_df = pd.DataFrame(
        np.random.randn(T, len(top_rois)) * 0.5,
        columns=top_rois,
    )
    events_df = pd.DataFrame({"onset": [0.0], "duration": [float(T) / 2]})
    return InferenceResult(
        preds=preds,
        roi_df=roi_df,
        top_rois=top_rois,
        peak_t=peak_t,
        events_df=events_df,
        seconds_elapsed=1.0,
    )


# ---------------------------------------------------------------------------
# _yeo7_network_code
# ---------------------------------------------------------------------------

class TestYeo7NetworkCode:
    def test_standard_roi_returns_network(self):
        from cortex.analysis import _yeo7_network_code
        assert _yeo7_network_code("7Networks_LH_Vis_1") == "Vis"

    def test_default_mode_network(self):
        from cortex.analysis import _yeo7_network_code
        assert _yeo7_network_code("7Networks_RH_Default_5") == "Default"

    def test_short_label_returns_unknown(self):
        from cortex.analysis import _yeo7_network_code
        assert _yeo7_network_code("AB") == "Unknown"

    def test_underscore_only_returns_unknown(self):
        from cortex.analysis import _yeo7_network_code
        assert _yeo7_network_code("ABC_DEF") == "Unknown"


# ---------------------------------------------------------------------------
# _temporal_dynamics
# ---------------------------------------------------------------------------

class TestTemporalDynamics:
    def _simple_ts(self, T: int = 20, peak: int = 10) -> np.ndarray:
        ts = np.zeros(T)
        ts[peak] = 1.0
        for i in range(1, 5):
            if peak - i >= 0:
                ts[peak - i] = 1.0 - i * 0.15
            if peak + i < T:
                ts[peak + i] = 1.0 - i * 0.15
        return ts.astype(np.float32)

    def test_peak_tr_matches_input(self):
        from cortex.analysis import _temporal_dynamics
        ts = self._simple_ts(peak=8)
        d = _temporal_dynamics(ts, peak_t=8)
        assert d["peak_tr"] == 8

    def test_peak_s_is_half_of_tr(self):
        from cortex.analysis import _temporal_dynamics
        ts = self._simple_ts(peak=10)
        d = _temporal_dynamics(ts, peak_t=10)
        assert d["peak_s"] == 5.0

    def test_decay_slope_is_finite(self):
        from cortex.analysis import _temporal_dynamics
        ts = self._simple_ts(peak=5)
        d = _temporal_dynamics(ts, peak_t=5)
        assert np.isfinite(d["decay_slope_per_tr"])

    def test_rise_s_is_non_negative(self):
        from cortex.analysis import _temporal_dynamics
        ts = self._simple_ts(peak=10)
        d = _temporal_dynamics(ts, peak_t=10)
        assert d["rise_s"] >= 0.0

    def test_duration_above_half_max_is_non_negative(self):
        from cortex.analysis import _temporal_dynamics
        ts = self._simple_ts(peak=10)
        d = _temporal_dynamics(ts, peak_t=10)
        assert d["duration_above_half_max_tr"] >= 0

    def test_peak_at_end_slope_is_zero(self):
        from cortex.analysis import _temporal_dynamics
        ts = np.zeros(5, dtype=np.float32)
        ts[-1] = 1.0
        d = _temporal_dynamics(ts, peak_t=4)
        assert d["decay_slope_per_tr"] == 0.0

    def test_all_keys_present(self):
        from cortex.analysis import _temporal_dynamics
        ts = self._simple_ts()
        d = _temporal_dynamics(ts, peak_t=10)
        for key in ["peak_tr", "peak_s", "peak_z", "rise_tr", "rise_s",
                    "duration_above_half_max_tr", "duration_above_half_max_s",
                    "decay_slope_per_tr"]:
            assert key in d


# ---------------------------------------------------------------------------
# BrainAnalysis
# ---------------------------------------------------------------------------

class TestBrainAnalysis:
    def _build(self, T: int = 10, peak_t: int = 5) -> "BrainAnalysis":
        from cortex.analysis import BrainAnalysis
        top_rois = [
            "7Networks_LH_Vis_1",
            "7Networks_RH_Default_2",
            "7Networks_LH_SomMot_3",
        ]
        roi_df = pd.DataFrame(
            np.random.randn(T, 3) * 0.5,
            columns=top_rois,
        )
        preds = np.random.randn(T, 20484).astype(np.float32) * 0.5
        ts = np.abs(preds).mean(axis=1)
        from cortex.analysis import _temporal_dynamics
        temporal = _temporal_dynamics(ts, peak_t)

        return BrainAnalysis(
            preds_shape=(T, 20484),
            duration_s=T / 2.0,
            s400_roi_df=roi_df,
            s400_top_rois=top_rois,
            network_means={"Vis": 0.5, "Default": 0.4, "SomMot": 0.3},
            network_laterality={"Vis": 0.2, "Default": -0.3, "SomMot": 0.0},
            dominant_network="Vis",
            temporal=temporal,
            vertices_above_1sd=5000,
            vertices_above_2sd=2000,
            activation_fraction_1sd=5000 / 20484,
            activation_fraction_2sd=2000 / 20484,
            global_mean_z=0.1,
            global_std_z=0.5,
            global_max_z=2.5,
            global_min_z=-2.5,
            lh_dominant_networks=["Visual", "Somatomotor"],
            rh_dominant_networks=["Default Mode"],
        )

    def test_to_dict_has_duration(self):
        ba = self._build()
        d = ba.to_dict()
        assert "duration_s" in d

    def test_to_dict_has_networks_ranked(self):
        ba = self._build()
        d = ba.to_dict()
        assert "networks_ranked" in d
        assert len(d["networks_ranked"]) > 0

    def test_to_dict_laterality_side(self):
        ba = self._build()
        d = ba.to_dict()
        nets = {n["network"]: n for n in d["networks_ranked"]}
        assert nets["Vis"]["laterality_side"] == "left"
        assert nets["Default"]["laterality_side"] == "right"
        assert nets["SomMot"]["laterality_side"] == "bilateral"

    def test_gemma_context_is_string(self):
        ba = self._build()
        ctx = ba.gemma_context()
        assert isinstance(ctx, str)
        assert len(ctx) > 0

    def test_gemma_context_contains_duration(self):
        ba = self._build()
        ctx = ba.gemma_context()
        assert "Duration" in ctx

    def test_top_roi_list_empty_df(self):
        from cortex.analysis import BrainAnalysis
        ba = BrainAnalysis(preds_shape=(5, 20484), duration_s=2.5)
        result = ba._top_roi_list(pd.DataFrame(), [], n=8)
        assert result == []

    def test_top_roi_list_with_data(self):
        ba = self._build()
        result = ba._top_roi_list(
            ba.s400_roi_df,
            ba.s400_top_rois,
            n=3,
        )
        assert len(result) <= 3
        for item in result:
            assert "roi" in item
            assert "mean_abs_z" in item


# ---------------------------------------------------------------------------
# analyse() function
# ---------------------------------------------------------------------------

class TestAnalyseFunction:
    def test_analyse_returns_brain_analysis(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False, brainnetome=False)
        from cortex.analysis import BrainAnalysis
        assert isinstance(ba, BrainAnalysis)

    def test_analyse_preds_shape_correct(self):
        from cortex.analysis import analyse
        result = _make_fake_result(T=8)
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert ba.preds_shape == (8, 20484)

    def test_analyse_duration_s(self):
        from cortex.analysis import analyse
        result = _make_fake_result(T=20)
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert ba.duration_s == pytest.approx(10.0)

    def test_analyse_network_means_not_empty(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert len(ba.network_means) > 0

    def test_analyse_temporal_has_peak_tr(self):
        from cortex.analysis import analyse
        result = _make_fake_result(peak_t=4)
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert ba.temporal["peak_tr"] == 4

    def test_analyse_activation_volume(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert ba.vertices_above_1sd >= 0
        assert ba.vertices_above_2sd >= 0

    def test_analyse_high_res_skipped_by_default(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, high_res=False, harvard_oxford=False, juelich=False)
        assert ba.s1000_roi_df.empty

    def test_analyse_harvard_oxford_exception_is_swallowed(self):
        from cortex.analysis import analyse

        with patch("nilearn.datasets.fetch_atlas_harvard_oxford",
                   side_effect=Exception("atlas not found")):
            result = _make_fake_result()
            ba = analyse(result, harvard_oxford=True, juelich=False)
        # Should not raise; ho fields remain empty
        assert ba.ho_roi_df.empty

    def test_analyse_juelich_exception_is_swallowed(self):
        from cortex.analysis import analyse

        with patch("nilearn.datasets.fetch_atlas_juelich",
                   side_effect=Exception("atlas not found")):
            result = _make_fake_result()
            ba = analyse(result, harvard_oxford=False, juelich=True)
        assert ba.juelich_roi_df.empty

    def test_analyse_brainnetome_off_by_default(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False, brainnetome=False)
        assert ba.bna_roi_df.empty

    def test_analyse_global_stats_finite(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert np.isfinite(ba.global_mean_z)
        assert np.isfinite(ba.global_std_z)
        assert np.isfinite(ba.global_max_z)
        assert np.isfinite(ba.global_min_z)

    def test_analyse_dominant_network_is_string(self):
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False)
        assert isinstance(ba.dominant_network, str)

    def test_to_dict_serializable(self):
        import json
        from cortex.analysis import analyse
        result = _make_fake_result()
        ba = analyse(result, harvard_oxford=False, juelich=False)
        d = ba.to_dict()
        # Should be JSON serializable (all types are basic Python types)
        json.dumps(d)  # raises if not serializable


# ---------------------------------------------------------------------------
# Atlas helper functions (mocked nilearn)
# ---------------------------------------------------------------------------

def _make_fake_vol_labels(n_verts: int = 10242, n_rois: int = 5) -> np.ndarray:
    """Return integer vertex labels: roi 1 gets verts[:200], roi 2 next 200, etc."""
    labels = np.zeros(n_verts, dtype=int)
    chunk = n_verts // (n_rois + 1)
    for i in range(1, min(n_rois + 1, n_verts // chunk + 1)):
        labels[(i - 1) * chunk: i * chunk] = i
    return labels


class TestAtlasFunctions:
    """Cover _project_volumetric_atlas and the atlas-specific callers."""

    @pytest.fixture(autouse=True)
    def _clear_atlas_cache(self):
        """Clear module-level atlas cache before and after each test."""
        import cortex.analysis as _an
        _an._atlas_cache.clear()
        yield
        _an._atlas_cache.clear()

    def _fake_vol_to_surf_factory(self, lh_labels, rh_labels):
        """Return a side_effect callable that alternates lh / rh on each call."""
        calls = [0]

        def _fake(atlas_maps, surf_mesh, **kwargs):
            idx = calls[0]
            calls[0] += 1
            return lh_labels if idx % 2 == 0 else rh_labels

        return _fake

    def test_project_volumetric_atlas_returns_df_and_top(self):
        from unittest.mock import MagicMock, patch
        import cortex.analysis as _an

        preds = np.random.randn(5, 20484).astype(np.float32)
        labels = [f"TestROI_{i}" for i in range(5)]

        lh = _make_fake_vol_labels(10242, 3)
        rh = _make_fake_vol_labels(10242, 2)

        fake_fsavg5 = MagicMock()
        with patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.surface.vol_to_surf",
                   side_effect=self._fake_vol_to_surf_factory(lh, rh)):
            df, top = _an._project_volumetric_atlas(preds, MagicMock(), labels, "proj_test")

        assert isinstance(df, pd.DataFrame)
        assert isinstance(top, list)

    def test_project_volumetric_atlas_uses_cache_on_second_call(self):
        """Second call with same cache_key skips vol_to_surf."""
        from unittest.mock import MagicMock, patch
        import cortex.analysis as _an

        preds = np.random.randn(5, 20484).astype(np.float32)
        labels = [f"CachedROI_{i}" for i in range(3)]

        lh = _make_fake_vol_labels(10242, 3)
        rh = _make_fake_vol_labels(10242, 2)

        call_count = [0]

        def _counting_vol_to_surf(atlas_maps, surf_mesh, **kwargs):
            call_count[0] += 1
            return lh if call_count[0] % 2 == 1 else rh

        fake_fsavg5 = MagicMock()
        with patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.surface.vol_to_surf", side_effect=_counting_vol_to_surf):
            _an._project_volumetric_atlas(preds, MagicMock(), labels, "cache_key_test")
            calls_after_first = call_count[0]
            _an._project_volumetric_atlas(preds, MagicMock(), labels, "cache_key_test")

        # vol_to_surf called only for first call (lh+rh = 2 calls); zero more for cached
        assert call_count[0] == calls_after_first

    def test_schaefer_roi_means_decodes_bytes_labels(self):
        """_schaefer_roi_means decodes bytes labels from nilearn atlas."""
        from unittest.mock import MagicMock, patch
        import cortex.analysis as _an

        preds = np.random.randn(5, 20484).astype(np.float32)

        fake_atlas = MagicMock()
        # Mix of bytes and str labels (nilearn sometimes returns bytes)
        fake_atlas.labels = [
            b"7Networks_LH_Vis_1",
            b"7Networks_LH_Default_2",
            "7Networks_RH_SomMot_3",
        ]
        fake_atlas.maps = MagicMock()

        lh = _make_fake_vol_labels(10242, 3)
        rh = _make_fake_vol_labels(10242, 2)

        fake_fsavg5 = MagicMock()
        with patch("nilearn.datasets.fetch_atlas_schaefer_2018", return_value=fake_atlas), \
             patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.surface.vol_to_surf",
                   side_effect=self._fake_vol_to_surf_factory(lh, rh)):
            df, top = _an._schaefer_roi_means(preds, n_rois=400)

        assert isinstance(df, pd.DataFrame)
        # Bytes labels decoded to str
        assert all(isinstance(c, str) for c in df.columns)

    def test_harvard_oxford_roi_means_concatenates_cort_subc(self):
        """_harvard_oxford_roi_means merges cortical + subcortical DataFrames."""
        from unittest.mock import MagicMock, patch
        import cortex.analysis as _an

        preds = np.random.randn(5, 20484).astype(np.float32)

        fake_cort = MagicMock()
        fake_cort.labels = ["Background", "Frontal Pole", "Insular Cortex"]
        fake_cort.maps = MagicMock()

        fake_subc = MagicMock()
        fake_subc.labels = ["Background", "Caudate", "Thalamus"]
        fake_subc.maps = MagicMock()

        def _fetch_ho(atlas_name, **kwargs):
            return fake_cort if "cort" in atlas_name else fake_subc

        lh = _make_fake_vol_labels(10242, 2)
        rh = _make_fake_vol_labels(10242, 2)

        fake_fsavg5 = MagicMock()
        with patch("nilearn.datasets.fetch_atlas_harvard_oxford", side_effect=_fetch_ho), \
             patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.surface.vol_to_surf",
                   side_effect=self._fake_vol_to_surf_factory(lh, rh)):
            df, top = _an._harvard_oxford_roi_means(preds)

        assert isinstance(df, pd.DataFrame)
        # Should have columns from both atlases (minus background)
        cort_cols = [c for c in df.columns if "HO-cort" in c]
        subc_cols = [c for c in df.columns if "HO-sub" in c]
        assert len(cort_cols) > 0 or len(subc_cols) > 0  # at least one atlas had active ROIs

    def test_juelich_roi_means_skips_background(self):
        """_juelich_roi_means skips the first label (Background)."""
        from unittest.mock import MagicMock, patch
        import cortex.analysis as _an

        preds = np.random.randn(5, 20484).astype(np.float32)

        fake_atlas = MagicMock()
        fake_atlas.labels = ["Background", "Area 4a (PreCG)", "Area 4p (PreCG)", "Area 3b (PosCG)"]
        fake_atlas.maps = MagicMock()

        lh = _make_fake_vol_labels(10242, 3)
        rh = _make_fake_vol_labels(10242, 2)

        fake_fsavg5 = MagicMock()
        with patch("nilearn.datasets.fetch_atlas_juelich", return_value=fake_atlas), \
             patch("nilearn.datasets.fetch_surf_fsaverage", return_value=fake_fsavg5), \
             patch("nilearn.surface.vol_to_surf",
                   side_effect=self._fake_vol_to_surf_factory(lh, rh)):
            df, top = _an._juelich_roi_means(preds)

        assert isinstance(df, pd.DataFrame)
        # Background should not appear in columns
        assert not any("Background" in c for c in df.columns)


# ---------------------------------------------------------------------------
# analyse() extra paths (empty roi_df, high_res, brainnetome exception)
# ---------------------------------------------------------------------------

class TestAnalyseExtraPaths:
    """Cover branches in analyse() not hit by the main test class."""

    def _make_empty_roi_result(self, T: int = 10, peak_t: int = 5):
        """InferenceResult with empty roi_df (forces _schaefer_roi_means call)."""
        return _RealInferenceResult(
            preds=np.random.randn(T, 20484).astype(np.float32),
            roi_df=pd.DataFrame(),  # empty → triggers _schaefer_roi_means
            top_rois=[],
            peak_t=peak_t,
            events_df=pd.DataFrame({"onset": [0.0], "duration": [float(T) / 2]}),
            seconds_elapsed=1.0,
        )

    def _schaefer_fake(self):
        """Return (fake_df, fake_top) that analyse() can consume."""
        cols = [f"7Networks_LH_Vis_{i}" for i in range(1, 7)] + \
               [f"7Networks_RH_Default_{i}" for i in range(1, 5)]
        df = pd.DataFrame(np.random.randn(10, len(cols)) * 0.5, columns=cols)
        return df, cols[:6]

    def test_analyse_empty_roi_df_triggers_schaefer_call(self):
        """When result.roi_df is empty, analyse() calls _schaefer_roi_means."""
        from cortex.analysis import analyse, BrainAnalysis
        from unittest.mock import patch

        result = self._make_empty_roi_result()
        fake_df, fake_top = self._schaefer_fake()

        with patch("cortex.analysis._schaefer_roi_means", return_value=(fake_df, fake_top)) as mock_s:
            ba = analyse(result, harvard_oxford=False, juelich=False)

        mock_s.assert_called_once()
        assert isinstance(ba, BrainAnalysis)
        assert not ba.s400_roi_df.empty

    def test_analyse_high_res_calls_schaefer_1000(self):
        """high_res=True triggers a second _schaefer_roi_means call with n_rois=1000."""
        from cortex.analysis import analyse
        from unittest.mock import patch

        result = _make_fake_result()
        fake_df, fake_top = self._schaefer_fake()

        with patch("cortex.analysis._schaefer_roi_means",
                   return_value=(fake_df, fake_top)) as mock_s:
            ba = analyse(result, high_res=True, harvard_oxford=False, juelich=False)

        mock_s.assert_called_once_with(result.preds, 1000)
        assert not ba.s1000_roi_df.empty

    def test_analyse_brainnetome_exception_swallowed(self):
        """brainnetome=True + exception leaves bna_roi_df empty (no raise)."""
        from cortex.analysis import analyse
        from unittest.mock import patch

        result = _make_fake_result()
        with patch("cortex.analysis._brainnetome_roi_means",
                   side_effect=Exception("network download failed")):
            ba = analyse(result, harvard_oxford=False, juelich=False, brainnetome=True)

        assert ba.bna_roi_df.empty

    def test_analyse_kurtosis_fallback_on_exception(self):
        """If scipy.stats.kurtosis raises, kurtosis defaults to 0.0."""
        from cortex.analysis import analyse
        from unittest.mock import patch

        result = _make_fake_result()

        # scipy.stats is already imported; patch the kurtosis function to raise
        with patch("scipy.stats.kurtosis", side_effect=RuntimeError("scipy broken")):
            ba = analyse(result, harvard_oxford=False, juelich=False)

        # global_kurtosis defaults to 0.0 when kurtosis() raises
        assert ba.global_kurtosis == 0.0
