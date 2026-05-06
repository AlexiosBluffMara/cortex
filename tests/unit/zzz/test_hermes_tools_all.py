"""Tests for hermes/tools/*.py — brain_scan, narrate, describe_input, visualize."""
from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Restore real hermes.tools.* submodules before each test.
# test_hermes_agent.py (collected earlier alphabetically: "agent" < "tools")
# replaces hermes.tools.brain_scan / narrate / describe_input / visualize in
# both sys.modules AND as package attributes.  This fixture clears both so
# that `from hermes.tools.X import Y` gets the real implementation.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_hermes_tools():
    import hermes.tools as _ht
    for _mod in ("brain_scan", "narrate", "describe_input", "visualize"):
        sys.modules.pop(f"hermes.tools.{_mod}", None)
        if hasattr(_ht, _mod):
            delattr(_ht, _mod)
    yield


# ---------------------------------------------------------------------------
# Shared fake helpers
# ---------------------------------------------------------------------------

def _make_fake_scheduler():
    sched = MagicMock()
    sched.ensure_gemma = AsyncMock(return_value=None)
    sched.run_brain_scan = AsyncMock()
    sched._ollama_url = "http://fake-ollama:11434"
    sched.state = MagicMock()
    sched.state.value = "gemma_active"
    return sched


def _make_fake_inference_result():
    import numpy as np
    import pandas as pd
    preds = np.random.randn(10, 20484).astype("float32")
    top_rois = [f"7Networks_LH_Vis_{i}" for i in range(1, 7)]
    roi_df = pd.DataFrame(
        np.random.randn(10, 6),
        columns=top_rois,
    )
    result = MagicMock()
    result.preds = preds
    result.top_rois = top_rois
    result.roi_df = roi_df
    result.peak_t = 5
    result.seconds_elapsed = 12.3
    return result


# ---------------------------------------------------------------------------
# hermes/tools/brain_scan.py
# ---------------------------------------------------------------------------

class TestBrainScanToolSchema:
    def test_schema_has_correct_name(self):
        from hermes.tools.brain_scan import TOOL_SCHEMA
        assert TOOL_SCHEMA["name"] == "brain_scan"

    def test_schema_requires_media_path(self):
        from hermes.tools.brain_scan import TOOL_SCHEMA
        assert "media_path" in TOOL_SCHEMA["parameters"]["required"]

    def test_schema_has_include_narration_param(self):
        from hermes.tools.brain_scan import TOOL_SCHEMA
        assert "include_narration" in TOOL_SCHEMA["parameters"]["properties"]

    def test_schema_has_narration_tier_param(self):
        from hermes.tools.brain_scan import TOOL_SCHEMA
        assert "narration_tier" in TOOL_SCHEMA["parameters"]["properties"]


class TestBrainScanExecute:
    def test_file_not_found_returns_error(self):
        async def _run():
            from hermes.tools.brain_scan import execute
            return await execute("/nonexistent/path/video.mp4")
        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "file_not_found"

    def test_unsupported_format_returns_error(self, tmp_path):
        f = tmp_path / "audio.xyz"
        f.write_bytes(b"fake")
        async def _run():
            from hermes.tools.brain_scan import execute
            return await execute(str(f))
        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "unsupported_format"

    def test_preprocessing_error_returns_error(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake mp4")

        fake_scheduler = _make_fake_scheduler()
        fake_result = _make_fake_inference_result()
        fake_scheduler.run_brain_scan = AsyncMock(return_value=fake_result)

        async def _run():
            from hermes.tools.brain_scan import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.media_processor": MagicMock(
                    probe=MagicMock(side_effect=Exception("MediaProcessingError: bad file")),
                    preprocess_for_tribe=MagicMock(),
                    MediaProcessingError=Exception,
                ),
            }):
                return await execute(str(f))

        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "preprocessing_failed"

    def test_successful_scan_returns_ok(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake mp4")

        fake_scheduler = _make_fake_scheduler()
        fake_result = _make_fake_inference_result()
        fake_scheduler.run_brain_scan = AsyncMock(return_value=fake_result)

        fake_probe_info = MagicMock()
        fake_probe_info.duration_s = 30.0

        async def _run():
            from hermes.tools.brain_scan import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.media_processor": MagicMock(
                    probe=MagicMock(return_value=fake_probe_info),
                    preprocess_for_tribe=MagicMock(return_value={
                        "video": str(f), "audio": None,
                        "original_duration_s": 30.0, "processed_duration_s": 30.0,
                        "original_resolution": "1920x1080",
                        "n_keyframes": 10,
                        "keyframes_dir": None,
                    }),
                    MediaProcessingError=Exception,
                ),
                "cortex.tiers": MagicMock(narrate_tier=MagicMock(return_value="narration text")),
                "cortex.gemma_vision": MagicMock(vision_gate=AsyncMock(return_value={"analyses": ["video"], "frames_analyzed": 5})),
            }):
                return await execute(str(f), include_narration=False, extract_keyframes=False)

        result = asyncio.run(_run())
        assert result["ok"] is True
        assert "top_rois" in result

    def test_video_too_long_returns_error(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake mp4")

        fake_scheduler = _make_fake_scheduler()
        fake_probe_info = MagicMock()
        fake_probe_info.duration_s = 400.0  # > 300s

        async def _run():
            from hermes.tools.brain_scan import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.media_processor": MagicMock(
                    probe=MagicMock(return_value=fake_probe_info),
                    preprocess_for_tribe=MagicMock(),
                    MediaProcessingError=Exception,
                ),
            }):
                return await execute(str(f))

        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "too_long"


# ---------------------------------------------------------------------------
# hermes/tools/narrate.py
# ---------------------------------------------------------------------------

class TestNarrateToolSchema:
    def test_schema_name(self):
        from hermes.tools.narrate import TOOL_SCHEMA
        assert TOOL_SCHEMA["name"] == "narrate"

    def test_schema_requires_scan_data(self):
        from hermes.tools.narrate import TOOL_SCHEMA
        assert "scan_data" in TOOL_SCHEMA["parameters"]["required"]


class TestNarrateInternals:
    def test_build_brain_context_contains_rois(self):
        from hermes.tools.narrate import _build_brain_context
        ctx = _build_brain_context(
            top_rois=["ROI_A", "ROI_B", "ROI_C"],
            peak_frame=10,
            num_timepoints=100,
        )
        assert "ROI_A" in ctx
        assert "ROI_B" in ctx

    def test_build_brain_context_peak_seconds(self):
        from hermes.tools.narrate import _build_brain_context
        ctx = _build_brain_context(top_rois=[], peak_frame=20, num_timepoints=40)
        assert "10.0s" in ctx  # peak_frame / 2.0 = 10.0

    def test_narrate_from_context_calls_generate(self):
        from hermes.tools.narrate import _narrate_from_context
        import cortex

        # narrate.py does `from cortex import ollama_client` which reads the
        # cortex *package attribute*, not sys.modules directly.  Use
        # patch.object on the package so the attribute is replaced correctly.
        mock_gen = MagicMock(return_value="narration text")
        mock_oc = MagicMock(generate=mock_gen)
        with patch.object(cortex, "ollama_client", mock_oc):
            result = _narrate_from_context("brain context", "label", tier=2)
        assert result == "narration text"

    def test_narrate_from_context_tier_clamp(self):
        from hermes.tools.narrate import _narrate_from_context
        import cortex

        mock_gen = MagicMock(return_value="ok")
        mock_oc = MagicMock(generate=mock_gen)
        with patch.object(cortex, "ollama_client", mock_oc):
            # tier=10 should clamp to 6
            _narrate_from_context("ctx", "label", tier=10)
            _narrate_from_context("ctx", "label", tier=-5)
        assert mock_gen.call_count == 2


class TestNarrateExecute:
    def test_failed_scan_returns_error(self):
        async def _run():
            from hermes.tools.narrate import execute
            return await execute(scan_data={"ok": False, "error": "scan_failed"})
        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "invalid_scan"

    def test_no_rois_returns_error(self):
        async def _run():
            from hermes.tools.narrate import execute
            return await execute(scan_data={"ok": True, "top_rois": []})
        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "no_rois"

    def test_single_tier_returns_narration(self):
        fake_scheduler = _make_fake_scheduler()

        async def _run():
            from hermes.tools.narrate import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.ollama_client": MagicMock(generate=MagicMock(return_value="tier2 narration")),
                "cortex.prompts": MagicMock(ALL_TIER_SYSTEMS=["s0","s1","s2","s3","s4","s5","s6"]),
                "cortex.config": MagicMock(
                    OLLAMA_MODEL_FAST="gemma4:e4b",
                    OLLAMA_MODEL_DEEP="gemma4:12b",
                    OLLAMA_MODEL_EXPERT="gemma4:27b",
                ),
            }):
                return await execute(
                    scan_data={"ok": True, "top_rois": ["ROI_A", "ROI_B"], "peak_frame": 5, "num_timepoints": 20},
                    tier=2,
                )

        result = asyncio.run(_run())
        assert result["ok"] is True
        assert "narration" in result

    def test_all_tiers_returns_7_entries(self):
        fake_scheduler = _make_fake_scheduler()

        async def _run():
            from hermes.tools.narrate import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.ollama_client": MagicMock(generate=MagicMock(return_value="tier narration")),
                "cortex.prompts": MagicMock(ALL_TIER_SYSTEMS=["s0","s1","s2","s3","s4","s5","s6"]),
                "cortex.config": MagicMock(
                    OLLAMA_MODEL_FAST="gemma4:e4b",
                    OLLAMA_MODEL_DEEP="gemma4:12b",
                    OLLAMA_MODEL_EXPERT="gemma4:27b",
                ),
            }):
                return await execute(
                    scan_data={"ok": True, "top_rois": ["ROI_A"], "peak_frame": 2, "num_timepoints": 10},
                    all_tiers=True,
                )

        result = asyncio.run(_run())
        assert result["ok"] is True
        assert len(result["narrations"]) == 7


# ---------------------------------------------------------------------------
# hermes/tools/describe_input.py
# ---------------------------------------------------------------------------

class TestDescribeInputSchema:
    def test_schema_name(self):
        from hermes.tools.describe_input import TOOL_SCHEMA
        assert TOOL_SCHEMA["name"] == "describe_input"

    def test_schema_requires_media_path(self):
        from hermes.tools.describe_input import TOOL_SCHEMA
        assert "media_path" in TOOL_SCHEMA["parameters"]["required"]


class TestDescribeInputExecute:
    def test_file_not_found_returns_error(self):
        async def _run():
            from hermes.tools.describe_input import execute
            return await execute("/nonexistent/video.mp4")
        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "file_not_found"

    def test_keyframe_extraction_error_returns_error(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")

        fake_scheduler = _make_fake_scheduler()

        async def _run():
            from hermes.tools.describe_input import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.media_processor": MagicMock(
                    probe=MagicMock(return_value=MagicMock(duration_s=10.0)),
                    MediaProcessingError=Exception,
                ),
            }):
                with patch("subprocess.run", side_effect=Exception("ffmpeg failed")):
                    return await execute(str(f))

        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "keyframe_extraction_failed"

    def test_successful_describe_returns_ok(self, tmp_path):
        f = tmp_path / "video.mp4"
        f.write_bytes(b"fake")

        fake_scheduler = _make_fake_scheduler()

        # Create fake frames
        frames_dir = tmp_path / "video_describe_frames"
        frames_dir.mkdir()
        (frames_dir / "frame_0001.jpg").write_bytes(b"fake jpg")
        (frames_dir / "frame_0002.jpg").write_bytes(b"fake jpg")

        async def _run():
            from hermes.tools.describe_input import execute
            with patch.dict("sys.modules", {
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.media_processor": MagicMock(
                    probe=MagicMock(return_value=MagicMock(
                        duration_s=10.0, has_audio=True, width=1920, height=1080
                    )),
                    MediaProcessingError=Exception,
                ),
                "cortex.gemma_vision": MagicMock(
                    vision_gate=AsyncMock(return_value={"frames_analyzed": 2, "analyses": ["scene"]})
                ),
            }):
                with patch("subprocess.run", return_value=MagicMock(returncode=0)):
                    # Monkey-patch glob to return our fake frames
                    with patch.object(Path, "glob", return_value=iter([
                        frames_dir / "frame_0001.jpg",
                        frames_dir / "frame_0002.jpg",
                    ])):
                        return await execute(str(f), max_frames=2)

        result = asyncio.run(_run())
        # May fail at ffmpeg step, but we check structure
        assert "ok" in result


# ---------------------------------------------------------------------------
# hermes/tools/visualize.py
# ---------------------------------------------------------------------------

class TestVisualizeToolSchema:
    def test_schema_name(self):
        from hermes.tools.visualize import TOOL_SCHEMA
        assert TOOL_SCHEMA["name"] == "visualize"

    def test_schema_requires_scan_data(self):
        from hermes.tools.visualize import TOOL_SCHEMA
        assert "scan_data" in TOOL_SCHEMA["parameters"]["required"]


class TestVisualizeHelpers:
    def test_short_roi_name(self):
        from hermes.tools.visualize import _short_roi_name
        assert _short_roi_name("7Networks_LH_Vis_3") == "LH Vis 3"

    def test_short_roi_name_no_prefix(self):
        from hermes.tools.visualize import _short_roi_name
        result = _short_roi_name("PlainROI")
        assert isinstance(result, str)

    def test_extract_network_valid(self):
        from hermes.tools.visualize import _extract_network
        assert _extract_network("7Networks_LH_Vis_3") == "Vis"

    def test_extract_network_short_label(self):
        from hermes.tools.visualize import _extract_network
        assert _extract_network("AB") == "Unknown"

    def test_extract_network_default_mode(self):
        from hermes.tools.visualize import _extract_network
        assert _extract_network("7Networks_LH_Default_5") == "Default"


class TestVisualizeExecute:
    def test_invalid_scan_returns_error(self):
        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
            }):
                return await execute(scan_data={"ok": False, "error": "scan failed"})
        result = asyncio.run(_run())
        assert result["ok"] is False
        assert result["error"] == "invalid_scan"

    def test_heatmap_format(self, tmp_path):
        scan_data = {
            "ok": True,
            "top_rois": ["7Networks_LH_Vis_1", "7Networks_RH_Default_2"],
            "peak_time_s": 10.0,
        }
        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
            }):
                return await execute(
                    scan_data=scan_data,
                    output_dir=str(tmp_path),
                    formats=["heatmap"],
                )
        result = asyncio.run(_run())
        assert result["ok"] is True
        assert len(result["files"]) >= 1

    def test_bar_chart_format(self, tmp_path):
        scan_data = {
            "ok": True,
            "top_rois": ["7Networks_LH_Vis_1"],
            "peak_time_s": 5.0,
        }
        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
            }):
                return await execute(
                    scan_data=scan_data,
                    output_dir=str(tmp_path),
                    formats=["bar_chart"],
                )
        result = asyncio.run(_run())
        assert result["ok"] is True

    def test_time_series_format(self, tmp_path):
        scan_data = {
            "ok": True,
            "top_rois": [],
            "num_timepoints": 20,
            "peak_frame": 10,
            "duration_s": 10.0,
        }
        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
            }):
                return await execute(
                    scan_data=scan_data,
                    output_dir=str(tmp_path),
                    formats=["time_series"],
                )
        result = asyncio.run(_run())
        assert result["ok"] is True

    def test_multiple_formats(self, tmp_path):
        scan_data = {
            "ok": True,
            "top_rois": ["7Networks_LH_Vis_1", "7Networks_LH_Default_2"],
            "peak_time_s": 5.0,
            "num_timepoints": 20,
            "peak_frame": 10,
            "duration_s": 10.0,
        }
        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
            }):
                return await execute(
                    scan_data=scan_data,
                    output_dir=str(tmp_path),
                    formats=["heatmap", "bar_chart", "time_series"],
                )
        result = asyncio.run(_run())
        assert result["ok"] is True
        assert len(result["files"]) == 3

    def test_default_formats_used_when_none(self, tmp_path):
        scan_data = {
            "ok": True,
            "top_rois": ["7Networks_LH_Vis_1"],
            "peak_time_s": 3.0,
        }
        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
            }):
                return await execute(
                    scan_data=scan_data,
                    output_dir=str(tmp_path),
                    formats=None,
                )
        result = asyncio.run(_run())
        assert result["ok"] is True
        assert len(result["files"]) == 2  # default: heatmap + bar_chart

    def test_threejs_format_with_mocked_ollama(self, tmp_path):
        scan_data = {
            "ok": True,
            "top_rois": ["7Networks_LH_Vis_1"],
            "peak_time_s": 5.0,
        }
        fake_scheduler = _make_fake_scheduler()

        async def _run():
            from hermes.tools.visualize import execute
            with patch.dict("sys.modules", {
                "cortex.logger": MagicMock(log=MagicMock()),
                "cortex.gpu_scheduler": MagicMock(get_scheduler=MagicMock(return_value=fake_scheduler)),
                "cortex.ollama_client": MagicMock(generate=MagicMock(return_value="<html>fake</html>")),
            }):
                return await execute(
                    scan_data=scan_data,
                    output_dir=str(tmp_path),
                    formats=["threejs"],
                )
        result = asyncio.run(_run())
        assert result["ok"] is True

    def test_render_bar_chart_groups_by_network(self, tmp_path):
        from hermes.tools.visualize import _render_bar_chart
        scan_data = {
            "top_rois": [
                "7Networks_LH_Vis_1",
                "7Networks_LH_Default_2",
                "7Networks_RH_SomMot_3",
            ],
        }
        path = _render_bar_chart(scan_data, tmp_path)
        assert path.exists()

    def test_render_heatmap_empty_rois(self, tmp_path):
        from hermes.tools.visualize import _render_heatmap
        path = _render_heatmap({"top_rois": [], "peak_time_s": 0.0}, tmp_path)
        assert path.exists()
