"""Extended coverage for cortex.media_processor.

Covers: _run_ffmpeg error paths, preprocess_for_tribe trim/audio,
audio-only preprocess, A/V sync check, preprocess_for_web, extract_thumbnail.
"""
from __future__ import annotations

import json
import subprocess as _subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# _run_ffmpeg error paths
# ---------------------------------------------------------------------------

class TestRunFfmpegErrors:
    def test_called_process_error_raises_media_error(self, tmp_path):
        from cortex.media_processor import MediaProcessingError, _run_ffmpeg

        def _raise(*args, **kwargs):
            exc = _subprocess.CalledProcessError(1, ["ffmpeg"], stderr="codec error")
            raise exc

        with patch("subprocess.run", side_effect=_raise):
            with pytest.raises(MediaProcessingError, match="FFmpeg error"):
                _run_ffmpeg(["ffmpeg", "-y", "-i", "x.mp4", "out.mp4"])

    def test_timeout_raises_media_error(self):
        from cortex.media_processor import MediaProcessingError, _run_ffmpeg

        def _timeout(*args, **kwargs):
            raise _subprocess.TimeoutExpired(["ffmpeg"], 300)

        with patch("subprocess.run", side_effect=_timeout):
            with pytest.raises(MediaProcessingError, match="timeout"):
                _run_ffmpeg(["ffmpeg", "-y", "-i", "x.mp4", "out.mp4"], timeout=300)

    def test_file_not_found_raises_media_error(self):
        from cortex.media_processor import MediaProcessingError, _run_ffmpeg

        with patch("subprocess.run", side_effect=FileNotFoundError("ffmpeg not found")):
            with pytest.raises(MediaProcessingError, match="FFmpeg not found"):
                _run_ffmpeg(["ffmpeg"])

    def test_success_returns_completed_process(self):
        from cortex.media_processor import _run_ffmpeg

        fake_result = MagicMock(spec=_subprocess.CompletedProcess)
        with patch("subprocess.run", return_value=fake_result):
            result = _run_ffmpeg(["ffmpeg", "-version"])

        assert result is fake_result


# ---------------------------------------------------------------------------
# preprocess_for_tribe — additional paths
# ---------------------------------------------------------------------------

def _make_media_info(
    *,
    duration_s=10.0,
    width=1920,
    height=1080,
    has_audio=True,
    fps=30.0,
):
    from cortex.media_processor import MediaInfo
    return MediaInfo(
        duration_s=duration_s,
        width=width,
        height=height,
        fps=fps,
        has_audio=has_audio,
        codec="h264",
        file_size_mb=5.0,
        audio_codec="aac" if has_audio else "",
        audio_sample_rate=48000 if has_audio else 0,
    )


class TestPreprocessForTribeExtended:
    def _make_run_ffmpeg(self, tmp_path, capture: list):
        """Factory for a fake _run_ffmpeg that creates dummy output files."""
        def _fake(cmd, timeout=300):
            capture.append(cmd)
            for tok in cmd:
                if tok.endswith("_tribe.mp4"):
                    Path(tok).write_bytes(b"\x00" * 1024)
                elif tok.endswith("_tribe.wav"):
                    Path(tok).write_bytes(b"\x00" * 1024)
                elif tok.endswith(".jpg"):
                    kf_dir = Path(tok).parent
                    kf_dir.mkdir(parents=True, exist_ok=True)
                    (kf_dir / "frame_0001.jpg").write_bytes(b"\x00")
            return MagicMock()
        return _fake

    def test_trim_args_when_duration_exceeds_max(self, tmp_path):
        """When duration > max_duration_s, trim args are included in video cmd."""
        from cortex import media_processor as mp

        src = tmp_path / "long.mp4"
        src.write_bytes(b"\x00")
        captured: list = []

        # Duration 120s > max_duration_s 50s → trim applied
        info = _make_media_info(duration_s=120.0, has_audio=False)
        with patch.object(mp, "probe", return_value=info), \
             patch.object(mp, "_run_ffmpeg", side_effect=self._make_run_ffmpeg(tmp_path, captured)):
            result = mp.preprocess_for_tribe(src, tmp_path / "out", max_duration_s=50.0)

        video_cmd = captured[0]
        assert "-ss" in video_cmd
        assert "-to" in video_cmd

    def test_audio_extraction_command_issued(self, tmp_path):
        """When has_audio=True, an audio extraction command is generated."""
        from cortex import media_processor as mp

        src = tmp_path / "video_with_audio.mp4"
        src.write_bytes(b"\x00")
        captured: list = []

        info = _make_media_info(duration_s=10.0, has_audio=True)
        with patch.object(mp, "probe", return_value=info), \
             patch.object(mp, "_run_ffmpeg", side_effect=self._make_run_ffmpeg(tmp_path, captured)):
            result = mp.preprocess_for_tribe(src, tmp_path / "out")

        # Should have a command with pcm_s16le (audio extraction)
        audio_cmds = [c for c in captured if "pcm_s16le" in c]
        assert len(audio_cmds) == 1
        assert result["audio"] is not None

    def test_audio_only_no_video_stream(self, tmp_path):
        """Audio-only file (width=0) skips video + keyframe commands."""
        from cortex import media_processor as mp

        src = tmp_path / "audio_only.mp3"
        src.write_bytes(b"\x00")
        captured: list = []

        # width=0 → no video stream
        info = _make_media_info(duration_s=10.0, width=0, height=0, has_audio=True)

        def _fake_run(cmd, timeout=300):
            captured.append(cmd)
            for tok in cmd:
                if tok.endswith("_tribe.wav"):
                    Path(tok).write_bytes(b"\x00" * 1024)
            return MagicMock()

        with patch.object(mp, "probe", return_value=info), \
             patch.object(mp, "_run_ffmpeg", side_effect=_fake_run):
            result = mp.preprocess_for_tribe(src, tmp_path / "out")

        assert result["video"] is None
        assert result["audio"] is not None
        assert result["keyframes_dir"] is None

    def test_av_sync_check_runs_when_both_present(self, tmp_path):
        """A/V sync check executes when both video and audio are produced."""
        from cortex import media_processor as mp

        src = tmp_path / "sync_test.mp4"
        src.write_bytes(b"\x00")
        captured: list = []

        info = _make_media_info(duration_s=10.0, has_audio=True)

        def _fake_run(cmd, timeout=300):
            captured.append(cmd)
            for tok in cmd:
                if tok.endswith("_tribe.mp4"):
                    Path(tok).write_bytes(b"\x00" * 1024)
                elif tok.endswith("_tribe.wav"):
                    Path(tok).write_bytes(b"\x00" * 1024)
                elif tok.endswith(".jpg"):
                    d = Path(tok).parent
                    d.mkdir(parents=True, exist_ok=True)
                    (d / "frame_0001.jpg").write_bytes(b"\x00")
            return MagicMock()

        # probe is called again for the A/V sync check (on video_out)
        video_info = _make_media_info(duration_s=10.0, has_audio=False)
        audio_probe_response = json.dumps({"format": {"duration": "10.0"}})
        probe_calls = [0]

        def _fake_probe(path):
            probe_calls[0] += 1
            return info if probe_calls[0] == 1 else video_info

        def _fake_subprocess_run(cmd, **kwargs):
            r = MagicMock()
            r.stdout = audio_probe_response
            return r

        with patch.object(mp, "probe", side_effect=_fake_probe), \
             patch.object(mp, "_run_ffmpeg", side_effect=_fake_run), \
             patch("subprocess.run", side_effect=_fake_subprocess_run):
            result = mp.preprocess_for_tribe(src, tmp_path / "out")

        assert "av_drift_s" in result


# ---------------------------------------------------------------------------
# preprocess_for_web
# ---------------------------------------------------------------------------

class TestPreprocessForWeb:
    def test_emits_libx264_command(self, tmp_path):
        from cortex import media_processor as mp

        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00")

        captured: list = []

        def _fake_run(cmd, timeout=300):
            captured.append(cmd)
            output_path = cmd[-1]
            Path(output_path).write_bytes(b"\x00" * 1024)
            return MagicMock()

        with patch.object(mp, "_run_ffmpeg", side_effect=_fake_run):
            out = mp.preprocess_for_web(src, tmp_path / "web")

        cmd = captured[0]
        assert "libx264" in cmd
        assert "23" in cmd  # CRF
        assert "720" in " ".join(cmd)  # scale to 720p
        assert isinstance(out, Path)

    def test_returns_path(self, tmp_path):
        from cortex import media_processor as mp

        src = tmp_path / "vid.mp4"
        src.write_bytes(b"\x00")

        def _fake_run(cmd, timeout=300):
            Path(cmd[-1]).write_bytes(b"\x00" * 512)
            return MagicMock()

        with patch.object(mp, "_run_ffmpeg", side_effect=_fake_run):
            out = mp.preprocess_for_web(src, tmp_path / "out")

        assert out.name.endswith("_web.mp4")


# ---------------------------------------------------------------------------
# extract_thumbnail
# ---------------------------------------------------------------------------

class TestExtractThumbnail:
    def test_emits_vframes_command(self, tmp_path):
        from cortex import media_processor as mp

        src = tmp_path / "vid.mp4"
        src.write_bytes(b"\x00")
        captured: list = []

        def _fake_run(cmd, timeout=300):
            captured.append(cmd)
            Path(cmd[-1]).write_bytes(b"\x00" * 100)
            return MagicMock()

        with patch.object(mp, "_run_ffmpeg", side_effect=_fake_run):
            out = mp.extract_thumbnail(src, tmp_path / "thumbs", time_s=3.0)

        cmd = captured[0]
        assert "-vframes" in cmd
        assert "1" in cmd
        assert "3.0" in cmd  # time_s
        assert isinstance(out, Path)

    def test_returns_jpg_path(self, tmp_path):
        from cortex import media_processor as mp

        src = tmp_path / "clip.mp4"
        src.write_bytes(b"\x00")

        def _fake_run(cmd, timeout=300):
            Path(cmd[-1]).write_bytes(b"\x00")
            return MagicMock()

        with patch.object(mp, "_run_ffmpeg", side_effect=_fake_run):
            out = mp.extract_thumbnail(src, tmp_path / "thumbs")

        assert out.suffix == ".jpg"
        assert "_thumb" in out.name
