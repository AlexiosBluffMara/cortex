"""Contract coverage for the cloud TRIBE worker."""
from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.integration


def _configure_worker_tmp_dirs(monkeypatch, tmp_path):
    from cloud.tribe_worker import app as worker_mod

    upload_dir = tmp_path / "uploads"
    scan_dir = tmp_path / "scans"
    upload_dir.mkdir()
    scan_dir.mkdir()
    monkeypatch.setattr(worker_mod, "UPLOAD_DIR", upload_dir)
    monkeypatch.setattr(worker_mod, "SCAN_DIR", scan_dir)
    monkeypatch.setattr(worker_mod, "WORKER_MODE", "fake")
    monkeypatch.setenv("CORTEX_WORKER_FAKE_DELAY_S", "0")
    return worker_mod


def test_fake_worker_matches_cortex_proxy_contract(tmp_path, monkeypatch):
    worker_mod = _configure_worker_tmp_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(worker_mod, "WORKER_TOKEN", "")

    app = worker_mod.create_app(registry=worker_mod.Registry())
    with TestClient(app) as client:
        health = client.get("/healthz")
        assert health.status_code == 200
        assert health.json()["mode"] == "fake"

        resp = client.post(
            "/api/scan",
            data={"tier": "4", "source": "test", "narration_model": "openrouter/free"},
            files={"file": ("clip.mp4", b"fake video bytes", "video/mp4")},
        )
        assert resp.status_code == 202
        scan_id = resp.json()["scan_id"]

        body = {}
        for _ in range(30):
            detail = client.get(f"/api/scan/{scan_id}")
            assert detail.status_code == 200
            body = detail.json()
            if body["status"] == "complete":
                break
            time.sleep(0.05)

        assert body["status"] == "complete"
        assert body["analysis_mode"] == "tribe_video"
        assert body["has_bold_vertex"] is True
        assert body["top_rois"]

        bold = client.get(f"/api/scan/{scan_id}/bold-vertex?n_t=8")
        assert bold.status_code == 200
        assert bold.headers["X-N-T"] == "8"
        assert bold.headers["X-N-Vert"] == str(worker_mod.N_VERTICES)
        assert len(bold.content) == 8 * worker_mod.N_VERTICES * 4

        sim = client.get(f"/api/scan/{scan_id}/bold-simulate?n_t=8")
        assert sim.status_code == 200
        assert sim.json()["n_t"] == 8
        assert sim.json()["n_regions"] == 8

        media = client.get(f"/api/scan/{scan_id}/source-media")
        assert media.status_code == 200
        assert media.content == b"fake video bytes"


def test_worker_token_protects_scan_contract(tmp_path, monkeypatch):
    worker_mod = _configure_worker_tmp_dirs(monkeypatch, tmp_path)
    monkeypatch.setattr(worker_mod, "WORKER_TOKEN", "secret")

    app = worker_mod.create_app(registry=worker_mod.Registry())
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/api/scans").status_code == 401
        assert client.get("/api/scans", headers={"Authorization": "Bearer secret"}).status_code == 200

        rejected = client.post(
            "/api/scan",
            files={"file": ("clip.mp4", b"fake", "video/mp4")},
        )
        assert rejected.status_code == 401

        accepted = client.post(
            "/api/scan",
            headers={"Authorization": "Bearer secret"},
            files={"file": ("clip.mp4", b"fake", "video/mp4")},
        )
        assert accepted.status_code == 202
