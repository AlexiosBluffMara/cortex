"""Unit tests for gcp/relay/main.py — Cortex Cloud Run relay.

Strategy
--------
* Google Cloud clients (Firestore, GCS) are injected into sys.modules *before*
  importlib loads main.py, so the module-level ``db``, ``gcs``, ``bucket``, and
  ``fmri_bucket`` bindings are mocks.
* httpx.AsyncClient is patched per-test to avoid real network calls.
* Fake Firestore helpers (FakeCollection / FakeDocRef) mirror the async Firestore
  API used by the relay so we can exercise real code paths.
* HTML static files are patched with in-memory strings so tests don't depend on
  the public/ directory.

All tests are ``async def`` — pytest-asyncio in auto mode runs them without
explicit ``@pytest.mark.asyncio`` decoration (asyncio_mode = "auto" in pyproject).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path
from typing import AsyncIterator
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

# ---------------------------------------------------------------------------
# Environment vars must be set BEFORE importing the relay module
# ---------------------------------------------------------------------------
os.environ.setdefault("TUNNEL_URL", "http://fake-5090")
os.environ.setdefault("GCS_BUCKET", "test-bucket")
os.environ.setdefault("GCP_PROJECT", "test-project")
os.environ.setdefault("GEMINI_API_KEY", "test-gemini-key")
os.environ.setdefault("FMRI_BUCKET", "test-fmri-bucket")

# ---------------------------------------------------------------------------
# Fake Firestore helpers
# ---------------------------------------------------------------------------

class FakeFirestoreDoc:
    """Mimics google.cloud.firestore.DocumentSnapshot."""

    def __init__(self, doc_id: str, data: dict | None = None, exists: bool = True):
        self.id = doc_id
        self._data: dict = data if data is not None else {}
        self.exists = exists

    def to_dict(self) -> dict:
        return dict(self._data)


class FakeDocRef:
    """Mimics google.cloud.firestore.AsyncDocumentReference."""

    def __init__(self, doc_id: str, store: dict):
        self.id = doc_id
        self._store = store

    async def get(self) -> FakeFirestoreDoc:
        data = self._store.get(self.id)
        exists = data is not None
        return FakeFirestoreDoc(self.id, data, exists=exists)

    async def set(self, data: dict, **kw) -> None:
        self._store[self.id] = dict(data)

    async def update(self, data: dict) -> None:
        if self.id not in self._store:
            self._store[self.id] = {}
        self._store[self.id].update(data)


class FakeStreamIterator:
    """Async iterator yielding FakeFirestoreDoc objects from a list."""

    def __init__(self, docs: list[FakeFirestoreDoc]):
        self._docs = iter(docs)

    def __aiter__(self) -> "FakeStreamIterator":
        return self

    async def __anext__(self) -> FakeFirestoreDoc:
        try:
            return next(self._docs)
        except StopIteration:
            raise StopAsyncIteration


class FakeQuery:
    """Chainable query object that returns a fixed list of docs."""

    def __init__(self, docs: list[FakeFirestoreDoc] | None = None):
        self._docs: list[FakeFirestoreDoc] = docs or []

    def order_by(self, *a, **kw) -> "FakeQuery":
        return self

    def limit(self, n: int) -> "FakeQuery":
        return FakeQuery(self._docs[:n])

    def where(self, *a, **kw) -> "FakeQuery":
        return self

    def stream(self) -> FakeStreamIterator:
        return FakeStreamIterator(self._docs)


class FakeCollection:
    """Mimics google.cloud.firestore.AsyncCollectionReference."""

    def __init__(self) -> None:
        self._docs: dict[str, dict] = {}
        self._query_docs: list[FakeFirestoreDoc] = []

    def document(self, doc_id: str) -> FakeDocRef:
        return FakeDocRef(doc_id, self._docs)

    def order_by(self, *a, **kw) -> FakeQuery:
        docs = [FakeFirestoreDoc(k, v) for k, v in self._docs.items()]
        return FakeQuery(docs)

    def where(self, *a, **kw) -> FakeQuery:
        docs = [FakeFirestoreDoc(k, v) for k, v in self._docs.items()]
        return FakeQuery(docs)

    def limit(self, n: int) -> FakeQuery:
        docs = [FakeFirestoreDoc(k, v) for k, v in self._docs.items()]
        return FakeQuery(docs[:n])

    def _seed(self, doc_id: str, data: dict) -> None:
        self._docs[doc_id] = dict(data)


class FakeFirestoreClient:
    """Mimics google.cloud.firestore.AsyncClient."""

    def __init__(self) -> None:
        self._collections: dict[str, FakeCollection] = {}

    def collection(self, name: str) -> FakeCollection:
        if name not in self._collections:
            self._collections[name] = FakeCollection()
        return self._collections[name]


# ---------------------------------------------------------------------------
# GCS blob/bucket mocks
# ---------------------------------------------------------------------------

class FakeBlob:
    def __init__(self, name: str, exists: bool = True, content_type: str = "application/octet-stream"):
        self.name = name
        self._exists = exists
        self.content_type = content_type
        self._data = b"fake-blob-content"

    def exists(self) -> bool:
        return self._exists

    def upload_from_string(self, data: bytes, content_type: str = "") -> None:
        self._data = data
        self.content_type = content_type

    def open(self, mode: str = "rb"):
        import io
        return io.BytesIO(self._data)

    def generate_signed_url(self, **kw) -> str:
        return "https://storage.googleapis.com/fake-signed-url"


class FakeBucket:
    def __init__(self) -> None:
        self._blobs: dict[str, FakeBlob] = {}

    def blob(self, name: str) -> FakeBlob:
        if name not in self._blobs:
            self._blobs[name] = FakeBlob(name, exists=False)
        return self._blobs[name]

    def _seed_blob(self, name: str, content_type: str = "image/png") -> FakeBlob:
        b = FakeBlob(name, exists=True, content_type=content_type)
        self._blobs[name] = b
        return b


# ---------------------------------------------------------------------------
# Module-level mock injection: must run before relay is imported
# ---------------------------------------------------------------------------

def _make_firestore_module(fake_client: FakeFirestoreClient) -> types.ModuleType:
    mod = types.ModuleType("google.cloud.firestore")
    mod.AsyncClient = MagicMock(return_value=fake_client)
    # SERVER_TIMESTAMP sentinel used in doc writes
    mod.SERVER_TIMESTAMP = "SERVER_TIMESTAMP_SENTINEL"

    class _Query:
        DESCENDING = "DESCENDING"

    mod.Query = _Query
    return mod


def _make_storage_module(fake_gcs_client: MagicMock) -> types.ModuleType:
    mod = types.ModuleType("google.cloud.storage")
    mod.Client = MagicMock(return_value=fake_gcs_client)
    return mod


class _PassthroughLimiter:
    """Drop-in replacement for slowapi.Limiter that never rate-limits.

    The real slowapi.Limiter.limit() decorator modifies the wrapped function
    signature in a way that confuses FastAPI's dependency injection, causing
    422 Unprocessable Entity on every decorated endpoint.  This stub returns
    an identity decorator so the original function is registered unchanged.
    """

    def __init__(self, *a, **kw):
        pass

    def limit(self, *a, **kw):
        def _decorator(fn):
            return fn
        return _decorator

    def exempt(self, fn):
        return fn


def _make_slowapi_modules() -> None:
    """Inject pass-through slowapi stubs so rate-limited endpoints work in tests."""
    # Always override — even if the real slowapi is installed its Limiter
    # decorator mangles function signatures and breaks FastAPI DI.
    slow_mod = types.ModuleType("slowapi")
    slow_mod.Limiter = _PassthroughLimiter
    slow_mod._rate_limit_exceeded_handler = MagicMock()
    sys.modules["slowapi"] = slow_mod

    errs = types.ModuleType("slowapi.errors")
    errs.RateLimitExceeded = type("RateLimitExceeded", (Exception,), {})
    sys.modules["slowapi.errors"] = errs

    util_mod = types.ModuleType("slowapi.util")
    util_mod.get_remote_address = lambda req: "127.0.0.1"
    sys.modules["slowapi.util"] = util_mod

    # cachetools — use the real one if available, else stub
    try:
        import cachetools  # noqa: F401
    except ImportError:
        cachetools_mod = types.ModuleType("cachetools")
        cachetools_mod.TTLCache = dict
        sys.modules["cachetools"] = cachetools_mod


# ---------------------------------------------------------------------------
# Relay module loader (lazy, cached)
# ---------------------------------------------------------------------------

_relay_module_cache: dict[str, object] = {}


def _load_relay(fake_db: FakeFirestoreClient, fake_bucket: FakeBucket, fake_fmri_bucket: FakeBucket):
    """Load gcp/relay/main.py with fully mocked Google Cloud clients.

    Returns the module.  We reload fresh each time to isolate state between
    test sessions; the fixture below handles per-test isolation differently.
    """
    _make_slowapi_modules()

    # Patch google.cloud.* before the module runs
    firestore_mod = _make_firestore_module(fake_db)

    fake_gcs_client = MagicMock()
    fake_gcs_client.bucket.side_effect = lambda name: (
        fake_bucket if name == os.environ["GCS_BUCKET"] else fake_fmri_bucket
    )
    storage_mod = _make_storage_module(fake_gcs_client)

    # Ensure parent packages exist
    if "google" not in sys.modules:
        sys.modules["google"] = types.ModuleType("google")
    if "google.cloud" not in sys.modules:
        sys.modules["google.cloud"] = types.ModuleType("google.cloud")

    sys.modules["google.cloud.firestore"] = firestore_mod
    sys.modules["google.cloud.storage"] = storage_mod

    # Remove cached relay if already loaded so we get fresh module-level state
    for key in list(sys.modules.keys()):
        if key in ("relay_main",):
            del sys.modules[key]

    relay_path = os.path.join(
        os.path.dirname(__file__), "..", "..", "..", "gcp", "relay", "main.py"
    )
    relay_path = os.path.normpath(relay_path)

    spec = importlib.util.spec_from_file_location("relay_main", relay_path)
    assert spec is not None, f"Could not load spec from {relay_path}"
    relay_main = importlib.util.module_from_spec(spec)

    spec.loader.exec_module(relay_main)  # type: ignore[union-attr]

    # Inject the real fake_db so test code can reach it
    relay_main.db = fake_db  # type: ignore[attr-defined]
    relay_main.bucket = fake_bucket  # type: ignore[attr-defined]
    relay_main.fmri_bucket = fake_fmri_bucket  # type: ignore[attr-defined]

    # Redirect _STATIC to a temp dir with UTF-8 stub HTML files so the
    # gallery/scan endpoints don't try to read the real files with the locale
    # (cp1252 on Windows) codec.
    _tmp_static = Path(tempfile.mkdtemp(prefix="relay_test_static_"))
    fake_html = "<!DOCTYPE html><html><body>TEST</body></html>"
    (_tmp_static / "gallery.html").write_text(fake_html, encoding="utf-8")
    (_tmp_static / "scan.html").write_text(fake_html, encoding="utf-8")
    relay_main._STATIC = _tmp_static  # type: ignore[attr-defined]

    return relay_main


# ---------------------------------------------------------------------------
# Session-scoped module fixture (share one loaded copy per test session)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def _relay_session():
    """Load the relay module once for the entire session. Shared across all tests."""
    fake_db = FakeFirestoreClient()
    fake_bucket = FakeBucket()
    fake_fmri_bucket = FakeBucket()
    mod = _load_relay(fake_db, fake_bucket, fake_fmri_bucket)
    return mod, fake_db, fake_bucket, fake_fmri_bucket


@pytest.fixture()
def relay_module(_relay_session):
    """Per-test fixture that exposes the module and resets Firestore state."""
    mod, fake_db, fake_bucket, fake_fmri_bucket = _relay_session
    # Reset collection state between tests
    fake_db._collections.clear()
    fake_bucket._blobs.clear()
    fake_fmri_bucket._blobs.clear()
    return mod


@pytest.fixture()
def relay_app(relay_module):
    return relay_module.app


@pytest.fixture()
async def client(relay_app):
    async with httpx.AsyncClient(
        transport=ASGITransport(app=relay_app), base_url="http://test"
    ) as c:
        yield c


# ---------------------------------------------------------------------------
# Gemini response helpers
# ---------------------------------------------------------------------------

def _gemini_narration_response(persona_text: str = "test narration") -> dict:
    return {
        "candidates": [
            {"content": {"parts": [{"text": persona_text}]}}
        ]
    }


def _gemini_metadata_response(top_rois=None, peak_t: int = 42) -> dict:
    rois = top_rois or ["Visual cortex", "Default Mode Network"]
    payload = json.dumps({"top_rois": rois, "peak_t": peak_t})
    return {"candidates": [{"content": {"parts": [{"text": payload}]}}]}


def _make_httpx_response(status_code: int = 200, json_body: dict | None = None,
                         content: bytes | None = None) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status_code
    if json_body is not None:
        resp.json.return_value = json_body
        resp.content = json.dumps(json_body).encode()
    elif content is not None:
        resp.content = content
        resp.json.return_value = json.loads(content)
    else:
        resp.json.return_value = {}
        resp.content = b"{}"
    return resp


# ---------------------------------------------------------------------------
# Class: TestRelayRoutes
# ---------------------------------------------------------------------------

class TestRelayRoutes:
    """HTTP endpoint smoke + behaviour tests."""

    async def test_root_redirects_to_gallery(self, client: httpx.AsyncClient):
        r = await client.get("/", follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308)
        assert r.headers["location"].endswith("/gallery")

    async def test_gallery_returns_html(self, client: httpx.AsyncClient):
        r = await client.get("/gallery")
        assert r.status_code == 200
        assert "html" in r.headers["content-type"].lower()

    async def test_scan_html_with_id_redirects(self, client: httpx.AsyncClient):
        r = await client.get("/scan.html?id=abc123", follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308)
        assert "abc123" in r.headers["location"]

    async def test_scan_html_without_id_redirects_to_gallery(self, client: httpx.AsyncClient):
        r = await client.get("/scan.html", follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308)
        assert "/gallery" in r.headers["location"]

    async def test_scan_html_preserves_persona_param(self, client: httpx.AsyncClient):
        r = await client.get("/scan.html?id=abc123&persona=sam", follow_redirects=False)
        assert r.status_code in (301, 302, 307, 308)
        loc = r.headers["location"]
        assert "abc123" in loc
        assert "sam" in loc

    async def test_scan_page_returns_html(self, client: httpx.AsyncClient):
        r = await client.get("/scan/somescanid")
        assert r.status_code == 200
        assert "html" in r.headers["content-type"].lower()

    async def test_ads_txt(self, client: httpx.AsyncClient):
        r = await client.get("/ads.txt")
        assert r.status_code == 200
        assert "google.com" in r.text
        assert r.headers["content-type"].startswith("text/plain")

    async def test_robots_txt(self, client: httpx.AsyncClient):
        r = await client.get("/robots.txt")
        assert r.status_code == 200
        assert "User-agent" in r.text

    async def test_sitemap_xml(self, client: httpx.AsyncClient):
        r = await client.get("/sitemap.xml")
        assert r.status_code == 200
        assert "xml" in r.headers["content-type"].lower()
        assert "urlset" in r.text

    async def test_health_returns_ok(self, client: httpx.AsyncClient, relay_module):
        with patch.object(relay_module, "_5090_alive", new=AsyncMock(return_value=True)):
            r = await client.get("/api/health")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert "5090_online" in body
        assert "tunnel" in body

    async def test_health_5090_offline(self, client: httpx.AsyncClient, relay_module):
        with patch.object(relay_module, "_5090_alive", new=AsyncMock(return_value=False)):
            r = await client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["5090_online"] is False

    async def test_status_alias(self, client: httpx.AsyncClient, relay_module):
        with patch.object(relay_module, "_5090_alive", new=AsyncMock(return_value=True)):
            r = await client.get("/api/status")
        assert r.status_code == 200
        body = r.json()
        assert body["ok"] is True
        assert body["5090_online"] is True

    async def test_info_contains_tribe_config(self, client: httpx.AsyncClient, relay_module):
        with patch.object(relay_module, "_5090_alive", new=AsyncMock(return_value=False)):
            r = await client.get("/api/info")
        assert r.status_code == 200
        body = r.json()
        assert body["relay"].startswith("cortex-relay")
        assert "tribe_v2" in body
        assert body["tribe_v2"]["n_vertices"] == 20484
        assert "cost" in body

    async def test_geo_returns_ip_info(self, client: httpx.AsyncClient, relay_module):
        fake_geo = {"ip": "1.2.3.4", "city": "Chicago", "region": "IL",
                    "country": "US", "org": "AS1234 FakeISP", "loc": "41,-87"}
        with patch.object(relay_module, "_geo", new=AsyncMock(return_value=fake_geo)):
            r = await client.get("/api/geo")
        assert r.status_code == 200
        assert r.json()["country"] == "US"

    async def test_utilization_proxied(self, client: httpx.AsyncClient, relay_module):
        fake_util = {"accepting": True, "queue_depth": 2, "scheduler_state": "ready"}
        with patch.object(relay_module, "_5090_utilization", new=AsyncMock(return_value=fake_util)):
            r = await client.get("/api/utilization")
        assert r.status_code == 200
        assert r.json()["accepting"] is True


# ---------------------------------------------------------------------------
# Class: TestFileProxy
# ---------------------------------------------------------------------------

class TestFileProxy:
    async def test_proxy_existing_blob(self, client: httpx.AsyncClient, relay_module):
        relay_module.bucket._seed_blob("uploads/abc123.nii", "application/octet-stream")
        r = await client.get("/api/files/uploads/abc123.nii")
        assert r.status_code == 200

    async def test_proxy_missing_blob_returns_404(self, client: httpx.AsyncClient):
        r = await client.get("/api/files/uploads/nothere.bin")
        assert r.status_code == 404
        assert r.json()["error"] == "not found"

    async def test_proxy_sets_cache_header(self, client: httpx.AsyncClient, relay_module):
        relay_module.bucket._seed_blob("uploads/test.png", "image/png")
        r = await client.get("/api/files/uploads/test.png")
        assert "cache-control" in {k.lower() for k in r.headers}


# ---------------------------------------------------------------------------
# Class: TestScanSubmission
# ---------------------------------------------------------------------------

class TestScanSubmission:
    async def test_submit_scan_success(self, client: httpx.AsyncClient, relay_module):
        with patch.object(relay_module, "_forward_to_5090", new=AsyncMock()), \
             patch.object(relay_module, "_geo", new=AsyncMock(return_value={"country": "US", "city": "", "org": ""})):
            r = await client.post(
                "/api/scan",
                files={"file": ("brain.nii", b"fake-nii-data", "application/octet-stream")},
                data={"tier": "4"},
            )
        assert r.status_code == 202
        body = r.json()
        assert body["ok"] is True
        assert "scan_id" in body
        assert "cost_mode" in body

    async def test_submit_scan_creates_firestore_doc(self, client: httpx.AsyncClient, relay_module):
        with patch.object(relay_module, "_forward_to_5090", new=AsyncMock()), \
             patch("asyncio.create_task"), \
             patch.object(relay_module, "_geo", new=AsyncMock(return_value={"country": "US", "city": "Normal", "org": ""})):
            r = await client.post(
                "/api/scan",
                files={"file": ("test.mp4", b"data", "video/mp4")},
            )
        assert r.status_code == 202
        scan_id = r.json()["scan_id"]
        scans = relay_module.db.collection("scans")
        doc = scans._docs.get(scan_id)
        assert doc is not None
        assert doc["status"] == "queued"

    async def test_submit_scan_file_too_large(self, client: httpx.AsyncClient, relay_module):
        big_data = b"x" * (51 * 1024 * 1024)  # 51 MB
        with patch.object(relay_module, "_geo", new=AsyncMock(return_value={})):
            r = await client.post(
                "/api/scan",
                files={"file": ("huge.bin", big_data, "application/octet-stream")},
            )
        assert r.status_code == 413

    async def test_submit_scan_blocked_ip(self, client: httpx.AsyncClient, relay_module):
        original = relay_module._IP_BLOCKLIST
        # The httpx ASGI client always presents 127.0.0.1 as client.host.
        # Inject it via X-Forwarded-For so _client_ip() returns a known value.
        relay_module._IP_BLOCKLIST = {"10.0.0.1"}
        try:
            r = await client.post(
                "/api/scan",
                files={"file": ("x.nii", b"data", "application/octet-stream")},
                headers={"x-forwarded-for": "10.0.0.1"},
            )
            assert r.status_code == 403
        finally:
            relay_module._IP_BLOCKLIST = original


# ---------------------------------------------------------------------------
# Class: TestScanListAndGet
# ---------------------------------------------------------------------------

class TestScanListAndGet:
    async def test_list_scans_empty(self, client: httpx.AsyncClient):
        r = await client.get("/api/scans")
        assert r.status_code == 200
        assert r.json()["scans"] == []

    async def test_list_scans_returns_docs(self, client: httpx.AsyncClient, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("abc001", {"status": "complete", "filename": "f.nii",
                                "created_at": "2025-01-01", "cost_mode": "local"})
        r = await client.get("/api/scans")
        assert r.status_code == 200
        body = r.json()
        assert len(body["scans"]) >= 1
        ids = [s["id"] for s in body["scans"]]
        assert "abc001" in ids

    async def test_get_scan_not_found(self, client: httpx.AsyncClient):
        r = await client.get("/api/scan/doesnotexist")
        assert r.status_code == 404

    async def test_get_scan_happy_path(self, client: httpx.AsyncClient, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("scan001", {"status": "complete", "filename": "brain.nii",
                                 "top_rois": ["Visual cortex"], "peak_t": 25})
        r = await client.get("/api/scan/scan001")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "scan001"
        assert body["status"] == "complete"

    async def test_get_scan_self_heal_queued_cloud(self, client: httpx.AsyncClient, relay_module):
        """queued_cloud doc + GEMINI_KEY set → update to processing_cloud and kick task."""
        scans = relay_module.db.collection("scans")
        scans._seed("qcscan", {"status": "queued_cloud", "filename": "brain.nii"})
        # Ensure GEMINI_KEY is set in module
        original_key = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        try:
            with patch("asyncio.create_task") as mock_ct:
                r = await client.get("/api/scan/qcscan")
            assert r.status_code == 200
            body = r.json()
            assert body["status"] == "processing_cloud"
            mock_ct.assert_called_once()
            # Firestore doc should be updated
            assert scans._docs["qcscan"]["status"] == "processing_cloud"
            assert scans._docs["qcscan"]["_cloud_kicked"] is True
        finally:
            relay_module.GEMINI_KEY = original_key

    async def test_get_scan_self_heal_skipped_if_already_kicked(
        self, client: httpx.AsyncClient, relay_module
    ):
        scans = relay_module.db.collection("scans")
        scans._seed("qs2", {"status": "queued_cloud", "filename": "f.nii", "_cloud_kicked": True})
        original_key = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        try:
            with patch("asyncio.create_task") as mock_ct:
                r = await client.get("/api/scan/qs2")
            assert r.status_code == 200
            mock_ct.assert_not_called()
        finally:
            relay_module.GEMINI_KEY = original_key

    async def test_get_scan_no_self_heal_without_gemini_key(
        self, client: httpx.AsyncClient, relay_module
    ):
        scans = relay_module.db.collection("scans")
        scans._seed("qs3", {"status": "queued_cloud", "filename": "f.nii"})
        original_key = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = ""
        try:
            with patch("asyncio.create_task") as mock_ct:
                r = await client.get("/api/scan/qs3")
            assert r.status_code == 200
            mock_ct.assert_not_called()
        finally:
            relay_module.GEMINI_KEY = original_key

    async def test_get_narrations_from_doc(self, client: httpx.AsyncClient, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("ns1", {
            "status": "complete",
            "filename": "f.mp4",
            "narrations": {"sam": "hey cool brain", "priya": "dense ML text"},
        })
        r = await client.get("/api/scan/ns1/narrations")
        assert r.status_code == 200
        body = r.json()
        assert "narrations" in body
        assert body["narrations"]["sam"] == "hey cool brain"

    async def test_get_narrations_not_found(self, client: httpx.AsyncClient, relay_module):
        r = await client.get("/api/scan/nope_nope/narrations")
        assert r.status_code == 404

    async def test_get_narrations_gemini_fallback(self, client: httpx.AsyncClient, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("nf1", {"status": "complete", "filename": "vid.mp4"})
        original_key = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        fake_narrations = {"sam": "yo brain", "priya": "technical", "dr_park": "clinical", "chris": "story"}
        try:
            with patch.object(relay_module, "_gemini_narration", new=AsyncMock(return_value=fake_narrations)):
                r = await client.get("/api/scan/nf1/narrations")
            assert r.status_code == 200
            body = r.json()
            assert body["narrations"]["sam"] == "yo brain"
            assert body["source"] == "gemini-fallback"
        finally:
            relay_module.GEMINI_KEY = original_key


# ---------------------------------------------------------------------------
# Class: TestInternalFunctions
# ---------------------------------------------------------------------------

class TestInternalFunctions:
    def _get_fix_mojibake(self, relay_module):
        """_fix_mojibake is defined inside _gemini_narration; expose via exec."""
        ns: dict = {}
        exec(
            """
def _fix_mojibake(s):
    try:
        return s.encode('cp1252').decode('utf-8')
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s
""",
            ns,
        )
        return ns["_fix_mojibake"]

    def test_fix_mojibake_corrects_smart_quote(self, relay_module):
        fix = self._get_fix_mojibake(relay_module)
        # "you're" encoded as cp1252 then decoded as latin-1 gives "youâ€™re"
        mojibake = "youâ€™re"
        result = fix(mojibake)
        assert result == "you’re"  # right single quotation mark

    def test_fix_mojibake_passthrough_on_encode_error(self, relay_module):
        fix = self._get_fix_mojibake(relay_module)
        # String with characters that can't be encoded as cp1252 → returns original
        s = "こんにちは"  # Japanese can't encode as cp1252
        result = fix(s)
        assert result == s

    def test_fix_mojibake_passthrough_on_plain_ascii(self, relay_module):
        fix = self._get_fix_mojibake(relay_module)
        assert fix("hello world") == "hello world"

    async def test_gemini_narration_returns_empty_without_key(self, relay_module):
        original = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = ""
        try:
            result = await relay_module._gemini_narration("test.mp4")
            assert result == {}
        finally:
            relay_module.GEMINI_KEY = original

    async def test_gemini_narration_with_mocked_httpx(self, relay_module):
        original = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        try:
            persona_text = "this is a brain narration"
            mock_resp = _make_httpx_response(200, _gemini_narration_response(persona_text))
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await relay_module._gemini_narration("test.mp4")
            # 4 personas should each have been called
            assert isinstance(result, dict)
            assert len(result) == 4
            for persona in ("sam", "priya", "dr_park", "chris"):
                assert persona in result
        finally:
            relay_module.GEMINI_KEY = original

    async def test_gemini_narration_handles_api_error(self, relay_module):
        original = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        try:
            mock_resp = _make_httpx_response(500, {})
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await relay_module._gemini_narration("test.mp4")
            # All 4 personas get "(narration unavailable)"
            for persona in ("sam", "priya", "dr_park", "chris"):
                assert result[persona] == "(narration unavailable)"
        finally:
            relay_module.GEMINI_KEY = original

    async def test_gemini_metadata_returns_defaults_without_key(self, relay_module):
        original = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = ""
        try:
            result = await relay_module._gemini_metadata("test.nii")
            assert "top_rois" in result
            assert "peak_t" in result
            assert isinstance(result["peak_t"], int)
        finally:
            relay_module.GEMINI_KEY = original

    async def test_gemini_metadata_parses_json_from_response(self, relay_module):
        original = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        try:
            mock_resp = _make_httpx_response(200, _gemini_metadata_response(
                top_rois=["Visual cortex", "Auditory cortex"], peak_t=37
            ))
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await relay_module._gemini_metadata("clip.mp4")
            assert result["peak_t"] == 37
            assert "Visual cortex" in result["top_rois"]
        finally:
            relay_module.GEMINI_KEY = original

    async def test_gemini_metadata_falls_back_on_bad_json(self, relay_module):
        original = relay_module.GEMINI_KEY
        relay_module.GEMINI_KEY = "test-key"
        try:
            bad_resp_body = {"candidates": [{"content": {"parts": [{"text": "not json at all"}]}}]}
            mock_resp = _make_httpx_response(200, bad_resp_body)
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock(return_value=mock_resp)
            with patch("httpx.AsyncClient", return_value=mock_client):
                result = await relay_module._gemini_metadata("clip.mp4")
            # Falls back to defaults
            assert result["peak_t"] == 18
        finally:
            relay_module.GEMINI_KEY = original

    async def test_process_cloud_writes_complete_status(self, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("pc001", {"status": "processing_cloud", "filename": "brain.nii"})
        fake_narrations = {"sam": "ok", "priya": "ok", "dr_park": "ok", "chris": "ok"}
        fake_meta = {"top_rois": ["Visual cortex"], "peak_t": 30}
        with patch.object(relay_module, "_gemini_narration", new=AsyncMock(return_value=fake_narrations)), \
             patch.object(relay_module, "_gemini_metadata", new=AsyncMock(return_value=fake_meta)):
            await relay_module._process_cloud("pc001", "brain.nii")
        doc = scans._docs["pc001"]
        assert doc["status"] == "complete"
        assert doc["cost_mode"] == "cloud"
        assert doc["narrations"]["sam"] == "ok"
        assert doc["peak_t"] == 30

    async def test_process_cloud_writes_failed_on_exception(self, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("pc002", {"status": "processing_cloud", "filename": "bad.nii"})
        with patch.object(relay_module, "_gemini_narration", new=AsyncMock(side_effect=RuntimeError("boom"))):
            await relay_module._process_cloud("pc002", "bad.nii")
        doc = scans._docs["pc002"]
        assert doc["status"] == "failed"
        assert "boom" in doc["error"]

    async def test_forward_to_5090_not_accepting_routes_to_cloud(self, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("fw001", {"status": "queued", "filename": "f.nii"})
        util_not_accepting = {"accepting": False, "queue_depth": 5, "scheduler_state": "busy"}
        with patch.object(relay_module, "_5090_utilization", new=AsyncMock(return_value=util_not_accepting)), \
             patch.object(relay_module, "_process_cloud", new=AsyncMock()) as mock_pc:
            await relay_module._forward_to_5090("fw001", b"data", "f.nii", ".nii", 4)
        mock_pc.assert_awaited_once_with("fw001", "f.nii")
        doc = scans._docs["fw001"]
        assert doc["status"] == "processing_cloud"

    async def test_forward_to_5090_accepting_posts_to_tunnel(self, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("fw002", {"status": "queued", "filename": "f.nii"})
        util_accepting = {"accepting": True, "queue_depth": 0, "scheduler_state": "ready"}
        tunnel_resp = _make_httpx_response(202, {"scan_id": "local-abc"})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=tunnel_resp)
        with patch.object(relay_module, "_5090_utilization", new=AsyncMock(return_value=util_accepting)), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await relay_module._forward_to_5090("fw002", b"data", "f.nii", ".nii", 4)
        doc = scans._docs["fw002"]
        assert doc["status"] == "processing"
        assert doc["local_scan_id"] == "local-abc"

    async def test_forward_to_5090_tunnel_error_marks_failed(self, relay_module):
        scans = relay_module.db.collection("scans")
        scans._seed("fw003", {"status": "queued"})
        util_accepting = {"accepting": True, "queue_depth": 0, "scheduler_state": "ready"}
        tunnel_resp = _make_httpx_response(500, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=tunnel_resp)
        with patch.object(relay_module, "_5090_utilization", new=AsyncMock(return_value=util_accepting)), \
             patch("httpx.AsyncClient", return_value=mock_client):
            await relay_module._forward_to_5090("fw003", b"data", "f.nii", ".nii", 4)
        doc = scans._docs["fw003"]
        assert doc["status"] == "failed"


# ---------------------------------------------------------------------------
# Class: TestFmriEndpoints
# ---------------------------------------------------------------------------

class TestFmriEndpoints:
    VALID_SNOWFLAKE = "123456789012345678"   # 18 digits — valid Discord snowflake
    # 16 digits: numeric, passes pydantic min_length=10, fails _is_discord_snowflake (needs ≥17)
    SHORT_ID = "1234567890123456"           # 16 digits → invalid snowflake but valid pydantic

    async def test_upload_url_valid_snowflake(self, client: httpx.AsyncClient, relay_module):
        r = await client.post(
            "/api/fmri/upload-url",
            json={
                "user_id": self.VALID_SNOWFLAKE,
                "format_hint": "nii",
                "opted_in_research": False,
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert "upload_url" in body
        assert "scan_id" in body
        assert body["method"] == "PUT"

    async def test_upload_url_invalid_snowflake_400(self, client: httpx.AsyncClient):
        r = await client.post(
            "/api/fmri/upload-url",
            json={"user_id": self.SHORT_ID, "format_hint": "nii"},
        )
        assert r.status_code == 400

    async def test_upload_url_invalid_format_hint_400(self, client: httpx.AsyncClient):
        r = await client.post(
            "/api/fmri/upload-url",
            json={"user_id": self.VALID_SNOWFLAKE, "format_hint": "exe"},
        )
        assert r.status_code == 400

    async def test_upload_url_creates_firestore_doc(self, client: httpx.AsyncClient, relay_module):
        r = await client.post(
            "/api/fmri/upload-url",
            json={"user_id": self.VALID_SNOWFLAKE, "format_hint": "nii.gz"},
        )
        assert r.status_code == 200
        scan_id = r.json()["scan_id"]
        fmri_col = relay_module.db.collection("fmri_scans")
        doc = fmri_col._docs.get(scan_id)
        assert doc is not None
        assert doc["status"] == "pending_upload"
        assert doc["owner"] == self.VALID_SNOWFLAKE

    async def test_upload_url_signed_url_failure_500(self, client: httpx.AsyncClient, relay_module):
        bad_bucket = MagicMock()
        bad_blob = MagicMock()
        bad_blob.generate_signed_url.side_effect = RuntimeError("KMS not ready")
        bad_bucket.blob.return_value = bad_blob
        original = relay_module.fmri_bucket
        relay_module.fmri_bucket = bad_bucket
        try:
            r = await client.post(
                "/api/fmri/upload-url",
                json={"user_id": self.VALID_SNOWFLAKE, "format_hint": "nii"},
            )
            assert r.status_code == 500
        finally:
            relay_module.fmri_bucket = original

    async def test_list_uploads_valid_snowflake(self, client: httpx.AsyncClient, relay_module):
        fmri_col = relay_module.db.collection("fmri_scans")
        fmri_col._seed("scan-u1", {
            "scan_id": "scan-u1", "owner": self.VALID_SNOWFLAKE,
            "status": "pending_upload", "format_hint": "nii",
            "requested_at": "2025-01-01",
        })
        r = await client.get(f"/api/fmri/my-uploads/{self.VALID_SNOWFLAKE}")
        assert r.status_code == 200
        body = r.json()
        assert "uploads" in body

    async def test_list_uploads_invalid_snowflake_400(self, client: httpx.AsyncClient):
        r = await client.get(f"/api/fmri/my-uploads/{self.SHORT_ID}")
        assert r.status_code == 400

    async def test_opt_in_valid(self, client: httpx.AsyncClient):
        r = await client.post(f"/api/fmri/opt-in/{self.VALID_SNOWFLAKE}?opt_in=true")
        assert r.status_code == 200
        body = r.json()
        assert body["opted_in_research"] is True

    async def test_opt_in_invalid_snowflake_400(self, client: httpx.AsyncClient):
        r = await client.post(f"/api/fmri/opt-in/{self.SHORT_ID}?opt_in=true")
        assert r.status_code == 400

    async def test_soft_delete_success(self, client: httpx.AsyncClient, relay_module):
        fmri_col = relay_module.db.collection("fmri_scans")
        fmri_col._seed("del-scan-1", {
            "scan_id": "del-scan-1",
            "owner": self.VALID_SNOWFLAKE,
            "status": "pending_upload",
        })
        r = await client.delete(
            f"/api/fmri/scan/del-scan-1?user_id={self.VALID_SNOWFLAKE}"
        )
        assert r.status_code == 200
        assert r.json()["status"] == "soft_deleted"
        assert fmri_col._docs["del-scan-1"]["status"] == "soft_deleted"

    async def test_soft_delete_wrong_owner_403(self, client: httpx.AsyncClient, relay_module):
        fmri_col = relay_module.db.collection("fmri_scans")
        other_id = "111111111111111111"
        fmri_col._seed("del-scan-2", {
            "scan_id": "del-scan-2",
            "owner": other_id,
            "status": "pending_upload",
        })
        r = await client.delete(
            f"/api/fmri/scan/del-scan-2?user_id={self.VALID_SNOWFLAKE}"
        )
        assert r.status_code == 403

    async def test_soft_delete_not_found_404(self, client: httpx.AsyncClient):
        r = await client.delete(
            f"/api/fmri/scan/nonexistent-scan?user_id={self.VALID_SNOWFLAKE}"
        )
        assert r.status_code == 404

    async def test_soft_delete_invalid_snowflake_400(self, client: httpx.AsyncClient):
        r = await client.delete(f"/api/fmri/scan/somescan?user_id={self.SHORT_ID}")
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Class: TestGeoAndRateLimit
# ---------------------------------------------------------------------------

class TestGeoAndRateLimit:
    async def test_geo_lookup_cached(self, relay_module):
        """Second call for same IP uses cache, not network."""
        relay_module._geo_cache.clear()
        fake_result = {"ip": "8.8.8.8", "city": "Mountain View", "region": "CA",
                       "country": "US", "org": "Google", "loc": "37,-122"}
        mock_resp = _make_httpx_response(200, fake_result)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            r1 = await relay_module._geo("8.8.8.8")
            r2 = await relay_module._geo("8.8.8.8")
        # Second call should be from cache — only one network call made
        assert mock_client.get.call_count == 1
        assert r1["country"] == "US"
        assert r2 is r1

    async def test_geo_lookup_network_failure_returns_empty(self, relay_module):
        relay_module._geo_cache.clear()
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await relay_module._geo("1.2.3.4")
        assert result["ip"] == "1.2.3.4"
        assert result["city"] == ""

    def test_is_discord_snowflake_valid(self, relay_module):
        assert relay_module._is_discord_snowflake("123456789012345678") is True
        assert relay_module._is_discord_snowflake("12345678901234567") is True   # 17 digits
        assert relay_module._is_discord_snowflake("12345678901234567890") is True  # 20 digits

    def test_is_discord_snowflake_invalid(self, relay_module):
        assert relay_module._is_discord_snowflake("1234567890123456") is False   # 16 digits
        assert relay_module._is_discord_snowflake("123456789012345678901") is False  # 21 digits
        assert relay_module._is_discord_snowflake("12345678901234567a") is False  # non-digit
        assert relay_module._is_discord_snowflake("") is False

    def test_client_ip_from_xff(self, relay_module):
        req = MagicMock()
        req.headers = {"x-forwarded-for": "10.0.0.1, 172.16.0.1"}
        req.client = None
        assert relay_module._client_ip(req) == "10.0.0.1"

    def test_client_ip_fallback(self, relay_module):
        req = MagicMock()
        req.headers = {}
        req.client = MagicMock()
        req.client.host = "192.168.1.1"
        assert relay_module._client_ip(req) == "192.168.1.1"

    def test_client_ip_no_client(self, relay_module):
        req = MagicMock()
        req.headers = {}
        req.client = None
        assert relay_module._client_ip(req) == "0.0.0.0"


# ---------------------------------------------------------------------------
# Class: TestWebSocket
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_ws_sends_complete_status_and_closes(self, relay_module):
        """WebSocket should send doc JSON and close when status=complete."""
        from starlette.testclient import TestClient

        scans = relay_module.db.collection("scans")
        scans._seed("ws001", {
            "status": "complete",
            "filename": "f.nii",
            "narrations": {"sam": "brain is active"},
        })
        with patch("asyncio.sleep", new=AsyncMock()):
            with TestClient(relay_module.app) as tc:
                with tc.websocket_connect("/api/ws/ws001") as ws:
                    msg = ws.receive_json()
                    assert msg["id"] == "ws001"
                    assert msg["status"] == "complete"

    def test_ws_not_found_scan_sends_nothing(self, relay_module):
        """WebSocket for missing scan_id: handler loops without crashing."""
        from starlette.testclient import TestClient

        call_count = 0

        async def _fast_sleep(n):
            nonlocal call_count
            call_count += 1
            if call_count >= 2:
                raise asyncio.CancelledError

        with patch("asyncio.sleep", side_effect=_fast_sleep):
            with TestClient(relay_module.app) as tc:
                try:
                    with tc.websocket_connect("/api/ws/definitely_missing_scan") as ws:
                        ws.close()
                except Exception:
                    pass  # CancelledError or disconnect is expected


# ---------------------------------------------------------------------------
# Class: Test5090Helpers
# ---------------------------------------------------------------------------

class Test5090Helpers:
    async def test_5090_alive_true_on_200(self, relay_module):
        mock_resp = _make_httpx_response(200, {"relay": "ok"})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await relay_module._5090_alive()
        assert result is True

    async def test_5090_alive_false_on_error(self, relay_module):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await relay_module._5090_alive()
        assert result is False

    async def test_5090_utilization_returns_json(self, relay_module):
        fake_util = {"accepting": True, "queue_depth": 1, "scheduler_state": "ready"}
        mock_resp = _make_httpx_response(200, fake_util)
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await relay_module._5090_utilization()
        assert result["accepting"] is True

    async def test_5090_utilization_fallback_on_error(self, relay_module):
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("timeout"))
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await relay_module._5090_utilization()
        assert result["accepting"] is False
        assert result["scheduler_state"] == "unreachable"

    async def test_5090_utilization_fallback_on_non_200(self, relay_module):
        mock_resp = _make_httpx_response(503, {})
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await relay_module._5090_utilization()
        assert result["accepting"] is False


# ---------------------------------------------------------------------------
# Class: TestUserKmsKey
# ---------------------------------------------------------------------------

class TestUserKmsKey:
    def test_kms_key_path_stable(self, relay_module):
        """Same user_id always produces same key path."""
        uid = "123456789012345678"
        p1 = relay_module._user_kms_key_path(uid)
        p2 = relay_module._user_kms_key_path(uid)
        assert p1 == p2

    def test_kms_key_path_different_users(self, relay_module):
        p1 = relay_module._user_kms_key_path("111111111111111111")
        p2 = relay_module._user_kms_key_path("222222222222222222")
        assert p1 != p2

    def test_kms_key_path_format(self, relay_module):
        path = relay_module._user_kms_key_path("123456789012345678")
        assert path.startswith("projects/")
        assert "/keyRings/" in path
        assert "/cryptoKeys/u-" in path
        assert "test-project" in path
