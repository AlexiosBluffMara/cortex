"""Tests for cortex.errors — structured error handling per SPEC §15."""
from __future__ import annotations

import json

import pytest

from cortex.errors import (
    CortexError,
    CortexException,
    ErrorClass,
    ErrorCode,
    corrupt_media,
    duration_too_long,
    file_not_found,
    file_too_large,
    invalid_file_type,
    model_not_found,
    ollama_down,
    oom_error,
    queue_full,
    queue_timeout,
    tribe_nan_output,
    tribe_timeout,
    vision_gate_failed,
)

pytestmark = pytest.mark.unit


class TestCortexErrorCore:
    def test_ok_is_always_false(self):
        err = CortexError(code=ErrorCode.CUDA_OOM, message="OOM", component="pipeline")
        assert err.ok is False

    def test_error_class_lookup_for_each_class(self):
        cases = [
            (ErrorCode.INVALID_FILE_TYPE, ErrorClass.INPUT),
            (ErrorCode.CUDA_OOM, ErrorClass.RESOURCE),
            (ErrorCode.TRIBE_TIMEOUT, ErrorClass.MODEL),
            (ErrorCode.WHATSAPP_DISCONNECT, ErrorClass.NETWORK),
        ]
        for code, expected in cases:
            err = CortexError(code=code, message="x", component="c")
            assert err.error_class is expected

    def test_every_error_code_has_a_class_mapping(self):
        # Guards against forgetting to register a new code in _CODE_TO_CLASS.
        for code in ErrorCode:
            err = CortexError(code=code, message="m", component="c")
            assert err.error_class in ErrorClass

    def test_default_recovery_action_populated(self):
        err = CortexError(code=ErrorCode.CUDA_OOM, message="OOM", component="pipeline")
        assert err.recovery_action  # non-empty by default

    def test_explicit_recovery_action_overrides_default(self):
        err = CortexError(
            code=ErrorCode.CUDA_OOM,
            message="OOM",
            component="pipeline",
            recovery_action="Custom action",
        )
        assert err.recovery_action == "Custom action"

    def test_to_dict_round_trip_via_json(self):
        err = CortexError(
            code=ErrorCode.OLLAMA_DOWN,
            message="ollama dead",
            component="ollama_client",
            vram_state={"free_gb": 10.0},
        )
        d = err.to_dict()
        loaded = json.loads(json.dumps(d))
        assert loaded["error_code"] == "ollama_down"
        assert loaded["error_class"] == "resource"
        assert loaded["ok"] is False
        assert loaded["component"] == "ollama_client"
        assert loaded["vram_state"] == {"free_gb": 10.0}

    def test_timestamp_is_iso8601(self):
        err = CortexError(code=ErrorCode.CUDA_OOM, message="m", component="c")
        # Default timezone is UTC, formatted to seconds precision.
        assert "T" in err.timestamp
        assert err.timestamp.endswith("+00:00")

    def test_explicit_timestamp_preserved(self):
        err = CortexError(
            code=ErrorCode.CUDA_OOM,
            message="m",
            component="c",
            timestamp="2026-04-25T12:00:00+00:00",
        )
        assert err.timestamp == "2026-04-25T12:00:00+00:00"

    def test_dict_keys_match_spec(self):
        err = CortexError(code=ErrorCode.CUDA_OOM, message="m", component="c")
        d = err.to_dict()
        assert set(d.keys()) == {
            "ok", "error_code", "error_class", "message",
            "recovery_action", "retry", "fallback_used",
            "component", "vram_state", "timestamp",
        }


class TestCortexException:
    def test_wraps_error_message(self):
        err = CortexError(code=ErrorCode.CUDA_OOM, message="oops", component="c")
        with pytest.raises(CortexException) as ei:
            raise CortexException(err)
        assert ei.value.error is err
        assert "oops" in str(ei.value)


class TestFactories:
    def test_oom_error_defaults(self):
        err = oom_error("pipeline")
        assert err.code is ErrorCode.CUDA_OOM
        assert err.error_class is ErrorClass.RESOURCE
        assert err.retry is True
        assert err.component == "pipeline"

    def test_oom_error_with_state(self):
        err = oom_error("pipeline", vram_state={"free_gb": 0.2}, fallback_used="gcp_a100")
        assert err.vram_state == {"free_gb": 0.2}
        assert err.fallback_used == "gcp_a100"

    def test_invalid_file_type(self):
        err = invalid_file_type("malicious.exe")
        assert err.code is ErrorCode.INVALID_FILE_TYPE
        assert err.error_class is ErrorClass.INPUT
        assert "malicious.exe" in err.message

    def test_file_too_large_message_includes_sizes(self):
        err = file_too_large(75.3, 50)
        assert "75.3" in err.message
        assert "50" in err.message

    def test_duration_too_long_does_not_retry(self):
        err = duration_too_long(120.0, 50.0)
        assert err.code is ErrorCode.DURATION_TOO_LONG
        assert err.retry is False
        assert "120" in err.message

    def test_corrupt_media(self):
        err = corrupt_media("/tmp/bad.mp4")
        assert err.code is ErrorCode.CORRUPT_MEDIA
        assert "bad.mp4" in err.message

    def test_file_not_found(self):
        err = file_not_found("/missing/path.mp4")
        assert err.code is ErrorCode.FILE_NOT_FOUND
        assert "/missing/path.mp4" in err.message

    def test_ollama_down_marks_retry(self):
        err = ollama_down("http://localhost:11434")
        assert err.code is ErrorCode.OLLAMA_DOWN
        assert err.retry is True
        assert "11434" in err.message

    def test_model_not_found_includes_pull_command(self):
        err = model_not_found("gemma4:e4b")
        assert err.code is ErrorCode.MODEL_NOT_FOUND
        assert "gemma4:e4b" in err.message
        assert "ollama pull gemma4:e4b" in err.recovery_action

    def test_queue_full(self):
        err = queue_full(50)
        assert err.code is ErrorCode.QUEUE_FULL
        assert "50" in err.message

    def test_queue_timeout_marks_retry(self):
        err = queue_timeout(600)
        assert err.code is ErrorCode.QUEUE_TIMEOUT
        assert err.retry is True

    def test_vision_gate_failed_includes_reason(self):
        err = vision_gate_failed("model returned empty response")
        assert err.code is ErrorCode.VISION_GATE_FAILED
        assert "empty response" in err.message
        assert err.error_class is ErrorClass.MODEL

    def test_tribe_nan_output_marks_retry(self):
        err = tribe_nan_output()
        assert err.code is ErrorCode.TRIBE_NAN_OUTPUT
        assert err.retry is True

    def test_tribe_timeout_includes_elapsed(self):
        err = tribe_timeout(612.5)
        assert err.code is ErrorCode.TRIBE_TIMEOUT
        assert "612" in err.message


class TestEnumValues:
    """Lock the wire format: error code/class string values must not drift."""

    def test_error_class_values(self):
        assert ErrorClass.INPUT.value == "input"
        assert ErrorClass.RESOURCE.value == "resource"
        assert ErrorClass.MODEL.value == "model"
        assert ErrorClass.NETWORK.value == "network"

    def test_error_code_values(self):
        # Spot-check a few that the spec explicitly names.
        assert ErrorCode.CUDA_OOM.value == "cuda_oom"
        assert ErrorCode.OLLAMA_DOWN.value == "ollama_down"
        assert ErrorCode.HALLUCINATED_TOOL_CALL.value == "hallucinated_tool_call"
        assert ErrorCode.WHATSAPP_DISCONNECT.value == "whatsapp_disconnect"
        assert ErrorCode.CLOUDFLARE_TUNNEL_DOWN.value == "cloudflare_tunnel_down"
