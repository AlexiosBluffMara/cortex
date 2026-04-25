"""Integration tests for the FastAPI webapp.

Mocks the RequestQueue + GPUScheduler so we can exercise the HTTP surface
without spinning up Ollama or TRIBE v2.
"""
from __future__ import annotations

import io
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from cortex.gpu_scheduler import GPUState
from cortex.request_queue import RequestType

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_queue():
    q = MagicMock()
    q.status.return_value = {
        "queue_depth": 0,
        "processing": False,
        "active_request": None,
        "gpu_state": "idle",
        "completed": 0,
        "failed": 0,
    }

    async def _submit(request_type, payload, priority, source):
        if request_type == RequestType.BRAIN_SCAN:
            r = MagicMock()
            r.top_rois = ["ROI_1", "ROI_2"]
            r.peak_t = 42
            r.seconds_elapsed = 4.2
            return r
        return "narration text"

    q.submit = AsyncMock(side_effect=_submit)
    return q


@pytest.fixture
def fake_scheduler():
    s = MagicMock()
    s.state = GPUState.IDLE
    s.vram_report.return_value = {
        "state": "idle",
        "total_gb": 32.0,
        "used_gb": 2.0,
        "free_gb": 29.5,
        "tribe_fits": True,
        "gemma_e4b_fits": True,
        "swap_metrics": {"total_swaps": 0, "avg_swap_time_s": 0.0, "oom_recoveries": 0},
    }
    s.on_state_change = MagicMock()
    return s


@pytest.fixture
def app(fake_queue, fake_scheduler, monkeypatch):
    # Ensure the lazy `cortex.pipeline` import doesn't blow up if any handler
    # accidentally drags it in during a test.
    if "cortex.pipeline" not in sys.modules:
        sys.modules["cortex.pipeline"] = MagicMock()
    if "torch" not in sys.modules:
        sys.modules["torch"] = MagicMock()

    from webapp.server import create_app
    return create_app(queue=fake_queue, scheduler=fake_scheduler)


@pytest.fixture
def client(app):
    with TestClient(app) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_returns_ok(self, client):
        resp = client.get("/api/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["version"] == "0.1.0"
        assert "gpu" in body
        assert "queue" in body
        assert body["websocket_clients"] == 0

    def test_health_includes_vram_report(self, client):
        resp = client.get("/api/health")
        gpu = resp.json()["gpu"]
        assert gpu["state"] == "idle"
        assert gpu["tribe_fits"] is True


# ---------------------------------------------------------------------------
# Scan submission
# ---------------------------------------------------------------------------

class TestSubmitScan:
    def test_rejects_unsupported_extension(self, client):
        files = {"file": ("malicious.exe", io.BytesIO(b"\x00\x00"), "application/octet-stream")}
        resp = client.post("/api/scan", files=files)
        assert resp.status_code == 400
        body = resp.json()
        assert body["ok"] is False
        assert body["error_code"] == "invalid_file_type"
        assert body["error_class"] == "input"

    def test_accepts_mp4_upload(self, client, tmp_path):
        payload = b"\x00" * (1024 * 16)  # 16KB dummy
        files = {"file": ("clip.mp4", io.BytesIO(payload), "video/mp4")}
        data = {"tier": "1", "source": "webui"}
        resp = client.post("/api/scan", files=files, data=data)
        assert resp.status_code == 202
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "queued"
        assert "scan_id" in body
        assert len(body["scan_id"]) == 12  # uuid4().hex[:12]

    def test_rejects_oversized_upload(self, client):
        # 51MB exceeds the 50MB cap
        big = b"\x00" * (51 * 1024 * 1024)
        files = {"file": ("big.mp4", io.BytesIO(big), "video/mp4")}
        resp = client.post("/api/scan", files=files)
        assert resp.status_code == 413
        body = resp.json()
        assert body["error_code"] == "file_too_large"

    def test_accepts_audio_upload(self, client):
        files = {"file": ("voice.wav", io.BytesIO(b"\x00" * 1024), "audio/wav")}
        resp = client.post("/api/scan", files=files)
        assert resp.status_code == 202

    def test_tier_clamped_to_valid_range(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
        # Tier 99 is out of range; FastAPI returns 422 (validation error)
        resp = client.post("/api/scan", files=files, data={"tier": "99"})
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Scan lookup
# ---------------------------------------------------------------------------

class TestGetScan:
    def test_unknown_id_returns_404(self, client):
        resp = client.get("/api/scan/nonexistent")
        assert resp.status_code == 404

    def test_record_appears_after_submission(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 2048), "video/mp4")}
        submit_resp = client.post("/api/scan", files=files)
        scan_id = submit_resp.json()["scan_id"]

        lookup = client.get(f"/api/scan/{scan_id}")
        assert lookup.status_code == 200
        record = lookup.json()
        assert record["id"] == scan_id
        assert record["filename"] == "clip.mp4"
        assert record["tier"] == 1
        assert record["source"] == "webui"


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

class TestWebSocket:
    def test_initial_hello_message(self, client):
        with client.websocket_connect("/api/ws") as ws:
            msg = ws.receive_json()
            assert msg["type"] == "hello"
            assert msg["scheduler_state"] == "idle"
            assert "queue" in msg

    def test_websocket_broadcasts_scan_queued(self, client):
        with client.websocket_connect("/api/ws") as ws:
            # Drain the hello
            ws.receive_json()

            # Submit a scan; broadcasts should arrive
            files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
            client.post("/api/scan", files=files)

            # Receive the queued event
            msg = ws.receive_json()
            assert msg["type"] == "scan_queued"
            assert "scan_id" in msg
            assert msg["filename"] == "clip.mp4"


# ---------------------------------------------------------------------------
# Static
# ---------------------------------------------------------------------------

class TestAtlas:
    def test_atlas_endpoint_returns_full_atlas(self, client):
        resp = client.get("/api/atlas")
        assert resp.status_code == 200
        body = resp.json()
        assert body["schema_version"] == 1
        assert body["n_networks"] == 7
        assert "regions" in body and len(body["regions"]) >= 50
        assert "networks" in body
        # Every network entry must carry a label and color
        for net in body["networks"].values():
            assert "label" in net
            assert net["color"].startswith("#")

    def test_every_atlas_region_has_required_fields(self, client):
        atlas = client.get("/api/atlas").json()
        net_keys = set(atlas["networks"].keys())
        for region in atlas["regions"]:
            assert {"id", "name", "network", "hemi", "xyz"} <= region.keys()
            assert region["network"] in net_keys, f"unknown network on {region['id']}"
            assert len(region["xyz"]) == 3
            for v in region["xyz"]:
                assert -1.5 <= v <= 1.5  # normalized coords

    def test_atlas_404_when_file_missing(self, client, monkeypatch, tmp_path):
        from webapp import server as srv
        monkeypatch.setattr(srv, "PUBLIC_DIR", tmp_path)
        resp = client.get("/api/atlas")
        assert resp.status_code == 404


class TestSimulatedBOLD:
    def test_simulated_bold_shape(self, client):
        resp = client.get("/api/scan/abc123/bold-simulate?n_t=50")
        assert resp.status_code == 200
        body = resp.json()
        assert body["scan_id"] == "abc123"
        assert body["n_t"] == 50
        assert body["simulated"] is True
        assert body["tr_seconds"] == 0.5
        # bold is (n_t, n_regions)
        assert len(body["bold"]) == body["n_t"]
        assert all(len(row) == body["n_regions"] for row in body["bold"])
        assert len(body["region_ids"]) == body["n_regions"]

    def test_simulated_bold_is_deterministic_per_scan_id(self, client):
        a = client.get("/api/scan/same_id/bold-simulate?n_t=20").json()
        b = client.get("/api/scan/same_id/bold-simulate?n_t=20").json()
        assert a["bold"] == b["bold"]

    def test_simulated_bold_differs_across_scan_ids(self, client):
        a = client.get("/api/scan/scan_aaa/bold-simulate?n_t=20").json()
        b = client.get("/api/scan/scan_bbb/bold-simulate?n_t=20").json()
        assert a["bold"] != b["bold"]

    def test_simulated_bold_clamps_n_t(self, client):
        # Caller asks for 9999 → clamped to 512
        resp = client.get("/api/scan/x/bold-simulate?n_t=9999").json()
        assert resp["n_t"] == 512
        # Caller asks for 1 → clamped to 8
        resp = client.get("/api/scan/x/bold-simulate?n_t=1").json()
        assert resp["n_t"] == 8


class TestStatic:
    def test_root_returns_404_when_viewer_not_built(self, client, monkeypatch, tmp_path):
        # If the viewer build is genuinely missing, GET / should 404 with a clear message.
        from webapp import server as srv

        monkeypatch.setattr(srv, "PUBLIC_DIR", tmp_path)
        resp = client.get("/")
        assert resp.status_code == 404

    def test_root_serves_index_html_when_viewer_built(self, client):
        # The bundled placeholder lives at webapp/public/index.html.
        resp = client.get("/")
        assert resp.status_code == 200
        body = resp.text
        # Lock identifying markers to catch accidental rebrand regressions.
        assert "Cortex" in body
        assert "Gemma is a trademark of Google LLC" in body
