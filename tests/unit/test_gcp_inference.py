"""Tests for cortex.gcp_inference — GCP A100 inference fallback."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from cortex.errors import CortexError, CortexException, ErrorCode
from cortex.gcp_inference import (
    GCPInferenceFallback,
    NullFallback,
    RemoteInferenceResult,
    default_fallback,
    raise_for_error,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# NullFallback
# ---------------------------------------------------------------------------

class TestNullFallback:
    def test_unavailable(self):
        fb = NullFallback()
        assert fb.available() is False

    @pytest.mark.asyncio
    async def test_health_says_unavailable(self):
        fb = NullFallback()
        h = await fb.health()
        assert h["available"] is False

    @pytest.mark.asyncio
    async def test_submit_returns_structured_error(self):
        fb = NullFallback()
        result = await fb.submit("/tmp/x.mp4")
        assert isinstance(result, CortexError)
        assert result.code is ErrorCode.GCP_QUOTA_EXCEEDED
        assert "GCP_INFERENCE_ENDPOINT" in result.recovery_action


# ---------------------------------------------------------------------------
# GCPInferenceFallback — availability + auth gating
# ---------------------------------------------------------------------------

class TestAvailability:
    def test_unavailable_without_endpoint(self, monkeypatch):
        monkeypatch.delenv("GCP_INFERENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("GCP_INFERENCE_TOKEN", raising=False)
        assert GCPInferenceFallback().available() is False

    def test_unavailable_with_only_endpoint(self, monkeypatch):
        monkeypatch.setenv("GCP_INFERENCE_ENDPOINT", "https://x")
        monkeypatch.delenv("GCP_INFERENCE_TOKEN", raising=False)
        assert GCPInferenceFallback().available() is False

    def test_available_with_both(self, monkeypatch):
        monkeypatch.setenv("GCP_INFERENCE_ENDPOINT", "https://x")
        monkeypatch.setenv("GCP_INFERENCE_TOKEN", "tok")
        assert GCPInferenceFallback().available() is True

    def test_explicit_args_override_env(self, monkeypatch):
        monkeypatch.delenv("GCP_INFERENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("GCP_INFERENCE_TOKEN", raising=False)
        fb = GCPInferenceFallback(endpoint="https://gcp", token="tok")
        assert fb.available() is True


class TestSubmitWhenUnavailable:
    @pytest.mark.asyncio
    async def test_submit_returns_quota_error(self, monkeypatch):
        monkeypatch.delenv("GCP_INFERENCE_ENDPOINT", raising=False)
        result = await GCPInferenceFallback().submit("/tmp/x.mp4")
        assert isinstance(result, CortexError)
        assert result.code is ErrorCode.GCP_QUOTA_EXCEEDED


# ---------------------------------------------------------------------------
# GCPInferenceFallback — happy path with mocked httpx
# ---------------------------------------------------------------------------

@pytest.fixture
def configured_fallback():
    return GCPInferenceFallback(endpoint="https://example.run.app", token="secret")


def _make_response(status_code: int, json_body: dict | None = None):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body or {}
    if status_code >= 400:
        resp.raise_for_status = MagicMock(side_effect=Exception(f"HTTP {status_code}"))
    else:
        resp.raise_for_status = MagicMock()
    return resp


class _FakeAsyncClient:
    """Minimal stand-in for httpx.AsyncClient that we can inject script."""

    def __init__(self, *args, **kwargs):
        self.posts: list[dict] = []
        self.gets: list[dict] = []
        self._post_responses: list = []
        self._get_responses: list = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc_info):
        return None

    def queue_post(self, resp):
        self._post_responses.append(resp)

    def queue_get(self, resp):
        self._get_responses.append(resp)

    async def post(self, url, headers=None, json=None):
        self.posts.append({"url": url, "headers": headers, "json": json})
        if not self._post_responses:
            raise AssertionError(f"unexpected POST to {url}")
        return self._post_responses.pop(0)

    async def get(self, url, headers=None):
        self.gets.append({"url": url, "headers": headers})
        if not self._get_responses:
            raise AssertionError(f"unexpected GET to {url}")
        return self._get_responses.pop(0)


@pytest.fixture
def fake_httpx(monkeypatch):
    """Patch `httpx.AsyncClient` to return our scriptable fake."""
    import httpx
    instance_holder: dict = {"client": None}

    def _factory(*args, **kwargs):
        client = _FakeAsyncClient(*args, **kwargs)
        instance_holder["client"] = client
        return client

    monkeypatch.setattr(httpx, "AsyncClient", _factory)
    return instance_holder


class TestSubmitHappyPath:
    @pytest.mark.asyncio
    async def test_submit_succeeds_after_polling(self, configured_fallback, fake_httpx):
        # POST /infer → 202 { job_id }
        # GET  /infer/{id} → running, then succeeded
        client = _FakeAsyncClient()
        client.queue_post(_make_response(200, {"job_id": "JOB-123"}))
        client.queue_get(_make_response(200, {"status": "running"}))
        client.queue_get(_make_response(200, {
            "status": "succeeded",
            "result": {
                "preds_url": "gs://bucket/preds.npy",
                "top_rois": ["V1", "FFA", "STG"],
                "peak_t": 17,
                "seconds_elapsed": 4.5,
            },
        }))

        # Inject our pre-baked client
        import httpx
        def _factory(*args, **kwargs):
            return client
        import unittest.mock as _mock
        with _mock.patch.object(httpx, "AsyncClient", _factory):
            # Make the poll interval and timeout small for fast tests
            configured_fallback.POLL_INTERVAL_S = 0.0
            result = await configured_fallback.submit("/tmp/clip.mp4")

        assert isinstance(result, RemoteInferenceResult)
        assert result.top_rois == ["V1", "FFA", "STG"]
        assert result.peak_t == 17
        assert result.seconds_elapsed == 4.5
        assert result.fallback_used == "gcp_a100"
        assert result.job_id == "JOB-123"

        # Verify auth header on submit
        assert client.posts[0]["headers"]["Authorization"] == "Bearer secret"
        assert client.posts[0]["json"]["media_path"] == "/tmp/clip.mp4"


class TestSubmitFailures:
    @pytest.mark.asyncio
    async def test_worker_reports_failure(self, configured_fallback):
        client = _FakeAsyncClient()
        client.queue_post(_make_response(200, {"job_id": "J"}))
        client.queue_get(_make_response(200, {
            "status": "failed",
            "error": {"message": "TRIBE crashed"},
        }))

        import unittest.mock as _mock

        import httpx
        with _mock.patch.object(httpx, "AsyncClient", lambda *a, **kw: client):
            configured_fallback.POLL_INTERVAL_S = 0.0
            result = await configured_fallback.submit("/tmp/x.mp4")

        assert isinstance(result, CortexError)
        assert result.code is ErrorCode.INFERENCE_FAILED
        assert "TRIBE crashed" in result.message
        assert result.fallback_used == "gcp_a100"

    @pytest.mark.asyncio
    async def test_job_disappeared_returns_preempted(self, configured_fallback):
        client = _FakeAsyncClient()
        client.queue_post(_make_response(200, {"job_id": "J"}))
        client.queue_get(_make_response(404))

        import unittest.mock as _mock

        import httpx
        with _mock.patch.object(httpx, "AsyncClient", lambda *a, **kw: client):
            configured_fallback.POLL_INTERVAL_S = 0.0
            result = await configured_fallback.submit("/tmp/x.mp4")

        assert isinstance(result, CortexError)
        assert result.code is ErrorCode.GCP_PREEMPTED
        assert result.retry is True

    @pytest.mark.asyncio
    async def test_missing_job_id_returns_inference_failed(self, configured_fallback):
        client = _FakeAsyncClient()
        client.queue_post(_make_response(200, {}))   # no job_id

        import unittest.mock as _mock

        import httpx
        with _mock.patch.object(httpx, "AsyncClient", lambda *a, **kw: client):
            result = await configured_fallback.submit("/tmp/x.mp4")

        assert isinstance(result, CortexError)
        assert result.code is ErrorCode.INFERENCE_FAILED

    @pytest.mark.asyncio
    async def test_malformed_result_returns_inference_failed(self, configured_fallback):
        client = _FakeAsyncClient()
        client.queue_post(_make_response(200, {"job_id": "J"}))
        client.queue_get(_make_response(200, {
            "status": "succeeded",
            "result": {"preds_url": "gs://x"},  # missing top_rois, peak_t
        }))

        import unittest.mock as _mock

        import httpx
        with _mock.patch.object(httpx, "AsyncClient", lambda *a, **kw: client):
            configured_fallback.POLL_INTERVAL_S = 0.0
            result = await configured_fallback.submit("/tmp/x.mp4")

        assert isinstance(result, CortexError)
        assert result.code is ErrorCode.INFERENCE_FAILED


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    @pytest.mark.asyncio
    async def test_health_passes_through_response(self, configured_fallback):
        client = _FakeAsyncClient()
        client.queue_get(_make_response(200, {"ok": True, "gpu": "a100-40gb", "queue_depth": 0}))

        import unittest.mock as _mock

        import httpx
        with _mock.patch.object(httpx, "AsyncClient", lambda *a, **kw: client):
            health = await configured_fallback.health()

        assert health["available"] is True
        assert health["gpu"] == "a100-40gb"

    @pytest.mark.asyncio
    async def test_health_unavailable_when_not_configured(self, monkeypatch):
        monkeypatch.delenv("GCP_INFERENCE_ENDPOINT", raising=False)
        h = await GCPInferenceFallback().health()
        assert h["available"] is False


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class TestDefaultFallback:
    def test_returns_null_when_unconfigured(self, monkeypatch):
        monkeypatch.delenv("GCP_INFERENCE_ENDPOINT", raising=False)
        monkeypatch.delenv("GCP_INFERENCE_TOKEN", raising=False)
        fb = default_fallback()
        assert isinstance(fb, NullFallback)
        assert fb.available() is False

    def test_returns_gcp_when_configured(self, monkeypatch):
        monkeypatch.setenv("GCP_INFERENCE_ENDPOINT", "https://x")
        monkeypatch.setenv("GCP_INFERENCE_TOKEN", "tok")
        fb = default_fallback()
        assert isinstance(fb, GCPInferenceFallback)
        assert fb.available() is True


class TestRaiseForError:
    def test_returns_result_unchanged(self):
        result = RemoteInferenceResult(
            preds_url="gs://x", top_rois=[], peak_t=0, seconds_elapsed=0.0,
        )
        assert raise_for_error(result) is result

    def test_raises_on_error(self):
        err = CortexError(code=ErrorCode.CUDA_OOM, message="x", component="c")
        with pytest.raises(CortexException):
            raise_for_error(err)
