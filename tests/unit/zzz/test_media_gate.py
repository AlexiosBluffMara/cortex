"""Tests for cortex.media_gate — Gemma vision media classification."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Patch strategy:
#   cortex, cortex.config, cortex.prompts  — use the REAL modules.
#   cortex.ollama_client                   — replaced with a fake (no real server).
#   cortex.gemma_vision                    — replaced with a fake (no ffmpeg/whisper).
# ---------------------------------------------------------------------------

import cortex  # real package

# cortex.ollama_client stub
_fake_oc = types.ModuleType("cortex.ollama_client")
_fake_oc.generate_json = MagicMock(return_value={})
sys.modules["cortex.ollama_client"] = _fake_oc
cortex.ollama_client = _fake_oc  # type: ignore[attr-defined]

# cortex.gemma_vision stub — only the 4 symbols media_gate.py imports
_fake_gv = types.ModuleType("cortex.gemma_vision")
_fake_gv._probe_duration       = MagicMock(return_value=20.0)
_fake_gv.extract_keyframes     = MagicMock(return_value=[])
_fake_gv.extract_audio_segment = MagicMock(return_value=None)
_fake_gv.transcribe_audio      = MagicMock(return_value="")
sys.modules["cortex.gemma_vision"] = _fake_gv
cortex.gemma_vision = _fake_gv  # type: ignore[attr-defined]

# Now safe to import the module under test
from cortex.media_gate import DEFAULT, MediaDescription, classify, classify_image  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture data
# ---------------------------------------------------------------------------

_FULL_DICT = {
    "content_type": "video",
    "subject":      "a person walking",
    "setting":      "city street",
    "action":       "walking forward",
    "mood":         "calm",
    "modality":     "visual",
    "description":  "A person walks down a busy city street in daylight.",
}


def _reset():
    _fake_oc.generate_json.reset_mock()
    _fake_oc.generate_json.return_value = _FULL_DICT.copy()

    _fake_gv._probe_duration.reset_mock()
    _fake_gv._probe_duration.return_value = 20.0
    _fake_gv.extract_keyframes.reset_mock()
    _fake_gv.extract_keyframes.return_value = []
    _fake_gv.extract_audio_segment.reset_mock()
    _fake_gv.extract_audio_segment.return_value = None
    _fake_gv.transcribe_audio.reset_mock()
    _fake_gv.transcribe_audio.return_value = ""


# ---------------------------------------------------------------------------
# MediaDescription dataclass
# ---------------------------------------------------------------------------

class TestMediaDescription:
    def test_short_description_uses_description_field_when_present(self):
        md = MediaDescription(**_FULL_DICT)
        assert md.short_description() == _FULL_DICT["description"].strip()

    def test_short_description_fallback_to_subject_parts(self):
        md = MediaDescription(
            content_type="video", subject="a dog", setting="park",
            action="running", mood="energetic", modality="visual", description="",
        )
        out = md.short_description()
        assert "a dog" in out
        assert "park" in out
        assert "running" in out

    def test_short_description_omits_neutral_mood(self):
        md = MediaDescription(
            content_type="video", subject="a cat", setting="room",
            action="sitting", mood="neutral", modality="visual", description="",
        )
        assert "neutral" not in md.short_description()

    def test_short_description_includes_non_neutral_mood(self):
        md = MediaDescription(
            content_type="video", subject="crowd", setting="stadium",
            action="cheering", mood="energetic", modality="visual", description="",
        )
        assert "energetic" in md.short_description()

    def test_summary_line_contains_content_type(self):
        md = MediaDescription(**_FULL_DICT)
        assert "video" in md.summary_line()

    def test_summary_line_contains_subject(self):
        md = MediaDescription(**_FULL_DICT)
        assert "a person walking" in md.summary_line()

    def test_summary_line_contains_modality(self):
        md = MediaDescription(**_FULL_DICT)
        assert "visual" in md.summary_line()

    def test_summary_line_uses_of_separator(self):
        md = MediaDescription(**_FULL_DICT)
        assert " of " in md.summary_line()

    def test_frames_default_empty(self):
        md = MediaDescription(**_FULL_DICT)
        assert md.frames == []

    def test_frames_stored_correctly(self):
        p = Path("/tmp/frame.jpg")
        md = MediaDescription(**{**_FULL_DICT, "frames": [p]})
        assert md.frames == [p]


# ---------------------------------------------------------------------------
# DEFAULT constant
# ---------------------------------------------------------------------------

class TestDefault:
    def test_default_is_media_description(self):
        assert isinstance(DEFAULT, MediaDescription)

    def test_default_content_type_unknown(self):
        assert DEFAULT.content_type == "unknown"

    def test_default_frames_empty(self):
        assert DEFAULT.frames == []

    def test_default_description_non_empty(self):
        assert len(DEFAULT.description) > 0


# ---------------------------------------------------------------------------
# classify_image
# ---------------------------------------------------------------------------

class TestClassifyImage:
    def setup_method(self):
        _reset()

    def test_returns_media_description(self, tmp_path):
        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        assert isinstance(classify_image(img), MediaDescription)

    def test_happy_path_fields_from_generate_json(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        out = classify_image(img)
        assert out.content_type == "video"
        assert out.subject      == "a person walking"
        assert out.setting      == "city street"
        assert out.mood         == "calm"

    def test_frames_list_contains_image_path(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        assert img in classify_image(img).frames

    def test_generate_json_called_once(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        classify_image(img)
        assert _fake_oc.generate_json.call_count == 1

    def test_generate_json_receives_images_b64(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        classify_image(img)
        kw = _fake_oc.generate_json.call_args.kwargs
        assert "images_b64" in kw
        assert len(kw["images_b64"]) == 1

    def test_generate_json_uses_fast_model(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        classify_image(img)
        from cortex import config
        assert _fake_oc.generate_json.call_args.kwargs.get("model") == config.OLLAMA_MODEL_FAST

    def test_non_dict_response_returns_default_with_path(self, tmp_path):
        img = tmp_path / "photo.png"
        img.write_bytes(b"\x89PNG\r\n" + b"\x00" * 10)
        _fake_oc.generate_json.return_value = "not a dict"
        out = classify_image(img)
        assert out.content_type == DEFAULT.content_type
        assert img in out.frames

    def test_none_response_returns_default_with_path(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        _fake_oc.generate_json.return_value = None
        out = classify_image(img)
        assert out.content_type == DEFAULT.content_type
        assert img in out.frames

    def test_oserror_on_read_returns_default(self, tmp_path):
        missing = tmp_path / "nonexistent.jpg"
        out = classify_image(missing)
        assert out.content_type == DEFAULT.content_type
        assert out.frames == []

    def test_partial_dict_uses_defaults_for_missing_keys(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 10)
        _fake_oc.generate_json.return_value = {"content_type": "image"}
        out = classify_image(img)
        assert out.content_type == "image"
        assert out.subject == DEFAULT.subject


# ---------------------------------------------------------------------------
# classify (video path)
# ---------------------------------------------------------------------------

class TestClassify:
    def setup_method(self):
        _reset()

    def _make_frames(self, tmp_path, n=2) -> list[Path]:
        frames = []
        for i in range(n):
            f = tmp_path / f"frame_{i}.jpg"
            f.write_bytes(b"\xff\xd8\xff" + b"\x00" * 8)
            frames.append(f)
        return frames

    def test_video_calls_extract_keyframes(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        classify(video)
        _fake_gv.extract_keyframes.assert_called_once_with(video, n=4)

    def test_happy_path_returns_media_description(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.return_value = self._make_frames(tmp_path)
        assert isinstance(classify(video), MediaDescription)

    def test_happy_path_fields_populated(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.return_value = self._make_frames(tmp_path)
        out = classify(video)
        assert out.content_type == "video"

    def test_frames_stored_in_result(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        frames = self._make_frames(tmp_path)
        _fake_gv.extract_keyframes.return_value = frames
        assert classify(video).frames == frames

    def test_audio_only_extension_skips_frame_extraction(self, tmp_path):
        audio = tmp_path / "clip.mp3"
        audio.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.reset_mock()
        classify(audio)
        _fake_gv.extract_keyframes.assert_not_called()

    def test_wav_extension_is_audio_only(self, tmp_path):
        audio = tmp_path / "sound.wav"
        audio.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.reset_mock()
        classify(audio)
        _fake_gv.extract_keyframes.assert_not_called()

    def test_non_dict_response_returns_default_with_frames(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        frames = self._make_frames(tmp_path)
        _fake_gv.extract_keyframes.return_value = frames
        _fake_oc.generate_json.return_value = "bad"
        out = classify(video)
        assert out.content_type == DEFAULT.content_type
        assert out.frames == frames

    def test_transcript_appended_to_user_prompt_when_frames_present(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        frames = self._make_frames(tmp_path)
        _fake_gv.extract_keyframes.return_value = frames
        _fake_gv.extract_audio_segment.return_value = tmp_path / "audio.wav"
        (tmp_path / "audio.wav").write_bytes(b"\x00" * 8)
        _fake_gv.transcribe_audio.return_value = "Hello world transcript"
        classify(video)
        prompt = _fake_oc.generate_json.call_args.kwargs.get("prompt", "")
        assert "Hello world transcript" in prompt

    def test_no_frames_triggers_audio_only_path(self, tmp_path):
        video = tmp_path / "clip.webm"
        video.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.return_value = []
        classify(video)
        kw = _fake_oc.generate_json.call_args.kwargs
        assert kw.get("images_b64") is None

    def test_images_b64_passed_when_frames_present(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.return_value = self._make_frames(tmp_path)
        classify(video)
        kw = _fake_oc.generate_json.call_args.kwargs
        assert kw.get("images_b64") is not None
        assert len(kw["images_b64"]) == 2

    def test_duration_probed_for_video(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        classify(video)
        _fake_gv._probe_duration.assert_called_once_with(video)

    def test_n_frames_parameter_forwarded(self, tmp_path):
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        _fake_gv.extract_keyframes.reset_mock()
        classify(video, n_frames=8)
        _fake_gv.extract_keyframes.assert_called_once_with(video, n=8)
