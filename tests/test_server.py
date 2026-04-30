"""Unit tests for the Cortex FastAPI server."""
from __future__ import annotations

import asyncio
import io
import sys
import unittest.mock as mock
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stub out heavy deps before importing server
# ---------------------------------------------------------------------------

_fake_analysis = mock.MagicMock()
_fake_gpu_scheduler = mock.MagicMock()
_fake_request_queue = mock.MagicMock()
_fake_media_gate = mock.MagicMock()
_fake_prompts = mock.MagicMock()
_fake_tiers = mock.MagicMock()

_fake_prompts.TIER_USER_TEMPLATE = "{label}\n{brain_context}"
_fake_prompts.ALL_TIER_SYSTEMS = ["sys"] * 7
_fake_tiers._TIER_NUM_PREDICT = [120, 180, 300, 350, 420, 550, 700]
_fake_tiers._TIER_TEMPERATURE = [0.55, 0.5, 0.4, 0.4, 0.38, 0.32, 0.28]

_STUB_MODULES = {
    "cortex.analysis": _fake_analysis,
    "cortex.gpu_scheduler": _fake_gpu_scheduler,
    "cortex.request_queue": _fake_request_queue,
    "cortex.media_gate": _fake_media_gate,
    "cortex.prompts": _fake_prompts,
    "cortex.tiers": _fake_tiers,
}

with mock.patch.dict("sys.modules", _STUB_MODULES):
    from cortex.gpu_scheduler import GPUScheduler, GPUState, get_scheduler  # noqa: F401 — actually the mock
    from cortex.request_queue import RequestQueue, RequestType, get_queue    # noqa: F401


# ---------------------------------------------------------------------------
# In-memory fakes for GPUScheduler and RequestQueue
# ---------------------------------------------------------------------------

class FakeScheduler:
    def __init__(self):
        self.state = mock.MagicMock()
        self.state.value = "idle"

    def vram_report(self) -> dict[str, Any]:
        return {"free_gb": 30.0, "used_gb": 2.0}

    def on_state_change(self, cb) -> None:
        pass


class FakeQueue:
    def status(self) -> dict[str, Any]:
        return {"pending": 0, "active": 0}

    async def submit(self, **kwargs) -> str:
        return "mock narration"


# ---------------------------------------------------------------------------
# App fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def app():
    with mock.patch.dict("sys.modules", _STUB_MODULES):
        import importlib
        import webapp.server as _srv
        importlib.reload(_srv)
        _app = _srv.create_app(
            queue=FakeQueue(),
            scheduler=FakeScheduler(),
        )
    return _app


@pytest.fixture
async def client(app):
    from httpx import ASGITransport, AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True


@pytest.mark.asyncio
async def test_info(client):
    r = await client.get("/api/info")
    assert r.status_code == 200
    data = r.json()
    assert data["tribe_v2"]["tr_seconds"] == 0.5
    assert data["tribe_v2"]["n_vertices"] == 20484


@pytest.mark.asyncio
async def test_submit_scan_mp4(client):
    with mock.patch("asyncio.create_task"):
        r = await client.post(
            "/api/scan",
            files={"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")},
            data={"tier": "1", "source": "test"},
        )
    assert r.status_code == 202
    data = r.json()
    assert data["ok"] is True
    assert "scan_id" in data


@pytest.mark.asyncio
async def test_get_scan_not_found(client):
    r = await client.get("/api/scan/nonexistent000")
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_submit_scan_exe_rejected(client):
    r = await client.post(
        "/api/scan",
        files={"file": ("malware.exe", io.BytesIO(b"\x00" * 64), "application/octet-stream")},
        data={"tier": "1"},
    )
    assert r.status_code == 400


@pytest.mark.asyncio
async def test_submit_scan_oversized(client):
    large = io.BytesIO(b"\x00" * (51 * 1024 * 1024))
    r = await client.post(
        "/api/scan",
        files={"file": ("big.mp4", large, "video/mp4")},
        data={"tier": "1"},
    )
    assert r.status_code == 413
