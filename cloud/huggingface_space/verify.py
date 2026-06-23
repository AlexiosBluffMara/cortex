"""End-to-end verifier for the Cortex Hugging Face Gradio Space."""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable

import numpy as np

N_VERTICES = 20484


class GradioSpaceVerificationError(RuntimeError):
    """Raised when a Gradio Space fails the Cortex TRIBE output contract."""


def _gradio_file_path(value: Any) -> Path:
    if isinstance(value, str):
        return Path(value)
    if isinstance(value, dict):
        for key in ("path", "name"):
            candidate = value.get(key)
            if candidate:
                return Path(str(candidate))
    path_attr = getattr(value, "path", None) or getattr(value, "name", None)
    if path_attr:
        return Path(str(path_attr))
    raise GradioSpaceVerificationError(
        f"Gradio scan returned an unsupported file output: {type(value).__name__}"
    )


def _parse_space_result(result: Any) -> tuple[dict[str, Any], Path]:
    if not isinstance(result, (list, tuple)) or len(result) < 2:
        raise GradioSpaceVerificationError(
            f"Gradio scan returned an unsupported result: {type(result).__name__}"
        )
    scan_json, bold_file = result[0], result[1]
    if isinstance(scan_json, dict):
        record = dict(scan_json)
    else:
        try:
            record = json.loads(str(scan_json))
        except json.JSONDecodeError as exc:
            raise GradioSpaceVerificationError("Gradio scan returned non-JSON metadata") from exc
    return record, _gradio_file_path(bold_file)


def _sample_path(sample_path: Path | None) -> tuple[Path, tempfile.TemporaryDirectory[str] | None]:
    if sample_path is not None:
        return sample_path, None

    tmp = tempfile.TemporaryDirectory(prefix="cortex-gradio-verify-")
    path = Path(tmp.name) / "cortex-gradio-space-smoke.txt"
    path.write_text(
        "Cortex Gradio Space smoke test: a short text stimulus for TRIBE output verification.",
        encoding="utf-8",
    )
    return path, tmp


def verify_space(
    endpoint: str,
    *,
    hf_token: str | None = None,
    sample_path: Path | None = None,
    tier: int = 4,
    narration_model: str = "openrouter/free",
    require_real: bool = False,
    client: Any | None = None,
    handle_file_fn: Callable[[str], Any] | None = None,
) -> dict[str, Any]:
    """Run a blocking Gradio `scan` call and validate Cortex BOLD output."""
    endpoint = endpoint.rstrip("/")
    if not endpoint and client is None:
        raise GradioSpaceVerificationError("Gradio Space endpoint is required")

    if client is None:
        try:
            from gradio_client import Client, handle_file  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover - depends on optional package
            raise GradioSpaceVerificationError(
                "Install gradio_client to verify a remote Gradio Space."
            ) from exc

        kwargs: dict[str, Any] = {}
        if hf_token:
            kwargs["hf_token"] = hf_token
        client = Client(endpoint, **kwargs)
        handle_file_fn = handle_file
    elif handle_file_fn is None:
        handle_file_fn = lambda value: value

    stimulus, tmp = _sample_path(sample_path)
    started = time.monotonic()
    try:
        if not stimulus.exists():
            raise GradioSpaceVerificationError(f"sample file does not exist: {stimulus}")
        mime = mimetypes.guess_type(stimulus.name)[0] or "application/octet-stream"
        result = client.predict(
            handle_file_fn(str(stimulus)),
            int(tier),
            narration_model,
            api_name="/scan",
        )
        record, bold_path = _parse_space_result(result)
        if not record.get("ok"):
            raise GradioSpaceVerificationError(f"scan did not report ok=true: {record}")
        if record.get("status") != "complete":
            raise GradioSpaceVerificationError(f"scan did not complete: {record.get('status')}")
        if not record.get("contract_ready", True):
            raise GradioSpaceVerificationError("Space did not report contract_ready=true")
        if require_real and (
            record.get("worker_mode") != "real" or not bool(record.get("real_mode_ready"))
        ):
            missing = "; ".join(record.get("readiness_missing") or ["real TRIBE mode is not ready"])
            raise GradioSpaceVerificationError(f"real TRIBE mode is not ready: {missing}")
        if not bold_path.exists():
            raise GradioSpaceVerificationError(f"BOLD file was not downloaded: {bold_path}")

        bold = np.load(bold_path, mmap_mode="r")
        if bold.ndim != 2 or int(bold.shape[1]) != N_VERTICES:
            raise GradioSpaceVerificationError(
                f"unexpected BOLD shape: {tuple(int(x) for x in bold.shape)}"
            )
        if record.get("n_t") is not None and int(record["n_t"]) != int(bold.shape[0]):
            raise GradioSpaceVerificationError(
                f"scan metadata n_t={record['n_t']} does not match BOLD rows={bold.shape[0]}"
            )

        return {
            "ok": True,
            "endpoint": endpoint or "<injected-client>",
            "mode": record.get("worker_mode"),
            "provider": record.get("provider") or record.get("source"),
            "contract_ready": bool(record.get("contract_ready", True)),
            "real_mode_ready": bool(record.get("real_mode_ready")),
            "scan_id": record.get("scan_id") or record.get("id"),
            "scan_status": record.get("status"),
            "analysis_mode": record.get("analysis_mode"),
            "top_rois": record.get("top_rois") or [],
            "peak_t": record.get("peak_t"),
            "sample_mime": mime,
            "n_t": int(bold.shape[0]),
            "n_vertices": int(bold.shape[1]),
            "bold_file": str(bold_path),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    finally:
        if tmp is not None:
            tmp.cleanup()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a Cortex Hugging Face Gradio Space end to end.")
    parser.add_argument("--endpoint", default=os.environ.get("CORTEX_CLOUD_TRIBE_ENDPOINT", ""))
    parser.add_argument("--hf-token", default=os.environ.get("CORTEX_CLOUD_TRIBE_HF_TOKEN", ""))
    parser.add_argument("--sample", type=Path, default=None, help="Optional stimulus file to submit.")
    parser.add_argument("--tier", type=int, default=4)
    parser.add_argument("--narration-model", default="openrouter/free")
    parser.add_argument("--require-real", action="store_true", help="Fail unless real TRIBE mode is ready.")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)
    try:
        result = verify_space(
            args.endpoint,
            hf_token=args.hf_token,
            sample_path=args.sample,
            tier=args.tier,
            narration_model=args.narration_model,
            require_real=args.require_real,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        raise SystemExit(1) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
