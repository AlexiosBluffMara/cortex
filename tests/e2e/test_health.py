"""Smoke-level e2e test: hit the running server."""
import httpx
import pytest

BASE = "http://127.0.0.1:8765"


@pytest.mark.e2e
def test_health():
    r = httpx.get(f"{BASE}/api/health", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["ok"] is True


@pytest.mark.e2e
def test_info():
    r = httpx.get(f"{BASE}/api/info", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert data["tribe_v2"]["tr_seconds"] == 0.5
    assert data["tribe_v2"]["n_vertices"] == 20484


@pytest.mark.e2e
def test_atlas():
    r = httpx.get(f"{BASE}/api/atlas", timeout=5)
    assert r.status_code == 200
    data = r.json()
    assert "regions" in data
