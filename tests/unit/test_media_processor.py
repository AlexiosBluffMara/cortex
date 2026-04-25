"""Tests for cortex.media_processor — FFmpeg/FFprobe wrappers (mocked)."""
from __future__ import annotations

import json
import subprocess as _subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cortex.media_processor import (
    MediaInfo,
    MediaProcessingError,
    probe,
)

pytestmark = pytest.mark.unit


def _ffprobe_response(
    *,
    duration: float = 12.5,
    width: int = 1920,
    height: int = 1080,
    fps_str: str = "30000/1001",
    codec: str = "h264",
    has_audio: bool = True,
    audio_codec: str = "aac",
    audio_sample_rate: int = 48000,
    size_bytes: int = 5 * 1024 * 1024,
) -> str:
    streams = [
        {
            "codec_type": "video",
            "codec_name": codec,
            "width": width,
            "height": height,
            "r_frame_rate": fps_str,
        }
    ]
    if has_audio:
        streams.append(
            {
                "codec_type": "audio",
                "codec_name": audio_codec,
                "sample_rate": str(audio_sample_rate),
            }
        )
    return json.dumps({"format": {"duration": str(duration), "size": str(size_bytes)}, "streams": streams})


class TestProbe:
    def test_parses_typical_video(self, tmp_path, monkeypatch):
        path = tmp_path / "clip.mp4"
        path.write_bytes(b"\x00")
        called = []

        def _fake_run(cmd, **kwargs):
            called.append(cmd)
            r = MagicMock()
            r.stdout = _ffprobe_response()
            r.returncode = 0
            return r

        monkeypatch.setattr("subprocess.run", _fake_run)
        info = probe(path)

        assert isinstance(info, MediaInfo)
        assert info.duration_s == 12.5
        assert info.width == 1920
        assert info.height == 1080
        assert info.fps == pytest.approx(30000 / 1001, abs=0.01)
        assert info.has_audio is True
        assert info.codec == "h264"
        assert info.audio_codec == "aac"
        assert info.audio_sample_rate == 48000
        assert called[0][0] == "ffprobe"

    def test_handles_no_audio_stream(self, tmp_path, monkeypatch):
        path = tmp_path / "silent.mp4"
        path.write_bytes(b"\x00")
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: MagicMock(stdout=_ffprobe_response(has_audio=False), returncode=0),
        )
        info = probe(path)
        assert info.has_audio is False
        assert info.audio_codec == ""
        assert info.audio_sample_rate == 0

    def test_handles_division_by_zero_in_fps(self, tmp_path, monkeypatch):
        path = tmp_path / "weird.mp4"
        path.write_bytes(b"\x00")
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: MagicMock(stdout=_ffprobe_response(fps_str="0/0"), returncode=0),
        )
        info = probe(path)
        assert info.fps == 30.0  # fallback default

    def test_missing_file_raises_filenotfound(self, tmp_path):
        # probe() does its own existence check before calling FFprobe.
        with pytest.raises(FileNotFoundError):
            probe(tmp_path / "does_not_exist.mp4")

    def test_ffprobe_bad_json_raises_media_error(self, tmp_path, monkeypatch):
        path = tmp_path / "broken.mp4"
        path.write_bytes(b"\x00")
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: MagicMock(stdout="not json", returncode=0),
        )
        with pytest.raises(MediaProcessingError):
            probe(path)

    def test_ffprobe_timeout_raises_media_error(self, tmp_path, monkeypatch):
        path = tmp_path / "slow.mp4"
        path.write_bytes(b"\x00")

        def _timeout(cmd, **kwargs):
            raise _subprocess.TimeoutExpired(cmd, 30)

        monkeypatch.setattr("subprocess.run", _timeout)
        with pytest.raises(MediaProcessingError):
            probe(path)

    def test_ffprobe_no_format_raises(self, tmp_path, monkeypatch):
        path = tmp_path / "weird.mp4"
        path.write_bytes(b"\x00")
        monkeypatch.setattr(
            "subprocess.run",
            lambda cmd, **kw: MagicMock(stdout='{"streams": []}', returncode=0),
        )
        with pytest.raises(MediaProcessingError):
            probe(path)


class TestMediaInfoStruct:
    def test_default_audio_fields(self):
        info = MediaInfo(
            duration_s=10.0,
            width=640,
            height=480,
            fps=30.0,
            has_audio=False,
            codec="h264",
            file_size_mb=1.0,
        )
        assert info.audio_codec == ""
        assert info.audio_sample_rate == 0


class TestPreprocessForTribe:
    """High-level test that preprocess_for_tribe wires FFmpeg correctly.

    We mock _run_ffmpeg so no real ffmpeg invocation happens, then verify
    the produced commands have the right flags for V-JEPA2 / wav2vec-BERT.
    """

    def test_emits_libx265_h265_video_command(self, tmp_path, monkeypatch):
        from cortex import media_processor as mp

        # Stage: a fake input file
        src = tmp_path / "input.mp4"
        src.write_bytes(b"\x00")
        out_dir = tmp_path / "out"

        # Probe returns a video-only file (no audio) so we don't generate audio.wav
        monkeypatch.setattr(
            mp,
            "probe",
            lambda p: MediaInfo(
                duration_s=10.0, width=1280, height=720, fps=30.0,
                has_audio=False, codec="h264", file_size_mb=2.0,
            ),
        )

        captured: list[list[str]] = []

        def _fake_run(cmd, timeout=300):
            captured.append(cmd)
            # Create the would-be output to satisfy stat() in the function
            for tok in cmd:
                if tok.endswith("_tribe.mp4"):
                    Path(tok).write_bytes(b"\x00" * (1024 * 1024))
                if tok.endswith("frame_%04d.jpg"):
                    Path(tok).parent.mkdir(parents=True, exist_ok=True)
                    (Path(tok).parent / "frame_0001.jpg").write_bytes(b"\x00")
            return MagicMock()

        monkeypatch.setattr(mp, "_run_ffmpeg", _fake_run)

        result = mp.preprocess_for_tribe(src, out_dir)

        # Video pipeline used libx265 + CRF 6 + 224x224 + 30fps
        video_cmd = captured[0]
        assert video_cmd[0] == "ffmpeg"
        assert "libx265" in video_cmd
        assert "6" in video_cmd  # CRF
        assert any("scale=224:224" in tok for tok in video_cmd)
        assert any("fps=30" in tok for tok in video_cmd)

        # No audio command since has_audio=False
        # But there's a keyframe command at 1fps with 768px scale
        keyframe_cmd = captured[-1]
        assert any("fps=1" in tok for tok in keyframe_cmd)
        assert any("scale=768:768" in tok for tok in keyframe_cmd)

        assert result["video"] is not None
        assert result["audio"] is None
        assert result["keyframes_dir"] is not None
