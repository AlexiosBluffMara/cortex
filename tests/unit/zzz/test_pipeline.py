"""Tests for cortex.pipeline — TRIBE v2 inference pipeline.

All torch / tribev2 / nilearn imports are mocked at sys.modules level
before the pipeline module is imported, so no real GPU or weights are needed.
"""
from __future__ import annotations

import sys
import importlib
from dataclasses import fields
from pathlib import Path
from unittest.mock import MagicMock, patch, call
import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.unit

# ---------------------------------------------------------------------------
# sys.modules stubs — must be set before pipeline is imported
# ---------------------------------------------------------------------------

def _build_torch_mock(cuda_available: bool = True) -> MagicMock:
    mock_torch = MagicMock()
    mock_torch.cuda.is_available.return_value = cuda_available
    mock_torch.cuda.get_device_name.return_value = "NVIDIA RTX 5090"
    mock_torch.cuda.get_device_capability.return_value = (12, 0)
    props = MagicMock()
    props.total_mem = 32 * 1024 ** 3
    props.total_memory = 32 * 1024 ** 3
    mock_torch.cuda.get_device_properties.return_value = props
    mock_torch.cuda.memory_allocated.return_value = 4 * 1024 ** 3
    mock_torch.cuda.memory_reserved.return_value = 6 * 1024 ** 3
    mock_torch.cuda.max_memory_allocated.return_value = 8 * 1024 ** 3
    mock_torch.cuda.empty_cache = MagicMock()
    mock_torch.backends = MagicMock()
    mock_torch.backends.cuda = MagicMock()
    mock_torch.backends.cudnn = MagicMock()
    mock_torch.compile = MagicMock(side_effect=lambda fn, **kw: fn)
    mock_torch.inference_mode = MagicMock(return_value=_InferenceModeCtx())
    return mock_torch


class _InferenceModeCtx:
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _build_nilearn_mock() -> MagicMock:
    mock_nilearn = MagicMock()
    # fetch_atlas_schaefer_2018 → atlas with 400 labels
    atlas_obj = MagicMock()
    labels = [f"ROI_{i:03d}" for i in range(400)]
    atlas_obj.labels = labels
    atlas_obj.maps = "fake_nifti"
    mock_nilearn.datasets.fetch_atlas_schaefer_2018.return_value = atlas_obj
    mock_nilearn.datasets.fetch_surf_fsaverage.return_value = MagicMock(
        pial_left="left.surf", pial_right="right.surf"
    )
    # vol_to_surf → returns integer array matching fsaverage5 vertex count (10242 per hemi)
    n_hemi = 10242
    lh = np.arange(n_hemi, dtype=int) % 400 + 1
    rh = np.arange(n_hemi, dtype=int) % 400 + 1
    mock_nilearn.surface.vol_to_surf.side_effect = [lh, rh]
    return mock_nilearn


# Inject mocks before loading pipeline
_mock_torch = _build_torch_mock()
_mock_nilearn = _build_nilearn_mock()

sys.modules.setdefault("torch", _mock_torch)
sys.modules.setdefault("tribev2", MagicMock())
sys.modules.setdefault("tribev2.demo_utils", MagicMock())
sys.modules.setdefault("nilearn", _mock_nilearn)
sys.modules.setdefault("nilearn.datasets", _mock_nilearn.datasets)
sys.modules.setdefault("nilearn.surface", _mock_nilearn.surface)

# Now import pipeline (it will pick up the mocked modules)
import cortex.pipeline as pipeline  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_model(T: int = 5, V: int = 20484) -> MagicMock:
    """Return a TribeModel-like mock whose .predict() returns (ndarray, [])."""
    preds = np.random.rand(T, V).astype(np.float32)
    events_df = pd.DataFrame({"onset": [0.0], "duration": [2.0]})
    model = MagicMock()
    model.predict.return_value = (preds, [])
    model.get_events_dataframe.return_value = events_df
    return model, preds, events_df


# ---------------------------------------------------------------------------
# InferenceResult dataclass
# ---------------------------------------------------------------------------

class TestInferenceResult:
    def test_instantiation_and_field_access(self):
        preds = np.zeros((5, 20484), dtype=np.float32)
        roi_df = pd.DataFrame(np.zeros((5, 10)))
        result = pipeline.InferenceResult(
            preds=preds,
            roi_df=roi_df,
            top_rois=["ROI_001", "ROI_002"],
            peak_t=3,
            events_df=pd.DataFrame(),
            seconds_elapsed=1.23,
        )
        assert result.preds.shape == (5, 20484)
        assert result.peak_t == 3
        assert result.seconds_elapsed == pytest.approx(1.23)
        assert result.model_dtype == "bf16"  # default value

    def test_all_expected_fields_present(self):
        field_names = {f.name for f in fields(pipeline.InferenceResult)}
        expected = {"preds", "roi_df", "top_rois", "peak_t", "events_df",
                    "seconds_elapsed", "model_dtype"}
        assert expected.issubset(field_names)

    def test_custom_model_dtype(self):
        result = pipeline.InferenceResult(
            preds=np.zeros((2, 20484)),
            roi_df=pd.DataFrame(),
            top_rois=[],
            peak_t=0,
            events_df=pd.DataFrame(),
            seconds_elapsed=0.5,
            model_dtype="fp32",
        )
        assert result.model_dtype == "fp32"


# ---------------------------------------------------------------------------
# load_model
# ---------------------------------------------------------------------------

class TestLoadModel:
    def setup_method(self):
        # Reset module-level globals before each test
        pipeline._model = None
        pipeline._compiled = False
        pipeline._atlas_cache.clear()

    def teardown_method(self):
        pipeline._model = None
        pipeline._compiled = False
        pipeline._atlas_cache.clear()

    def test_cache_hit_returns_same_instance(self):
        sentinel = MagicMock(name="cached-model")
        pipeline._model = sentinel
        result = pipeline.load_model()
        assert result is sentinel

    def test_no_cuda_raises_runtime_error(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=False))
        pipeline._model = None
        with pytest.raises(RuntimeError, match="CUDA"):
            pipeline.load_model()

    def test_load_calls_tribe_from_pretrained(self, monkeypatch):
        pipeline._model = None
        # Ensure a fully-configured torch mock is in place for this test
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))

        fake_model, _, _ = _make_fake_model()
        TribeModel = MagicMock()
        TribeModel.from_pretrained.return_value = fake_model
        demo_utils = MagicMock()
        demo_utils.TribeModel = TribeModel
        monkeypatch.setitem(sys.modules, "tribev2.demo_utils", demo_utils)

        # Stub _warmup_model so we don't hit filesystem
        monkeypatch.setattr(pipeline, "_warmup_model", MagicMock())

        result = pipeline.load_model()
        assert result is fake_model
        TribeModel.from_pretrained.assert_called_once()

    def test_second_load_call_returns_cached(self, monkeypatch):
        pipeline._model = None
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))

        fake_model, _, _ = _make_fake_model()
        TribeModel = MagicMock()
        TribeModel.from_pretrained.return_value = fake_model
        demo_utils = MagicMock()
        demo_utils.TribeModel = TribeModel
        monkeypatch.setitem(sys.modules, "tribev2.demo_utils", demo_utils)
        monkeypatch.setattr(pipeline, "_warmup_model", MagicMock())

        m1 = pipeline.load_model()
        m2 = pipeline.load_model()
        assert m1 is m2
        # from_pretrained only called once
        assert TribeModel.from_pretrained.call_count == 1


# ---------------------------------------------------------------------------
# unload_model
# ---------------------------------------------------------------------------

class TestUnloadModel:
    def setup_method(self):
        pipeline._model = None
        pipeline._compiled = False

    def test_unload_clears_model(self):
        pipeline._model = MagicMock(name="loaded-model")
        pipeline.unload_model()
        assert pipeline._model is None

    def test_unload_resets_compiled_flag(self):
        pipeline._model = MagicMock()
        pipeline._compiled = True
        pipeline.unload_model()
        assert pipeline._compiled is False

    def test_unload_calls_empty_cache(self):
        pipeline._model = MagicMock()
        sys.modules["torch"].cuda.empty_cache.reset_mock()
        pipeline.unload_model()
        sys.modules["torch"].cuda.empty_cache.assert_called_once()

    def test_unload_when_already_none(self):
        pipeline._model = None
        # Should not raise
        pipeline.unload_model()
        assert pipeline._model is None


# ---------------------------------------------------------------------------
# run_inference
# ---------------------------------------------------------------------------

class TestRunInference:
    def setup_method(self):
        pipeline._model = None
        pipeline._compiled = False
        pipeline._atlas_cache.clear()

    def teardown_method(self):
        pipeline._model = None
        pipeline._compiled = False
        pipeline._atlas_cache.clear()

    def test_returns_inference_result(self, monkeypatch, tmp_path):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"fake video")

        fake_model, preds, events_df = _make_fake_model(T=8)
        monkeypatch.setattr(pipeline, "load_model", lambda: fake_model)
        monkeypatch.setattr(pipeline, "_schaefer_rois", lambda p: (
            pd.DataFrame(p[:, :10]), [f"ROI_{i:03d}" for i in range(12)]
        ))

        result = pipeline.run_inference(media)
        assert isinstance(result, pipeline.InferenceResult)
        assert result.preds.shape[1] == 20484

    def test_file_not_found_raises(self, monkeypatch):
        monkeypatch.setattr(pipeline, "load_model", lambda: MagicMock())
        with pytest.raises(FileNotFoundError):
            pipeline.run_inference(Path("/nonexistent/file.mp4"))

    def test_peak_t_within_bounds(self, monkeypatch, tmp_path):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"x")
        T = 6
        fake_model, preds, _ = _make_fake_model(T=T)
        monkeypatch.setattr(pipeline, "load_model", lambda: fake_model)
        monkeypatch.setattr(pipeline, "_schaefer_rois", lambda p: (
            pd.DataFrame(np.zeros((T, 5))), ["R1", "R2"]
        ))

        result = pipeline.run_inference(media)
        assert 0 <= result.peak_t < T

    def test_result_model_dtype_is_bf16(self, monkeypatch, tmp_path):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"x")
        fake_model, _, _ = _make_fake_model()
        monkeypatch.setattr(pipeline, "load_model", lambda: fake_model)
        monkeypatch.setattr(pipeline, "_schaefer_rois", lambda p: (pd.DataFrame(), []))

        result = pipeline.run_inference(media)
        assert result.model_dtype == "bf16"


# ---------------------------------------------------------------------------
# run_inference_text_only
# ---------------------------------------------------------------------------

class TestRunInferenceTextOnly:
    def setup_method(self):
        pipeline._model = None
        pipeline._compiled = False
        pipeline._atlas_cache.clear()

    def teardown_method(self):
        pipeline._model = None
        pipeline._compiled = False
        pipeline._atlas_cache.clear()

    def test_returns_inference_result(self, monkeypatch):
        fake_model, preds, events_df = _make_fake_model(T=4)
        monkeypatch.setattr(pipeline, "load_model", lambda: fake_model)
        monkeypatch.setattr(pipeline, "_schaefer_rois", lambda p: (pd.DataFrame(), []))

        result = pipeline.run_inference_text_only("A short documentary.")
        assert isinstance(result, pipeline.InferenceResult)
        assert result.model_dtype == "fp32"

    def test_empty_text_uses_default(self, monkeypatch):
        captured_text: list[str] = []
        fake_model, preds, events_df = _make_fake_model(T=4)

        def fake_get_events(text_path=None, **kw):
            if text_path:
                captured_text.append(Path(text_path).read_text(encoding="utf-8"))
            return events_df

        fake_model.get_events_dataframe.side_effect = fake_get_events
        monkeypatch.setattr(pipeline, "load_model", lambda: fake_model)
        monkeypatch.setattr(pipeline, "_schaefer_rois", lambda p: (pd.DataFrame(), []))

        pipeline.run_inference_text_only("   ")
        # Should have written the default fallback text
        assert captured_text[0] == "A short educational video."

    def test_tmp_file_cleaned_up(self, monkeypatch):
        fake_model, preds, events_df = _make_fake_model(T=3)
        monkeypatch.setattr(pipeline, "load_model", lambda: fake_model)
        monkeypatch.setattr(pipeline, "_schaefer_rois", lambda p: (pd.DataFrame(), []))

        import cortex.config as cfg
        created: list[Path] = []
        real_write_text = Path.write_text

        def tracking_write_text(self, data, encoding=None):
            if "quick_" in self.name:
                created.append(self)
            return real_write_text(self, data, encoding=encoding)

        monkeypatch.setattr(Path, "write_text", tracking_write_text)
        pipeline.run_inference_text_only("Hello world")
        # All created quick_ files should have been removed
        for p in created:
            assert not p.exists(), f"Temp file not cleaned up: {p}"


# ---------------------------------------------------------------------------
# _schaefer_rois (with mocked nilearn)
# ---------------------------------------------------------------------------

class TestSchaeferRois:
    def setup_method(self):
        pipeline._atlas_cache.clear()

    def teardown_method(self):
        pipeline._atlas_cache.clear()

    def test_returns_dataframe_and_top_rois(self):
        T, V = 10, 20484
        preds = np.random.rand(T, V).astype(np.float32)

        # nilearn vol_to_surf needs to return fresh arrays each call (side_effect consumed)
        n_hemi = V // 2
        lh = (np.arange(n_hemi) % 400 + 1).astype(int)
        rh = (np.arange(n_hemi) % 400 + 1).astype(int)
        sys.modules["nilearn.surface"].vol_to_surf.side_effect = [lh, rh]

        df, top = pipeline._schaefer_rois(preds)
        assert isinstance(df, pd.DataFrame)
        assert isinstance(top, list)
        assert len(top) <= 12

    def test_uses_cache_on_second_call(self):
        T, V = 4, 20484
        preds = np.random.rand(T, V).astype(np.float32)

        n_hemi = V // 2
        lh = (np.arange(n_hemi) % 400 + 1).astype(int)
        rh = (np.arange(n_hemi) % 400 + 1).astype(int)
        sys.modules["nilearn.surface"].vol_to_surf.side_effect = [lh, rh]

        pipeline._schaefer_rois(preds)
        assert "schaefer" in pipeline._atlas_cache

        # Second call — side_effect list is exhausted, so vol_to_surf would fail
        # if called again. Cache prevents it.
        sys.modules["nilearn.surface"].vol_to_surf.side_effect = []
        pipeline._schaefer_rois(preds)  # should not raise


# ---------------------------------------------------------------------------
# get_vram_usage
# ---------------------------------------------------------------------------

class TestGetVramUsage:
    def test_returns_dict_with_expected_keys(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))
        result = pipeline.get_vram_usage()
        assert isinstance(result, dict)
        expected_keys = {"available", "total_gb", "allocated_gb", "reserved_gb",
                         "free_gb", "peak_gb"}
        assert expected_keys.issubset(result.keys())

    def test_available_true_when_cuda_available(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))
        result = pipeline.get_vram_usage()
        assert result["available"] is True

    def test_returns_available_false_when_no_cuda(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=False))
        result = pipeline.get_vram_usage()
        assert result == {"available": False}

    def test_values_are_floats_in_gb(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))
        result = pipeline.get_vram_usage()
        if result.get("available"):
            assert result["total_gb"] > 0
            assert result["allocated_gb"] >= 0


# ---------------------------------------------------------------------------
# vram_report
# ---------------------------------------------------------------------------

class TestVramReport:
    def test_returns_dict_with_string_keys(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))
        result = pipeline.vram_report()
        assert isinstance(result, dict)

    def test_has_gpu_key_when_cuda_available(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))
        result = pipeline.vram_report()
        assert "gpu" in result
        assert isinstance(result["gpu"], str)

    def test_has_numeric_fields(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=True))
        result = pipeline.vram_report()
        for key in ("allocated", "reserved", "total", "free"):
            assert key in result
            assert isinstance(result[key], float)

    def test_returns_empty_dict_when_no_cuda(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", _build_torch_mock(cuda_available=False))
        result = pipeline.vram_report()
        assert result == {}


# ---------------------------------------------------------------------------
# _build_events routing
# ---------------------------------------------------------------------------

class TestBuildEvents:
    def test_video_extension_routes_to_video_path(self, tmp_path):
        media = tmp_path / "clip.mp4"
        media.write_bytes(b"x")
        model = MagicMock()
        model.get_events_dataframe.return_value = pd.DataFrame()

        pipeline._build_events(model, media)
        model.get_events_dataframe.assert_called_once_with(video_path=str(media))

    def test_audio_extension_routes_to_audio_path(self, tmp_path):
        media = tmp_path / "clip.wav"
        media.write_bytes(b"x")
        model = MagicMock()
        model.get_events_dataframe.return_value = pd.DataFrame()

        pipeline._build_events(model, media)
        model.get_events_dataframe.assert_called_once_with(audio_path=str(media))

    def test_text_extension_routes_to_text_path(self, tmp_path):
        media = tmp_path / "script.txt"
        media.write_text("hello", encoding="utf-8")
        model = MagicMock()
        model.get_events_dataframe.return_value = pd.DataFrame()

        pipeline._build_events(model, media)
        model.get_events_dataframe.assert_called_once_with(text_path=str(media))

    def test_unsupported_extension_raises(self, tmp_path):
        media = tmp_path / "data.bin"
        media.write_bytes(b"x")
        model = MagicMock()

        with pytest.raises(ValueError, match="Unsupported media type"):
            pipeline._build_events(model, media)
