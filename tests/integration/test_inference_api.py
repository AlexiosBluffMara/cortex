"""Integration tests for `gcp.cloud_run.inference_api` — the A100 worker.

These run against the real FastAPI app via TestClient. The TRIBE pipeline is
mocked at the module level (`sys.modules["cortex.pipeline"]`) so we don't drag
in torch.

The wire format under test is the contract `cortex.gcp_inference` depends on:

  POST /infer              → 202 { job_id, status: "queued" }
  GET  /infer/{job_id}     → 200 { status, result | None, error | None, ... }
  GET  /healthz            → 200 { ok, gpu, queue_depth, active_jobs, ... }

Auth: when GCP_INFERENCE_TOKEN is set, the app rejects missing/bad bearers.
"""
from __future__ import annotations

import sys
import time
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def fake_pipeline_module(monkeypatch):
    """Stub `cortex.pipeline` so the worker can call `run_inference()` without
    importing torch. Returns the stub so tests can mutate behaviour.

    Patches both sys.modules and the `cortex.pipeline` attribute on the cortex
    package itself — without the latter, prior tests in the suite that imported
    cortex.pipeline leak a stale reference that `from cortex import pipeline`
    resolves to (since once the attribute exists on the package, the import
    statement uses it instead of re-resolving via sys.modules).
    """
    fake = MagicMock(name="cortex-pipeline-stub")

    def _default_run(media_path: str):
        result = MagicMock()
        result.preds_url = "gs://cortex-test-bucket/preds.npy"
        result.top_rois = ["V1", "FFA", "STG"]
        result.peak_t = 17
        result.seconds_elapsed = 4.2
        return result

    fake.run_inference.side_effect = _default_run

    monkeypatch.setitem(sys.modules, "cortex.pipeline", fake)
    import cortex
    monkeypatch.setattr(cortex, "pipeline", fake, raising=False)

    return fake


@pytest.fixture
def app(fake_pipeline_module, monkeypatch):
    """Build a fresh worker app with auth disabled by default."""
    monkeypatch.delenv("GCP_INFERENCE_TOKEN", raising=False)
    # Reload the module so EXPECTED_TOKEN resolves fresh
    import importlib

    import gcp.cloud_run.inference_api as worker
    importlib.reload(worker)
    return worker.create_app()


@pytest.fixture
def client(app):
    with TestClient(app) as tc:
        yield tc


def _wait_for_status(client: TestClient, job_id: str, target: str, timeout_s: float = 5.0) -> dict:
    """Poll /infer/{job_id} until status == target or timeout. Returns the body."""
    deadline = time.time() + timeout_s
    last_body: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/infer/{job_id}")
        if resp.status_code == 200:
            last_body = resp.json()
            if last_body.get("status") == target:
                return last_body
        time.sleep(0.05)
    raise AssertionError(
        f"Job {job_id} never reached status={target!r} within {timeout_s}s; last={last_body}"
    )


# ---------------------------------------------------------------------------
# Healthz
# ---------------------------------------------------------------------------

class TestHealthz:
    def test_healthz_shape(self, client):
        resp = client.get("/healthz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["version"] == "0.1.0"
        assert "gpu" in body
        assert "host" in body
        assert body["active_jobs"] == 0
        assert body["queue_depth"] == 0

    def test_healthz_does_not_require_auth(self, client, monkeypatch):
        # Even with auth on, /healthz is unauthenticated so liveness probes work.
        monkeypatch.setenv("GCP_INFERENCE_TOKEN", "secret")
        resp = client.get("/healthz")
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Submit + lookup happy path
# ---------------------------------------------------------------------------

class TestSubmitLookup:
    def test_submit_returns_202_and_job_id(self, client):
        resp = client.post("/infer", json={"media_path": "/tmp/clip.mp4"})
        assert resp.status_code == 202
        body = resp.json()
        assert body["status"] == "queued"
        assert "job_id" in body
        assert len(body["job_id"]) == 16

    def test_lookup_unknown_job_returns_404(self, client):
        resp = client.get("/infer/does-not-exist")
        assert resp.status_code == 404

    def test_full_lifecycle_queued_to_succeeded(self, client):
        submit = client.post("/infer", json={"media_path": "/tmp/clip.mp4"})
        job_id = submit.json()["job_id"]
        body = _wait_for_status(client, job_id, "succeeded")

        assert body["status"] == "succeeded"
        assert body["result"]["top_rois"] == ["V1", "FFA", "STG"]
        assert body["result"]["peak_t"] == 17
        assert body["result"]["seconds_elapsed"] == 4.2
        assert body["error"] is None
        assert body["queued_at"]
        assert body["started_at"]
        assert body["finished_at"]

    def test_failure_lifecycle_returns_failed_with_error(self, client, fake_pipeline_module):
        fake_pipeline_module.run_inference.side_effect = RuntimeError("CUDA OOM on worker")
        submit = client.post("/infer", json={"media_path": "/tmp/clip.mp4"})
        job_id = submit.json()["job_id"]
        body = _wait_for_status(client, job_id, "failed")
        assert body["status"] == "failed"
        assert body["error"]["message"] == "CUDA OOM on worker"
        assert body["error"]["type"] == "RuntimeError"
        assert body["result"] is None


# ---------------------------------------------------------------------------
# Submit validation
# ---------------------------------------------------------------------------

class TestSubmitValidation:
    def test_missing_media_path_returns_400(self, client):
        resp = client.post("/infer", json={})
        assert resp.status_code == 400
        assert "media_path" in resp.json()["detail"]

    def test_non_string_scan_id_returns_400(self, client):
        resp = client.post(
            "/infer",
            json={"media_path": "/tmp/x.mp4", "scan_id": 12345},
        )
        assert resp.status_code == 400

    def test_scan_id_passes_through_to_record(self, client):
        submit = client.post(
            "/infer",
            json={"media_path": "/tmp/x.mp4", "scan_id": "scan_abc"},
        )
        job_id = submit.json()["job_id"]
        # Look it up immediately — we may catch it queued or running, but the
        # scan_id should be persisted regardless of status.
        resp = client.get(f"/infer/{job_id}")
        assert resp.status_code == 200
        assert resp.json()["scan_id"] == "scan_abc"


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TestAuth:
    @pytest.fixture
    def authed_app(self, fake_pipeline_module, monkeypatch):
        monkeypatch.setenv("GCP_INFERENCE_TOKEN", "expected-token")
        import importlib

        import gcp.cloud_run.inference_api as worker
        importlib.reload(worker)
        return worker.create_app()

    @pytest.fixture
    def authed_client(self, authed_app):
        with TestClient(authed_app) as tc:
            yield tc

    def test_missing_authorization_returns_401(self, authed_client):
        resp = authed_client.post("/infer", json={"media_path": "/tmp/x.mp4"})
        assert resp.status_code == 401

    def test_wrong_token_returns_403(self, authed_client):
        resp = authed_client.post(
            "/infer",
            json={"media_path": "/tmp/x.mp4"},
            headers={"Authorization": "Bearer not-the-token"},
        )
        assert resp.status_code == 403

    def test_correct_token_accepts(self, authed_client):
        resp = authed_client.post(
            "/infer",
            json={"media_path": "/tmp/x.mp4"},
            headers={"Authorization": "Bearer expected-token"},
        )
        assert resp.status_code == 202

    def test_lookup_also_requires_auth(self, authed_client):
        # Submit with valid token to create a job
        submit = authed_client.post(
            "/infer",
            json={"media_path": "/tmp/x.mp4"},
            headers={"Authorization": "Bearer expected-token"},
        )
        job_id = submit.json()["job_id"]

        # Lookup without auth → 401
        resp = authed_client.get(f"/infer/{job_id}")
        assert resp.status_code == 401

        # Lookup with auth → 200
        resp = authed_client.get(
            f"/infer/{job_id}",
            headers={"Authorization": "Bearer expected-token"},
        )
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Wire-format compatibility with cortex.gcp_inference client
# ---------------------------------------------------------------------------

class TestWireCompat:
    """If these fail, the client (cortex.gcp_inference) won't be able to parse
    what the worker returns. They're the load-bearing contract tests.
    """

    def test_succeeded_response_has_fields_client_expects(self, client):
        submit = client.post("/infer", json={"media_path": "/tmp/x.mp4"})
        job_id = submit.json()["job_id"]
        body = _wait_for_status(client, job_id, "succeeded")
        # Match cortex/gcp_inference.py:_parse_result expectations
        result = body["result"]
        assert "preds_url" in result
        assert isinstance(result["top_rois"], list)
        assert isinstance(result["peak_t"], int)
        assert isinstance(result["seconds_elapsed"], float)

    def test_failed_response_has_fields_client_expects(self, client, fake_pipeline_module):
        fake_pipeline_module.run_inference.side_effect = RuntimeError("test")
        submit = client.post("/infer", json={"media_path": "/tmp/x.mp4"})
        job_id = submit.json()["job_id"]
        body = _wait_for_status(client, job_id, "failed")
        # The client extracts error.message → CortexError.message
        assert isinstance(body["error"], dict)
        assert "message" in body["error"]
        assert body["error"]["message"] == "test"
