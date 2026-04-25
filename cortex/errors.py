"""Structured error types for Cortex (per SPEC §15).

All Cortex public APIs return either a successful result or a `CortexError`
instance. Callers that need exception semantics raise `CortexException(error)`.
This is the only error path — do not raise raw RuntimeError from public APIs.

Error code → class mapping is fixed; callers cannot construct mismatches.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class ErrorClass(str, Enum):
    INPUT = "input"
    RESOURCE = "resource"
    MODEL = "model"
    NETWORK = "network"


class ErrorCode(str, Enum):
    # INPUT class — caller-side problems (bad upload, malformed file, etc.)
    INVALID_FILE_TYPE = "invalid_file_type"
    FILE_TOO_LARGE = "file_too_large"
    DURATION_TOO_LONG = "duration_too_long"
    DURATION_TOO_SHORT = "duration_too_short"
    CORRUPT_MEDIA = "corrupt_media"
    NO_AUDIO_TRACK = "no_audio_track"
    NO_VIDEO_TRACK = "no_video_track"
    RESOLUTION_TOO_HIGH = "resolution_too_high"
    FILE_NOT_FOUND = "file_not_found"

    # RESOURCE class — hardware / infra exhaustion
    CUDA_OOM = "cuda_oom"
    CUDA_UNAVAILABLE = "cuda_unavailable"
    DISK_FULL = "disk_full"
    OLLAMA_DOWN = "ollama_down"
    OLLAMA_TIMEOUT = "ollama_timeout"
    MODEL_NOT_FOUND = "model_not_found"
    GCP_PREEMPTED = "gcp_preempted"
    GCP_QUOTA_EXCEEDED = "gcp_quota_exceeded"
    QUEUE_FULL = "queue_full"
    QUEUE_TIMEOUT = "queue_timeout"

    # MODEL class — model behaving badly (hallucinations, NaN, junk output)
    HALLUCINATED_TOOL_CALL = "hallucinated_tool_call"
    BAD_NARRATION = "bad_narration"
    VISION_GATE_FAILED = "vision_gate_failed"
    TRIBE_NAN_OUTPUT = "tribe_nan_output"
    TRIBE_TIMEOUT = "tribe_timeout"
    INFERENCE_FAILED = "inference_failed"

    # NETWORK class — transport-level failures
    WHATSAPP_DISCONNECT = "whatsapp_disconnect"
    WEBSOCKET_DROPPED = "websocket_dropped"
    GCP_NETWORK_TIMEOUT = "gcp_network_timeout"
    CLOUDFLARE_TUNNEL_DOWN = "cloudflare_tunnel_down"
    OLLAMA_CONNECTION_ERROR = "ollama_connection_error"


_CODE_TO_CLASS: dict[ErrorCode, ErrorClass] = {
    ErrorCode.INVALID_FILE_TYPE: ErrorClass.INPUT,
    ErrorCode.FILE_TOO_LARGE: ErrorClass.INPUT,
    ErrorCode.DURATION_TOO_LONG: ErrorClass.INPUT,
    ErrorCode.DURATION_TOO_SHORT: ErrorClass.INPUT,
    ErrorCode.CORRUPT_MEDIA: ErrorClass.INPUT,
    ErrorCode.NO_AUDIO_TRACK: ErrorClass.INPUT,
    ErrorCode.NO_VIDEO_TRACK: ErrorClass.INPUT,
    ErrorCode.RESOLUTION_TOO_HIGH: ErrorClass.INPUT,
    ErrorCode.FILE_NOT_FOUND: ErrorClass.INPUT,
    ErrorCode.CUDA_OOM: ErrorClass.RESOURCE,
    ErrorCode.CUDA_UNAVAILABLE: ErrorClass.RESOURCE,
    ErrorCode.DISK_FULL: ErrorClass.RESOURCE,
    ErrorCode.OLLAMA_DOWN: ErrorClass.RESOURCE,
    ErrorCode.OLLAMA_TIMEOUT: ErrorClass.RESOURCE,
    ErrorCode.MODEL_NOT_FOUND: ErrorClass.RESOURCE,
    ErrorCode.GCP_PREEMPTED: ErrorClass.RESOURCE,
    ErrorCode.GCP_QUOTA_EXCEEDED: ErrorClass.RESOURCE,
    ErrorCode.QUEUE_FULL: ErrorClass.RESOURCE,
    ErrorCode.QUEUE_TIMEOUT: ErrorClass.RESOURCE,
    ErrorCode.HALLUCINATED_TOOL_CALL: ErrorClass.MODEL,
    ErrorCode.BAD_NARRATION: ErrorClass.MODEL,
    ErrorCode.VISION_GATE_FAILED: ErrorClass.MODEL,
    ErrorCode.TRIBE_NAN_OUTPUT: ErrorClass.MODEL,
    ErrorCode.TRIBE_TIMEOUT: ErrorClass.MODEL,
    ErrorCode.INFERENCE_FAILED: ErrorClass.MODEL,
    ErrorCode.WHATSAPP_DISCONNECT: ErrorClass.NETWORK,
    ErrorCode.WEBSOCKET_DROPPED: ErrorClass.NETWORK,
    ErrorCode.GCP_NETWORK_TIMEOUT: ErrorClass.NETWORK,
    ErrorCode.CLOUDFLARE_TUNNEL_DOWN: ErrorClass.NETWORK,
    ErrorCode.OLLAMA_CONNECTION_ERROR: ErrorClass.NETWORK,
}


_DEFAULT_RECOVERY: dict[ErrorCode, str] = {
    ErrorCode.INVALID_FILE_TYPE: (
        "Use one of the supported formats: .mp4, .mkv, .webm, .mov, .avi, "
        ".mp3, .wav, .flac, .ogg, .m4a, .jpg, .png, .webp"
    ),
    ErrorCode.FILE_TOO_LARGE: "Compress or trim the file to under 50MB",
    ErrorCode.DURATION_TOO_LONG: "Auto-trimmed to last 50 seconds",
    ErrorCode.DURATION_TOO_SHORT: "Provide media at least 2 seconds long",
    ErrorCode.CORRUPT_MEDIA: "File appears corrupt; re-encode with FFmpeg and retry",
    ErrorCode.NO_AUDIO_TRACK: "Continuing with video-only path (TRIBE handles missing modalities)",
    ErrorCode.NO_VIDEO_TRACK: "Continuing with audio-only path",
    ErrorCode.RESOLUTION_TOO_HIGH: "Auto-downscaled to 1920x1080",
    ErrorCode.FILE_NOT_FOUND: "Verify the file path",
    ErrorCode.CUDA_OOM: "Cleared cache and retrying; will fall back to GCP if local fails twice",
    ErrorCode.CUDA_UNAVAILABLE: "Falling back to GCP A100",
    ErrorCode.DISK_FULL: "Free disk space and retry",
    ErrorCode.OLLAMA_DOWN: "Restart Ollama service: `ollama serve`",
    ErrorCode.OLLAMA_TIMEOUT: "Retrying with longer timeout",
    ErrorCode.MODEL_NOT_FOUND: "Pull the model: `ollama pull <model>`",
    ErrorCode.GCP_PREEMPTED: "Spot instance preempted; queueing for next available instance",
    ErrorCode.GCP_QUOTA_EXCEEDED: "Falling back to local sequential mode",
    ErrorCode.QUEUE_FULL: "Try again in a few minutes",
    ErrorCode.QUEUE_TIMEOUT: "Request waited too long; resubmit",
    ErrorCode.HALLUCINATED_TOOL_CALL: "Re-prompting model with stricter schema",
    ErrorCode.BAD_NARRATION: "Retrying with lower temperature",
    ErrorCode.VISION_GATE_FAILED: "Falling back to text-only TRIBE path",
    ErrorCode.TRIBE_NAN_OUTPUT: "Retrying with adjusted preprocessing",
    ErrorCode.TRIBE_TIMEOUT: "Inference exceeded 10 minutes; killed",
    ErrorCode.INFERENCE_FAILED: "Check logs for details",
    ErrorCode.WHATSAPP_DISCONNECT: "Reconnecting with exponential backoff",
    ErrorCode.WEBSOCKET_DROPPED: "Auto-reconnecting; replaying last frame",
    ErrorCode.GCP_NETWORK_TIMEOUT: "Retrying; will fall back to local mode",
    ErrorCode.CLOUDFLARE_TUNNEL_DOWN: "Restarting tunnel; serving cached state",
    ErrorCode.OLLAMA_CONNECTION_ERROR: "Verify Ollama is running at the configured URL",
}


def _utc_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class CortexError:
    """Structured error response per SPEC §15.

    `code` and `error_class` are tightly coupled — the class is derived from
    the code via a fixed lookup. Callers cannot construct mismatched pairs.
    """

    code: ErrorCode
    message: str
    component: str
    recovery_action: str = ""
    retry: bool = False
    fallback_used: str | None = None
    vram_state: dict[str, Any] | None = None
    timestamp: str = field(default_factory=_utc_iso)

    def __post_init__(self) -> None:
        if not self.recovery_action:
            self.recovery_action = _DEFAULT_RECOVERY.get(self.code, "")

    @property
    def ok(self) -> bool:
        return False

    @property
    def error_class(self) -> ErrorClass:
        return _CODE_TO_CLASS[self.code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": False,
            "error_code": self.code.value,
            "error_class": self.error_class.value,
            "message": self.message,
            "recovery_action": self.recovery_action,
            "retry": self.retry,
            "fallback_used": self.fallback_used,
            "component": self.component,
            "vram_state": self.vram_state,
            "timestamp": self.timestamp,
        }


class CortexException(Exception):
    """Wrap a CortexError as a raisable Python exception."""

    def __init__(self, error: CortexError) -> None:
        super().__init__(error.message)
        self.error = error


# -- Convenience factories -----------------------------------------------------

def oom_error(
    component: str,
    *,
    vram_state: dict[str, Any] | None = None,
    fallback_used: str | None = None,
    retry: bool = True,
) -> CortexError:
    return CortexError(
        code=ErrorCode.CUDA_OOM,
        message="GPU memory exhausted during inference",
        component=component,
        retry=retry,
        fallback_used=fallback_used,
        vram_state=vram_state,
    )


def invalid_file_type(filename: str, component: str = "media_gate") -> CortexError:
    return CortexError(
        code=ErrorCode.INVALID_FILE_TYPE,
        message=f"Unsupported file type: {filename}",
        component=component,
    )


def file_too_large(size_mb: float, max_mb: float, component: str = "media_gate") -> CortexError:
    return CortexError(
        code=ErrorCode.FILE_TOO_LARGE,
        message=f"File is {size_mb:.1f}MB; max is {max_mb:.0f}MB",
        component=component,
    )


def duration_too_long(seconds: float, max_s: float, component: str = "media_gate") -> CortexError:
    return CortexError(
        code=ErrorCode.DURATION_TOO_LONG,
        message=f"Duration {seconds:.1f}s exceeds limit {max_s:.0f}s; trimming to last {max_s:.0f}s",
        component=component,
        retry=False,
    )


def corrupt_media(path: str, component: str = "media_processor") -> CortexError:
    return CortexError(
        code=ErrorCode.CORRUPT_MEDIA,
        message=f"FFmpeg could not decode {path}",
        component=component,
    )


def file_not_found(path: str, component: str = "media_gate") -> CortexError:
    return CortexError(
        code=ErrorCode.FILE_NOT_FOUND,
        message=f"File not found: {path}",
        component=component,
    )


def ollama_down(url: str, component: str = "ollama_client") -> CortexError:
    return CortexError(
        code=ErrorCode.OLLAMA_DOWN,
        message=f"Ollama not responding at {url}",
        component=component,
        retry=True,
    )


def model_not_found(model: str, component: str = "ollama_client") -> CortexError:
    return CortexError(
        code=ErrorCode.MODEL_NOT_FOUND,
        message=f"Model not pulled: {model}",
        component=component,
        recovery_action=f"Pull the model: `ollama pull {model}`",
    )


def queue_full(depth: int, component: str = "request_queue") -> CortexError:
    return CortexError(
        code=ErrorCode.QUEUE_FULL,
        message=f"Request queue full ({depth} pending)",
        component=component,
    )


def queue_timeout(seconds: int, component: str = "request_queue") -> CortexError:
    return CortexError(
        code=ErrorCode.QUEUE_TIMEOUT,
        message=f"Request waited >{seconds}s without being processed",
        component=component,
        retry=True,
    )


def vision_gate_failed(reason: str, component: str = "gemma_vision") -> CortexError:
    return CortexError(
        code=ErrorCode.VISION_GATE_FAILED,
        message=f"Vision gate failed: {reason}",
        component=component,
    )


def tribe_nan_output(component: str = "pipeline") -> CortexError:
    return CortexError(
        code=ErrorCode.TRIBE_NAN_OUTPUT,
        message="TRIBE v2 returned NaN BOLD values",
        component=component,
        retry=True,
    )


def tribe_timeout(elapsed_s: float, component: str = "pipeline") -> CortexError:
    return CortexError(
        code=ErrorCode.TRIBE_TIMEOUT,
        message=f"TRIBE v2 inference exceeded timeout ({elapsed_s:.0f}s)",
        component=component,
    )


__all__ = [
    "CortexError",
    "CortexException",
    "ErrorClass",
    "ErrorCode",
    "corrupt_media",
    "duration_too_long",
    "file_not_found",
    "file_too_large",
    "invalid_file_type",
    "model_not_found",
    "ollama_down",
    "oom_error",
    "queue_full",
    "queue_timeout",
    "tribe_nan_output",
    "tribe_timeout",
    "vision_gate_failed",
]
