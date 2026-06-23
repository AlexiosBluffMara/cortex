"""Integration tests for the FastAPI webapp.

Mocks the RequestQueue + GPUScheduler so we can exercise the HTTP surface
without spinning up Ollama or TRIBE v2.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from cortex.gpu_scheduler import GPUState
from cortex.request_queue import RequestType

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_queue():
    q = MagicMock()
    q.submissions = []
    q.status.return_value = {
        "queue_depth": 0,
        "processing": False,
        "active_request": None,
        "gpu_state": "idle",
        "completed": 0,
        "failed": 0,
    }

    async def _submit(request_type, payload, priority, source):
        q.submissions.append(
            {
                "request_type": request_type,
                "payload": payload,
                "priority": priority,
                "source": source,
            }
        )
        if request_type == RequestType.BRAIN_SCAN:
            r = MagicMock()
            r.top_rois = ["ROI_1", "ROI_2"]
            r.peak_t = 42
            r.seconds_elapsed = 4.2
            r.preds = None
            return r
        return "narration text"

    q.submit = AsyncMock(side_effect=_submit)
    return q


@pytest.fixture
def fake_scheduler():
    s = MagicMock()
    s.state = GPUState.IDLE
    idle_report = {
        "state": "idle",
        "total_gb": 32.0,
        "used_gb": 2.0,
        "free_gb": 29.5,
        "tribe_fits": True,
        "gemma_e4b_fits": True,
        "swap_metrics": {"total_swaps": 0, "avg_swap_time_s": 0.0, "oom_recoveries": 0},
    }
    s.vram_report.return_value = idle_report

    async def _ensure_tribe():
        s.state = GPUState.TRIBE_ACTIVE
        s.vram_report.return_value = {
            **idle_report,
            "state": "tribe_active",
            "used_gb": 10.0,
            "free_gb": 21.5,
        }

    s.ensure_tribe = AsyncMock(side_effect=_ensure_tribe)
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

    from webapp import server as server_mod

    monkeypatch.setattr(server_mod, "_GCP_AVAILABLE", False)
    monkeypatch.setattr(server_mod, "_describe_media_for_prompt", AsyncMock(return_value="test media context"))
    monkeypatch.setattr(server_mod, "_narrate_with_model", AsyncMock(return_value="narration text"))
    monkeypatch.setattr(server_mod, "_fetch_openrouter_free_models", AsyncMock(return_value=[]))
    return server_mod.create_app(queue=fake_queue, scheduler=fake_scheduler)


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


class TestControlPlane:
    def test_narration_models_defaults_to_openrouter_gemma(self, client):
        resp = client.get("/api/narration-models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default_model"] == "openrouter:google/gemma-4-26b-a4b-it:free"
        assert body["catalog_source"] == "static_fallback"
        assert body["openrouter_free_limits"]["daily_with_10_credits"] == 1000
        model_ids = {m["id"] for m in body["models"]}
        assert "openrouter:google/gemma-4-26b-a4b-it:free" in model_ids
        assert "local:gemma4:e4b" in model_ids

    def test_openrouter_payload_builds_free_catalog(self):
        from webapp import server as server_mod

        payload = {
            "data": [
                {
                    "id": "paid/model",
                    "name": "Paid: Model",
                    "context_length": 4096,
                    "architecture": {"input_modalities": ["text"]},
                    "pricing": {"prompt": "0.000001", "completion": "0.000002"},
                },
                {
                    "id": "openrouter/free",
                    "name": "OpenRouter: Auto Free",
                    "context_length": 200000,
                    "architecture": {"input_modalities": ["text", "image"]},
                    "pricing": {"prompt": "0", "completion": "0"},
                },
                {
                    "id": "google/gemma-4-26b-a4b-it:free",
                    "name": "Google: Gemma 4 26B A4B  (free)",
                    "context_length": 262144,
                    "architecture": {"input_modalities": ["image", "text", "video"]},
                    "pricing": {"prompt": "0", "completion": "0"},
                },
            ]
        }
        models = server_mod._openrouter_free_models_from_payload(payload)
        assert [m["id"] for m in models[:2]] == [
            "openrouter:google/gemma-4-26b-a4b-it:free",
            "openrouter:openrouter/free",
        ]
        assert "openrouter:paid/model" not in {m["id"] for m in models}
        assert models[0]["prompt_price"] == 0.0

    def test_narration_models_uses_live_openrouter_catalog(self, client, monkeypatch):
        from webapp import server as server_mod

        live_models = [
            {
                "id": "openrouter:google/gemma-4-26b-a4b-it:free",
                "label": "Gemma 4 26B A4B",
                "provider": "OpenRouter",
                "group": "Free",
                "default": True,
                "modalities": ["image", "text", "video"],
                "context_length": 262144,
                "prompt_price": 0.0,
                "completion_price": 0.0,
                "notes": "test live model",
            },
            {
                "id": "openrouter:test/free-text:free",
                "label": "Test Free Text",
                "provider": "OpenRouter",
                "group": "Free",
                "modalities": ["text"],
                "context_length": 8192,
                "prompt_price": 0.0,
                "completion_price": 0.0,
                "notes": "test live model",
            },
        ]
        monkeypatch.setattr(server_mod, "_fetch_openrouter_free_models", AsyncMock(return_value=live_models))
        resp = client.get("/api/narration-models")
        assert resp.status_code == 200
        body = resp.json()
        assert body["catalog_source"] == "openrouter_live"
        assert body["catalog_count"] == 2
        model_ids = {m["id"] for m in body["models"]}
        assert "openrouter:test/free-text:free" in model_ids
        assert "local:gemma4:e4b" in model_ids

    def test_openrouter_status_is_sanitized(self, client, monkeypatch):
        from webapp import server as server_mod

        monkeypatch.setattr(
            server_mod,
            "_openrouter_key_status",
            AsyncMock(return_value={
                "ok": False,
                "status": "not_configured",
                "message": "missing",
                "key_source": {"source": "missing", "label": "not configured"},
                "action_required": "configure key",
            }),
        )
        resp = client.get("/api/openrouter/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_configured"
        assert body["key_source"]["label"] == "not configured"
        assert body["action_required"] == "configure key"
        assert "default_model" in body
        assert "api_key" not in body

    def test_load_openrouter_key_info_prefers_process_env(self, monkeypatch):
        from webapp import server as server_mod

        monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-v1-test-process")
        info = server_mod._load_openrouter_api_key_info()
        assert info["api_key"] == "sk-or-v1-test-process"
        assert info["source"] == "process_env"
        assert "environment variable" in info["source_label"]

    def test_load_openrouter_key_info_reads_custom_env_file(self, tmp_path, monkeypatch):
        from webapp import server as server_mod

        env_file = tmp_path / "openrouter.env"
        env_file.write_text("OPENROUTER_API_KEY='sk-or-v1-test-file'\n", encoding="utf-8")
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
        monkeypatch.setenv("CORTEX_OPENROUTER_ENV_PATH", str(env_file))
        info = server_mod._load_openrouter_api_key_info()
        assert info["api_key"] == "sk-or-v1-test-file"
        assert info["source"] == "env_file"
        assert info["source_label"] == "configured operator env file"

    def test_tribe_status_reports_warmable_when_idle(self, client):
        resp = client.get("/api/tribe/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["pc_online"] is True
        assert body["can_warm_tribe"] is True
        assert body["tribe_ready"] is False

    def test_warm_tribe_calls_scheduler(self, client, fake_scheduler):
        resp = client.post("/api/tribe/warm")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["status"] == "tribe_ready"
        fake_scheduler.ensure_tribe.assert_awaited_once()

    def test_text_metadata_context_preserves_stimulus_text(self, tmp_path):
        from webapp import server as server_mod

        stimulus = tmp_path / "stimulus.txt"
        stimulus.write_text("A video transcript about thunder and neon lights.", encoding="utf-8")
        context = server_mod._media_metadata_context(stimulus)
        assert "modality: text" in context
        assert "thunder and neon lights" in context
        assert "TRIBE v2 received through its text events path" in context

    def test_cloud_narration_keeps_gpu_on_tribe(self):
        from webapp import server as server_mod

        assert server_mod._narration_uses_cloud_model("openrouter:google/gemma-4-26b-a4b-it:free")
        assert server_mod._narration_uses_cloud_model("gemini:gemini-2.5-flash")
        assert not server_mod._narration_uses_cloud_model("local:gemma4:e4b")

    def test_paid_access_helpers_gate_spend_paths(self):
        from webapp import server as server_mod

        assert server_mod._spend_access_granted("boileruphammerdown")
        assert not server_mod._spend_access_granted("wrong")
        assert not server_mod._narration_model_requires_paid_access(
            "openrouter:google/gemma-4-26b-a4b-it:free"
        )
        assert server_mod._narration_model_requires_paid_access(
            "openrouter:google/gemma-4-26b-a4b-it"
        )
        assert server_mod._compute_target_requires_paid_access("cloud_hf")
        assert not server_mod._compute_target_requires_paid_access("local")

    def test_compute_options_report_cloud_configuration(self, client):
        resp = client.get("/api/compute-options")
        assert resp.status_code == 200
        body = resp.json()
        assert body["default"] == "local"
        assert body["local"]["available"] is True
        assert body["cloud"]["targets"][0]["requires_paid_access"] is True

    def test_cloud_error_narrations_get_useful_tribe_fallback(self):
        from webapp import server as server_mod

        fake_result = MagicMock()
        fake_result.top_rois = ["7Networks_LH_Vis_1", "7Networks_RH_SomMot_2"]
        fake_result.peak_t = 8
        fake_result.seconds_elapsed = 11.2
        fake_result.preds.shape = (12, 20484)
        narrations = {
            "student": "OpenRouter narration unavailable (401). User not found.",
            "patient": "OpenRouter narration unavailable (401). User not found.",
            "clinician": "OpenRouter narration unavailable (401). User not found.",
            "ml_scientist": "OpenRouter narration unavailable (401). User not found.",
        }
        replaced = server_mod._apply_cloud_narration_fallbacks(
            narrations,
            label="demo.mp4",
            media_context="Source media metadata:\n- modality: video\n- audio: present",
            result=fake_result,
            narration_model="openrouter:google/gemma-4-26b-a4b-it:free",
        )

        assert replaced["student"] != narrations["student"]
        assert "tribe" in replaced["student"]
        assert "OpenRouter rejected" in replaced["patient"]
        assert "fsaverage5" in replaced["clinician"]
        assert "20,484-vertex" in replaced["ml_scientist"]
        assert len(set(replaced.values())) == 4

    def test_cloud_error_fallback_does_not_touch_local_model_narration(self):
        from webapp import server as server_mod

        narrations = {"student": "OpenRouter narration unavailable (401). User not found."}
        assert server_mod._apply_cloud_narration_fallbacks(
            narrations,
            label="demo",
            media_context="Input modality: text",
            narration_model="local:gemma4:e4b",
        ) == narrations


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
        record = client.get(f"/api/scan/{body['scan_id']}").json()
        assert record["narration_model"] == "openrouter:google/gemma-4-26b-a4b-it:free"
        assert record["compute_target"] == "local"

    def test_rejects_paid_openrouter_without_funded_access(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
        data = {"narration_model": "openrouter:google/gemma-4-26b-a4b-it"}
        resp = client.post("/api/scan", files=files, data=data)
        assert resp.status_code == 403
        body = resp.json()
        assert body["error_code"] == "paid_access_required"
        assert "Paid OpenRouter" in body["message"]

    def test_allows_paid_openrouter_with_funded_access(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
        data = {
            "narration_model": "openrouter:google/gemma-4-26b-a4b-it",
            "paid_access_code": "boileruphammerdown",
        }
        resp = client.post("/api/scan", files=files, data=data)
        assert resp.status_code == 202
        body = resp.json()
        record = client.get(f"/api/scan/{body['scan_id']}").json()
        assert record["paid_access"] is True
        assert record["narration_model"] == "openrouter:google/gemma-4-26b-a4b-it"

    def test_rejects_cloud_gpu_without_funded_access(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
        resp = client.post("/api/scan", files=files, data={"compute_target": "cloud_hf"})
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "paid_access_required"

    def test_cloud_gpu_with_access_reports_not_configured(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
        resp = client.post(
            "/api/scan",
            files=files,
            data={"compute_target": "cloud_hf", "paid_access_code": "boileruphammerdown"},
        )
        assert resp.status_code == 503
        body = resp.json()
        assert body["error_code"] == "cloud_tribe_not_configured"
        assert body["compute_target"] == "cloud_hf"

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
        assert resp.json()["analysis_mode"] == "tribe_audio"

    def test_accepts_image_upload_as_tribe_text_bridge(self, client):
        files = {"file": ("photo.png", io.BytesIO(b"\x89PNG\r\n\x1a\n"), "image/png")}
        resp = client.post("/api/scan", files=files)
        assert resp.status_code == 202
        body = resp.json()
        assert body["analysis_mode"] == "tribe_text_bridge_image"

        record = client.get(f"/api/scan/{body['scan_id']}").json()
        assert record["analysis_mode"] == "tribe_text_bridge_image"

    def test_accepts_document_upload_as_tribe_text_bridge(self, client):
        files = {"file": ("notes.html", io.BytesIO(b"<p>Neon storm over a lake</p>"), "text/html")}
        resp = client.post("/api/scan", files=files)
        assert resp.status_code == 202
        body = resp.json()
        assert body["analysis_mode"] == "tribe_text_bridge_document"

        record = client.get(f"/api/scan/{body['scan_id']}").json()
        assert record["analysis_mode"] == "tribe_text_bridge_document"

    def test_tier_clamped_to_valid_range(self, client):
        files = {"file": ("clip.mp4", io.BytesIO(b"\x00" * 1024), "video/mp4")}
        # Tier 99 is out of range; FastAPI returns 422 (validation error)
        resp = client.post("/api/scan", files=files, data={"tier": "99"})
        assert resp.status_code == 422

    def test_text_scan_routes_to_tribe_text_mode(self, client):
        resp = client.post(
            "/api/text-scan",
            data={"text": "A bright red apple rotates beside a lake.", "source": "webui"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["analysis_mode"] == "tribe_text"

        record = client.get(f"/api/scan/{body['scan_id']}").json()
        assert record["analysis_mode"] == "tribe_text"
        assert record["narration_model"] == "openrouter:google/gemma-4-26b-a4b-it:free"
        assert record["filename"] == "<text stimulus>"
        assert "bright red apple" in record["text"]

    def test_text_scan_rejects_paid_model_without_access(self, client):
        resp = client.post(
            "/api/text-scan",
            data={
                "text": "A bright red apple rotates beside a lake.",
                "narration_model": "openrouter:google/gemma-4-26b-a4b-it",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "paid_access_required"

    def test_frontend_exposes_camera_voice_and_funded_controls(self):
        html = Path("webapp/public/index.html").read_text(encoding="utf-8")
        js = Path("webapp/public/main.js").read_text(encoding="utf-8")

        assert 'id="camera-open-btn"' in html
        assert 'id="voice-record-btn"' in html
        assert 'id="paid-access-code"' in html
        assert 'name="compute-target"' in html
        assert "navigator.mediaDevices.getUserMedia" in js
        assert "new MediaRecorder" in js
        assert 'fd.append("compute_target"' in js
        assert 'fd.append("paid_access_code"' in js


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
        assert "TRIBE v2" in body
        assert "OpenRouter" in body
        assert "Show the brain what someone sees, hears, or reads." in body
