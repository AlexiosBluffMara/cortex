"""Tests for cortex.gemma_vision and cortex.manim_brain."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Restore the real cortex.gemma_vision before each vision test.
# test_media_gate.py (collected later alphabetically) contaminates
# sys.modules["cortex.gemma_vision"] AND cortex.gemma_vision package attr
# at collection time.  This fixture undoes that so local imports work.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_real_gemma_vision():
    import cortex
    sys.modules.pop("cortex.gemma_vision", None)
    if hasattr(cortex, "gemma_vision"):
        delattr(cortex, "gemma_vision")
    yield
    # Leave the restored real module in place; no teardown needed.


# ===========================================================================
# cortex.gemma_vision
# ===========================================================================

class TestRequireFfmpeg:
    def test_returns_path_when_found(self):
        from cortex.gemma_vision import _require_ffmpeg
        with patch("shutil.which", return_value="/usr/bin/ffmpeg"):
            result = _require_ffmpeg()
        assert result == "/usr/bin/ffmpeg"

    def test_exits_when_not_found(self):
        from cortex.gemma_vision import _require_ffmpeg
        with patch("shutil.which", return_value=None):
            with pytest.raises(SystemExit):
                _require_ffmpeg()


class TestProbeDuration:
    def test_returns_20_when_ffprobe_missing(self):
        from cortex.gemma_vision import _probe_duration
        with patch("shutil.which", return_value=None):
            result = _probe_duration(Path("/tmp/fake.mp4"))
        assert result == 20.0

    def test_returns_20_on_check_output_error(self):
        from cortex.gemma_vision import _probe_duration
        import subprocess
        with patch("shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("subprocess.check_output", side_effect=subprocess.CalledProcessError(1, "ffprobe")):
            result = _probe_duration(Path("/tmp/fake.mp4"))
        assert result == 20.0

    def test_returns_parsed_duration(self):
        from cortex.gemma_vision import _probe_duration
        with patch("shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("subprocess.check_output", return_value="42.5\n"):
            result = _probe_duration(Path("/tmp/video.mp4"))
        assert result == pytest.approx(42.5)

    def test_returns_20_on_value_error(self):
        from cortex.gemma_vision import _probe_duration
        with patch("shutil.which", return_value="/usr/bin/ffprobe"), \
             patch("subprocess.check_output", return_value="not_a_number\n"):
            result = _probe_duration(Path("/tmp/video.mp4"))
        assert result == 20.0


class TestExtractKeyframes:
    def test_returns_empty_on_failed_frames(self, tmp_path, monkeypatch):
        from cortex.gemma_vision import extract_keyframes
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        fake_result = MagicMock()
        fake_result.returncode = 1  # failure
        fake_result.stderr = b"error"

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=fake_result), \
             patch("cortex.gemma_vision._probe_duration", return_value=20.0):
            frames = extract_keyframes(Path("/tmp/video.mp4"), n=3, out_dir=tmp_path)

        assert frames == []

    def test_returns_created_files(self, tmp_path):
        from cortex.gemma_vision import extract_keyframes

        def _fake_run(cmd, **kwargs):
            # Create the output file to simulate ffmpeg success
            out_file = Path(cmd[-1])
            out_file.touch()
            result = MagicMock()
            result.returncode = 0
            result.stderr = b""
            return result

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", side_effect=_fake_run), \
             patch("cortex.gemma_vision._probe_duration", return_value=10.0):
            frames = extract_keyframes(Path("/tmp/video.mp4"), n=2, out_dir=tmp_path)

        assert len(frames) == 2
        for f in frames:
            assert isinstance(f, Path)


class TestExtractAudioSegment:
    def test_returns_none_on_ffmpeg_failure(self, tmp_path, monkeypatch):
        from cortex.gemma_vision import extract_audio_segment
        monkeypatch.setattr("cortex.config.OUT_DIR", tmp_path)

        fake_result = MagicMock()
        fake_result.returncode = 1

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", return_value=fake_result):
            result = extract_audio_segment(Path("/tmp/audio.mp4"), out_dir=tmp_path)

        assert result is None

    def test_returns_path_when_successful(self, tmp_path):
        from cortex.gemma_vision import extract_audio_segment

        def _fake_run(cmd, **kwargs):
            # Create the output WAV file with sufficient size
            out_wav = Path(cmd[-1])
            out_wav.write_bytes(b"RIFF" + b"\x00" * 2000)
            result = MagicMock()
            result.returncode = 0
            return result

        with patch("shutil.which", return_value="/usr/bin/ffmpeg"), \
             patch("subprocess.run", side_effect=_fake_run):
            result = extract_audio_segment(Path("/tmp/audio.mp4"), out_dir=tmp_path)

        assert result is not None
        assert result.suffix == ".wav"


class TestTranscribeAudio:
    def test_returns_empty_string_on_exception(self, tmp_path):
        from cortex.gemma_vision import transcribe_audio
        # Reset whisper pipe
        import cortex.gemma_vision as gv
        gv._whisper_pipe = None

        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"fake")

        with patch.dict("sys.modules", {"transformers": None, "torch": None}):
            # Will fail on import
            result = transcribe_audio(wav)
        assert result == ""

    def test_uses_existing_pipe(self, tmp_path):
        from cortex.gemma_vision import transcribe_audio
        import cortex.gemma_vision as gv

        fake_pipe = MagicMock(return_value={"text": "Hello world"})
        gv._whisper_pipe = fake_pipe

        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"fake")
        result = transcribe_audio(wav)
        assert result == "Hello world"
        # Reset
        gv._whisper_pipe = None

    def test_truncates_at_max_chars(self, tmp_path):
        from cortex.gemma_vision import transcribe_audio
        import cortex.gemma_vision as gv

        long_text = "A" * 1000
        fake_pipe = MagicMock(return_value={"text": long_text})
        gv._whisper_pipe = fake_pipe

        wav = tmp_path / "audio.wav"
        wav.write_bytes(b"fake")
        result = transcribe_audio(wav, max_chars=100)
        assert len(result) <= 100
        gv._whisper_pipe = None


# ===========================================================================
# cortex.manim_brain
# ===========================================================================

class TestYeo7Constants:
    def test_yeo7_has_7_networks(self):
        from cortex.manim_brain import YEO7
        assert len(YEO7) == 7

    def test_yeo7_has_expected_keys(self):
        from cortex.manim_brain import YEO7
        assert "Vis" in YEO7
        assert "Default" in YEO7
        assert "SomMot" in YEO7

    def test_yeo7_func_has_7_entries(self):
        from cortex.manim_brain import YEO7_FUNC
        assert len(YEO7_FUNC) == 7


class TestBoldToNetworkTraces:
    def test_returns_7_networks(self):
        from cortex.manim_brain import bold_to_network_traces
        bold = np.random.randn(10, 20484).astype(np.float32)
        traces = bold_to_network_traces(bold)
        assert len(traces) == 7

    def test_traces_have_correct_length(self):
        from cortex.manim_brain import bold_to_network_traces
        T = 15
        bold = np.random.randn(T, 20484).astype(np.float32)
        traces = bold_to_network_traces(bold)
        for key, arr in traces.items():
            assert len(arr) == T

    def test_works_with_400_regions(self):
        from cortex.manim_brain import bold_to_network_traces
        bold = np.random.randn(10, 400).astype(np.float32)
        traces = bold_to_network_traces(bold, n_regions=400)
        assert len(traces) == 7

    def test_works_with_fewer_regions(self):
        from cortex.manim_brain import bold_to_network_traces
        # 400 vertices is "fewer than 20484" but large enough that none of the
        # Yeo-7 network slice boundaries produce an empty sub-array (which would
        # raise RuntimeWarning treated as error in this test suite).
        bold = np.random.randn(10, 400).astype(np.float32)
        traces = bold_to_network_traces(bold)
        assert len(traces) > 0

    def test_network_keys_in_yeo7(self):
        from cortex.manim_brain import bold_to_network_traces, YEO7
        bold = np.random.randn(5, 20484).astype(np.float32)
        traces = bold_to_network_traces(bold)
        for key in traces:
            assert key in YEO7


class TestBuildManinScript:
    def test_creates_py_file(self, tmp_path):
        from cortex.manim_brain import _build_manim_script
        bold = np.random.randn(5, 20484).astype(np.float32)
        script_path = _build_manim_script(bold, peak_t=2, out_dir=tmp_path)
        assert script_path.exists()
        assert script_path.suffix == ".py"

    def test_script_contains_trace_data(self, tmp_path):
        from cortex.manim_brain import _build_manim_script
        bold = np.ones((5, 20484), dtype=np.float32)
        script_path = _build_manim_script(bold, peak_t=2, out_dir=tmp_path)
        content = script_path.read_text()
        assert '"Vis"' in content

    def test_script_no_peak_t(self, tmp_path):
        from cortex.manim_brain import _build_manim_script
        bold = np.random.randn(5, 20484).astype(np.float32)
        script_path = _build_manim_script(bold, peak_t=None, out_dir=tmp_path)
        content = script_path.read_text()
        assert "None" in content


class TestRenderBoldExplainer:
    def test_missing_npy_returns_none(self, tmp_path):
        from cortex.manim_brain import render_bold_explainer
        result = render_bold_explainer(
            bold_npy=tmp_path / "nonexistent.npy",
            output_dir=tmp_path,
        )
        assert result is None

    def test_manim_failure_returns_none(self, tmp_path):
        from cortex.manim_brain import render_bold_explainer
        bold = np.random.randn(5, 20484).astype(np.float32)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = "Error"

        with patch("subprocess.run", return_value=mock_result):
            result = render_bold_explainer(bold_npy=bold_path, output_dir=tmp_path)
        assert result is None

    def test_manim_file_not_found_returns_none(self, tmp_path):
        from cortex.manim_brain import render_bold_explainer
        bold = np.random.randn(5, 20484).astype(np.float32)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = render_bold_explainer(bold_npy=bold_path, output_dir=tmp_path)
        assert result is None

    def test_manim_timeout_returns_none(self, tmp_path):
        import subprocess
        from cortex.manim_brain import render_bold_explainer
        bold = np.random.randn(5, 20484).astype(np.float32)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("manim", 300)):
            result = render_bold_explainer(bold_npy=bold_path, output_dir=tmp_path)
        assert result is None

    def test_manim_success_finds_output(self, tmp_path):
        from cortex.manim_brain import render_bold_explainer
        bold = np.random.randn(5, 20484).astype(np.float32)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        # Create the expected output file
        expected_dir = tmp_path / "videos" / "manim_bold_scene" / "l480p15"
        expected_dir.mkdir(parents=True)
        expected_mp4 = expected_dir / "BoldTimeseries.mp4"
        expected_mp4.write_bytes(b"fake mp4")

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = render_bold_explainer(
                bold_npy=bold_path,
                output_dir=tmp_path,
                scene="BoldTimeseries",
            )
        assert result == expected_mp4

    def test_manim_returns_none_when_output_missing(self, tmp_path):
        from cortex.manim_brain import render_bold_explainer
        bold = np.random.randn(5, 20484).astype(np.float32)
        bold_path = tmp_path / "bold.npy"
        np.save(bold_path, bold)

        mock_result = MagicMock()
        mock_result.returncode = 0

        with patch("subprocess.run", return_value=mock_result):
            result = render_bold_explainer(bold_npy=bold_path, output_dir=tmp_path)
        assert result is None  # output dir was created but no mp4 file


class TestManinSceneStringContents:
    def test_manim_scenes_string_contains_class_names(self):
        from cortex.manim_brain import MANIM_SCENES
        assert "BoldTimeseries" in MANIM_SCENES
        assert "BrainNetworkDiagram" in MANIM_SCENES

    def test_manim_scenes_has_placeholders(self):
        from cortex.manim_brain import MANIM_SCENES
        assert "{NET_TRACES_PLACEHOLDER}" in MANIM_SCENES
        # PEAK_T is written as a bare assignment: PEAK_T = PEAK_T_PLACEHOLDER (no braces)
        assert "PEAK_T_PLACEHOLDER" in MANIM_SCENES
        assert "{PEAK_ACT_PLACEHOLDER}" in MANIM_SCENES
