"""Coverage tests for hermes.tools.brain_scan.execute().

Covers:
  - OOM RuntimeError path
  - Generic RuntimeError path
  - Vision gate execution path
  - Narration execution path
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Autouse fixture: restore real hermes.tools.brain_scan
# (test_hermes_tools_all.py installs a fake at collection time)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_brain_scan():
    import sys
    import hermes.tools as _ht
    sys.modules.pop("hermes.tools.brain_scan", None)
    if hasattr(_ht, "brain_scan"):
        delattr(_ht, "brain_scan")
    yield
    sys.modules.pop("hermes.tools.brain_scan", None)
    if hasattr(_ht, "brain_scan"):
        delattr(_ht, "brain_scan")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fake_inference_result():
    """Minimal inference result for brain_scan tests."""
    import pandas as pd
    from cortex.pipeline import InferenceResult

    preds = np.random.randn(10, 20484).astype(np.float32)
    top_rois = [f"7Networks_LH_Vis_{i}" for i in range(1, 7)]
    roi_df = pd.DataFrame(np.random.randn(10, len(top_rois)), columns=top_rois)
    events_df = pd.DataFrame({"onset": [0.0], "duration": [5.0]})
    return InferenceResult(
        preds=preds,
        roi_df=roi_df,
        top_rois=top_rois,
        peak_t=5,
        events_df=events_df,
        seconds_elapsed=0.5,
    )


def _make_media_info(duration_s=10.0):
    from cortex.media_processor import MediaInfo
    return MediaInfo(
        duration_s=duration_s,
        width=1920,
        height=1080,
        fps=30.0,
        has_audio=True,
        codec="h264",
        file_size_mb=5.0,
        audio_codec="aac",
        audio_sample_rate=48000,
    )


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# execute() — basic success path already tested via hermes_tools_all;
# here we focus on the three uncovered branches.
# ---------------------------------------------------------------------------

class TestBrainScanOOMPath:
    """RuntimeError with 'out of memory' text → oom error response."""

    def test_oom_error_returns_oom_key(self, tmp_path):
        from hermes.tools.brain_scan import execute

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"\x00")

        fake_info = _make_media_info()
        fake_processed = {
            "video": str(src),
            "audio": None,
            "keyframes_dir": None,
            "original_duration_s": 10.0,
            "processed_duration_s": 10.0,
            "original_resolution": "1920x1080",
            "n_keyframes": 0,
        }

        fake_scheduler = MagicMock()
        fake_scheduler.run_brain_scan = AsyncMock(
            side_effect=RuntimeError("CUDA out of memory")
        )

        with patch("cortex.media_processor.probe", return_value=fake_info), \
             patch("cortex.media_processor.preprocess_for_tribe", return_value=fake_processed), \
             patch("cortex.gpu_scheduler.get_scheduler", return_value=fake_scheduler):
            result = _run(execute(str(src), include_narration=False, extract_keyframes=False))

        assert result["ok"] is False
        assert result["error"] == "oom"


class TestBrainScanGenericRuntimeError:
    """RuntimeError without OOM text → inference_failed error response."""

    def test_generic_runtime_error_returns_inference_failed(self, tmp_path):
        from hermes.tools.brain_scan import execute

        src = tmp_path / "clip2.mp4"
        src.write_bytes(b"\x00")

        fake_info = _make_media_info()
        fake_processed = {
            "video": str(src),
            "audio": None,
            "keyframes_dir": None,
            "original_duration_s": 10.0,
            "processed_duration_s": 10.0,
            "original_resolution": "1920x1080",
            "n_keyframes": 0,
        }

        fake_scheduler = MagicMock()
        fake_scheduler.run_brain_scan = AsyncMock(
            side_effect=RuntimeError("model weights corrupted")
        )

        with patch("cortex.media_processor.probe", return_value=fake_info), \
             patch("cortex.media_processor.preprocess_for_tribe", return_value=fake_processed), \
             patch("cortex.gpu_scheduler.get_scheduler", return_value=fake_scheduler):
            result = _run(execute(str(src), include_narration=False, extract_keyframes=False))

        assert result["ok"] is False
        assert result["error"] == "inference_failed"
        assert "model weights corrupted" in result["message"]


class TestBrainScanVisionGatePath:
    """extract_keyframes=True + keyframes_dir present → vision_gate called."""

    def test_vision_gate_result_in_output(self, tmp_path):
        from hermes.tools.brain_scan import execute

        src = tmp_path / "clip3.mp4"
        src.write_bytes(b"\x00")

        kf_dir = tmp_path / "keyframes"
        kf_dir.mkdir()
        (kf_dir / "frame_0001.jpg").write_bytes(b"\x00")

        fake_info = _make_media_info()
        fake_processed = {
            "video": str(src),
            "audio": None,
            "keyframes_dir": str(kf_dir),
            "original_duration_s": 10.0,
            "processed_duration_s": 10.0,
            "original_resolution": "1920x1080",
            "n_keyframes": 1,
        }

        fake_scheduler = MagicMock()
        fake_scheduler.run_brain_scan = AsyncMock(return_value=_make_fake_inference_result())
        fake_scheduler._ollama_url = "http://localhost:11434"

        import cortex.gemma_vision as _gv
        fake_vision = AsyncMock(return_value={"analyses": ["nature documentary"], "label": "nature"})

        with patch("cortex.media_processor.probe", return_value=fake_info), \
             patch("cortex.media_processor.preprocess_for_tribe", return_value=fake_processed), \
             patch("cortex.gpu_scheduler.get_scheduler", return_value=fake_scheduler), \
             patch.object(_gv, "vision_gate", fake_vision, create=True):
            result = _run(execute(str(src), include_narration=False, extract_keyframes=True))

        assert result["ok"] is True
        assert "vision_analysis" in result

    def test_vision_gate_exception_is_absorbed(self, tmp_path):
        """If vision_gate raises, output still has ok=True with error in vision_analysis."""
        from hermes.tools.brain_scan import execute

        src = tmp_path / "clip4.mp4"
        src.write_bytes(b"\x00")

        kf_dir = tmp_path / "kf_err"
        kf_dir.mkdir()
        (kf_dir / "frame_0001.jpg").write_bytes(b"\x00")

        fake_info = _make_media_info()
        fake_processed = {
            "video": str(src),
            "audio": None,
            "keyframes_dir": str(kf_dir),
            "original_duration_s": 10.0,
            "processed_duration_s": 10.0,
            "original_resolution": "1920x1080",
            "n_keyframes": 1,
        }

        fake_scheduler = MagicMock()
        fake_scheduler.run_brain_scan = AsyncMock(return_value=_make_fake_inference_result())
        fake_scheduler._ollama_url = "http://localhost:11434"

        import cortex.gemma_vision as _gv2
        with patch("cortex.media_processor.probe", return_value=fake_info), \
             patch("cortex.media_processor.preprocess_for_tribe", return_value=fake_processed), \
             patch("cortex.gpu_scheduler.get_scheduler", return_value=fake_scheduler), \
             patch.object(_gv2, "vision_gate", AsyncMock(side_effect=Exception("vision error")), create=True):
            result = _run(execute(str(src), include_narration=False, extract_keyframes=True))

        assert result["ok"] is True
        assert "error" in result.get("vision_analysis", {})


class TestBrainScanNarrationPath:
    """include_narration=True → narrate_tier called and result in output."""

    def test_narration_in_output(self, tmp_path):
        from hermes.tools.brain_scan import execute

        src = tmp_path / "clip5.mp4"
        src.write_bytes(b"\x00")

        fake_info = _make_media_info()
        fake_processed = {
            "video": str(src),
            "audio": None,
            "keyframes_dir": None,
            "original_duration_s": 10.0,
            "processed_duration_s": 10.0,
            "original_resolution": "1920x1080",
            "n_keyframes": 0,
        }

        fake_scheduler = MagicMock()
        fake_scheduler.run_brain_scan = AsyncMock(return_value=_make_fake_inference_result())

        with patch("cortex.media_processor.probe", return_value=fake_info), \
             patch("cortex.media_processor.preprocess_for_tribe", return_value=fake_processed), \
             patch("cortex.gpu_scheduler.get_scheduler", return_value=fake_scheduler), \
             patch("cortex.tiers.narrate_tier", return_value="The visual cortex lit up."):
            result = _run(execute(str(src), include_narration=True,
                                  extract_keyframes=False, narration_tier=2))

        assert result["ok"] is True
        assert result["narration"] == "The visual cortex lit up."
        assert result["narration_tier"] == 2

    def test_narration_exception_absorbed(self, tmp_path):
        """narrate_tier failing → narration field contains error message, ok still True."""
        from hermes.tools.brain_scan import execute

        src = tmp_path / "clip6.mp4"
        src.write_bytes(b"\x00")

        fake_info = _make_media_info()
        fake_processed = {
            "video": str(src),
            "audio": None,
            "keyframes_dir": None,
            "original_duration_s": 10.0,
            "processed_duration_s": 10.0,
            "original_resolution": "1920x1080",
            "n_keyframes": 0,
        }

        fake_scheduler = MagicMock()
        fake_scheduler.run_brain_scan = AsyncMock(return_value=_make_fake_inference_result())

        with patch("cortex.media_processor.probe", return_value=fake_info), \
             patch("cortex.media_processor.preprocess_for_tribe", return_value=fake_processed), \
             patch("cortex.gpu_scheduler.get_scheduler", return_value=fake_scheduler), \
             patch("cortex.tiers.narrate_tier", side_effect=RuntimeError("ollama down")):
            result = _run(execute(str(src), include_narration=True, extract_keyframes=False))

        assert result["ok"] is True
        assert "Narration unavailable" in result["narration"]
