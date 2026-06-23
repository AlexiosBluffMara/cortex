"""End-to-end verifier for a Cortex Cloud TRIBE worker endpoint."""
from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import httpx


class WorkerVerificationError(RuntimeError):
    """Raised when a worker endpoint fails the Cortex proxy contract."""


def _headers(token: str | None) -> dict[str, str]:
    token = (token or "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}


def _sample_payload(sample_path: Path | None) -> tuple[str, bytes, str]:
    if sample_path is None:
        return (
            "cortex-cloud-worker-smoke.txt",
            b"Cortex cloud worker smoke test: a short text stimulus for TRIBE contract verification.",
            "text/plain",
        )
    filename = sample_path.name
    mime = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    return filename, sample_path.read_bytes(), mime


def _require_status(response: httpx.Response, expected: set[int], label: str) -> dict[str, Any]:
    if response.status_code not in expected:
        text = response.text[:500]
        raise WorkerVerificationError(f"{label} returned HTTP {response.status_code}: {text}")
    if not response.content:
        return {}
    try:
        return response.json()
    except ValueError as exc:
        raise WorkerVerificationError(f"{label} returned non-JSON content") from exc


async def verify_worker(
    endpoint: str,
    *,
    token: str | None = None,
    sample_path: Path | None = None,
    timeout_s: float = 90.0,
    poll_s: float = 0.5,
    n_t: int = 4,
    require_real: bool = False,
    client: httpx.AsyncClient | None = None,
) -> dict[str, Any]:
    """Run a full HTTP contract smoke test against a cloud TRIBE worker."""
    endpoint = endpoint.rstrip("/")
    if not endpoint:
        raise WorkerVerificationError("worker endpoint is required")

    close_client = client is None
    headers = _headers(token)
    if client is None:
        client = httpx.AsyncClient(timeout=20)

    started = time.monotonic()
    try:
        health = _require_status(await client.get(f"{endpoint}/healthz"), {200}, "healthz")
        readiness = _require_status(await client.get(f"{endpoint}/api/tribe/readiness"), {200}, "readiness")

        if require_real and not readiness.get("real_mode_ready"):
            missing = "; ".join(readiness.get("missing") or ["real mode is not ready"])
            raise WorkerVerificationError(f"real TRIBE mode is not ready: {missing}")
        if not readiness.get("contract_ready"):
            raise WorkerVerificationError("worker does not report contract_ready=true")

        filename, payload, mime = _sample_payload(sample_path)
        files = {"file": (filename, payload, mime)}
        data = {
            "tier": "4",
            "source": "cloud-worker-verifier",
            "narration_model": "openrouter/free",
            "compute_target": "cloud_verify",
        }
        submit = _require_status(
            await client.post(f"{endpoint}/api/scan", headers=headers, files=files, data=data),
            {200, 202},
            "scan submit",
        )
        scan_id = submit.get("scan_id") or submit.get("id")
        if not scan_id:
            raise WorkerVerificationError("scan submit did not return scan_id")

        deadline = time.monotonic() + timeout_s
        detail: dict[str, Any] = {}
        while time.monotonic() < deadline:
            detail = _require_status(
                await client.get(f"{endpoint}/api/scan/{scan_id}", headers=headers),
                {200},
                "scan detail",
            )
            status = detail.get("status")
            if status == "complete":
                break
            if status == "failed":
                raise WorkerVerificationError(f"scan failed: {detail.get('error')}")
            await asyncio.sleep(poll_s)
        else:
            raise WorkerVerificationError(f"scan {scan_id} did not complete within {timeout_s:g}s")

        bold = await client.get(f"{endpoint}/api/scan/{scan_id}/bold-vertex?n_t={int(n_t)}", headers=headers)
        if bold.status_code != 200:
            raise WorkerVerificationError(f"bold-vertex returned HTTP {bold.status_code}: {bold.text[:500]}")
        returned_t = int(bold.headers.get("X-N-T", "0") or 0)
        n_vertices = int(bold.headers.get("X-N-Vert", "0") or 0)
        expected_bytes = returned_t * n_vertices * 4
        if returned_t <= 0 or n_vertices != 20484:
            raise WorkerVerificationError(
                f"unexpected BOLD shape headers: X-N-T={returned_t}, X-N-Vert={n_vertices}"
            )
        if len(bold.content) != expected_bytes:
            raise WorkerVerificationError(
                f"unexpected BOLD byte length: got {len(bold.content)}, expected {expected_bytes}"
            )

        media = await client.get(f"{endpoint}/api/scan/{scan_id}/source-media", headers=headers)
        if media.status_code != 200:
            raise WorkerVerificationError(f"source-media returned HTTP {media.status_code}: {media.text[:500]}")

        return {
            "ok": True,
            "endpoint": endpoint,
            "mode": readiness.get("mode") or health.get("mode"),
            "provider": readiness.get("provider") or health.get("provider"),
            "contract_ready": bool(readiness.get("contract_ready")),
            "real_mode_ready": bool(readiness.get("real_mode_ready")),
            "scan_id": scan_id,
            "scan_status": detail.get("status"),
            "analysis_mode": detail.get("analysis_mode"),
            "top_rois": detail.get("top_rois") or [],
            "peak_t": detail.get("peak_t"),
            "n_t": returned_t,
            "n_vertices": n_vertices,
            "bold_bytes": len(bold.content),
            "source_media_bytes": len(media.content),
            "elapsed_s": round(time.monotonic() - started, 3),
        }
    finally:
        if close_client:
            await client.aclose()


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Verify a Cortex Cloud TRIBE worker endpoint end to end.")
    parser.add_argument("--endpoint", default=os.environ.get("CORTEX_CLOUD_TRIBE_ENDPOINT", ""))
    parser.add_argument("--token", default=os.environ.get("CORTEX_CLOUD_TRIBE_TOKEN", ""))
    parser.add_argument("--sample", type=Path, default=None, help="Optional stimulus file to submit.")
    parser.add_argument("--timeout-s", type=float, default=90.0)
    parser.add_argument("--n-t", type=int, default=4)
    parser.add_argument("--require-real", action="store_true", help="Fail unless real TRIBE mode is ready.")
    return parser.parse_args(argv)


async def _main(argv: list[str]) -> int:
    args = _parse_args(argv)
    try:
        result = await verify_worker(
            args.endpoint,
            token=args.token,
            sample_path=args.sample,
            timeout_s=args.timeout_s,
            n_t=args.n_t,
            require_real=args.require_real,
        )
    except Exception as exc:  # noqa: BLE001
        print(json.dumps({"ok": False, "error": str(exc)}, indent=2), file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2))
    return 0


def main() -> None:
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
