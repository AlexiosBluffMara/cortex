"""Tests for the 3-tier inference router (Ollama -> Workers AI -> HF).

All HTTP is mocked via httpx.MockTransport. We monkeypatch httpx.AsyncClient
inside the router module so its per-call client construction picks up our
mock transport.
"""
from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from inference_router import queue as queue_mod
from inference_router import router as router_mod
from inference_router.config import RouterConfig
from inference_router.router import (
    AllProvidersFailedError,
    GenerationResult,
    generate,
    generate_stream,
    health,
)
from inference_router.server import create_app

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cfg() -> RouterConfig:
    return RouterConfig(
        cloudflare_api_token="cf-test-token",
        cloudflare_account_id="cf-test-account",
        hf_token="hf-test-token",
        ollama_url="http://localhost:11434",
        ollama_model_preference="gemma3:12b",
    )


class _Calls:
    """Tracks which provider endpoints were hit during a test."""

    def __init__(self) -> None:
        self.ollama_health = 0
        self.ollama_generate = 0
        self.cf_generate = 0
        self.hf_generate = 0
        self.cf_health = 0
        self.hf_health = 0


def _build_handler(
    calls: _Calls,
    *,
    ollama_health_status: int = 200,
    ollama_generate_status: int = 200,
    ollama_response_text: str = "ollama-response",
    cf_status: int = 200,
    cf_response_text: str = "cf-response",
    hf_status: int = 200,
    hf_response_text: str = "hf-response",
    cf_health_status: int = 200,
    hf_health_status: int = 200,
    ollama_generate_delay: float = 0.0,
    on_ollama_generate: Callable[[], None] | None = None,
) -> Callable[[httpx.Request], httpx.Response]:
    async def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/api/tags"):
            calls.ollama_health += 1
            if ollama_health_status >= 400:
                return httpx.Response(ollama_health_status, text="down")
            return httpx.Response(200, json={"models": []})
        if url.endswith("/api/generate"):
            calls.ollama_generate += 1
            if on_ollama_generate is not None:
                on_ollama_generate()
            if ollama_generate_delay:
                await asyncio.sleep(ollama_generate_delay)
            if ollama_generate_status >= 400:
                return httpx.Response(ollama_generate_status, text="ollama err")
            try:
                body = json.loads(request.content.decode("utf-8"))
            except ValueError:
                body = {}
            if body.get("stream"):
                lines = [
                    json.dumps({"response": ollama_response_text, "done": False}),
                    json.dumps({"response": "", "done": True}),
                ]
                return httpx.Response(
                    200,
                    content=("\n".join(lines) + "\n").encode("utf-8"),
                    headers={"content-type": "application/x-ndjson"},
                )
            return httpx.Response(200, json={"response": ollama_response_text, "done": True})
        if "api.cloudflare.com" in url and "/ai/run/" in url:
            calls.cf_generate += 1
            if cf_status >= 400:
                return httpx.Response(cf_status, text="cf err")
            return httpx.Response(
                200,
                json={"success": True, "result": {"response": cf_response_text}},
            )
        if "api.cloudflare.com" in url and "/ai/models/search" in url:
            calls.cf_health += 1
            return httpx.Response(cf_health_status, json={"success": True})
        if "api-inference.huggingface.co" in url:
            calls.hf_generate += 1
            if hf_status >= 400:
                return httpx.Response(hf_status, text="hf err")
            return httpx.Response(200, json=[{"generated_text": hf_response_text}])
        if "huggingface.co/api/whoami" in url:
            calls.hf_health += 1
            return httpx.Response(hf_health_status, json={"name": "test"})
        return httpx.Response(404, text=f"unhandled url: {url}")

    return handler


def _patch_httpx(monkeypatch: pytest.MonkeyPatch, handler: Callable[[httpx.Request], httpx.Response]) -> None:
    transport = httpx.MockTransport(handler)
    real_async_client = httpx.AsyncClient

    def _factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        kwargs["transport"] = transport
        return real_async_client(*args, **kwargs)

    # Patch the router's private alias only — not the global httpx.AsyncClient,
    # so that the FastAPI test client (also using httpx) is unaffected.
    monkeypatch.setattr(router_mod, "_AsyncClient", _factory)


@pytest.fixture(autouse=True)
def _reset_serializer() -> None:
    queue_mod.reset_serializer()
    yield
    queue_mod.reset_serializer()


# ---------------------------------------------------------------------------
# Provider-routing tests
# ---------------------------------------------------------------------------

class TestProviderOrdering:
    async def test_ollama_healthy_serves_request(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls, ollama_response_text="hi from 5090"))

        result = await generate("hello", model="gemma3:12b", cfg=_cfg())

        assert isinstance(result, GenerationResult)
        assert result.provider == "ollama"
        assert result.text == "hi from 5090"
        assert calls.ollama_health == 1
        assert calls.ollama_generate == 1
        assert calls.cf_generate == 0
        assert calls.hf_generate == 0

    async def test_ollama_503_falls_through_to_workers_ai(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        # health returns 503 -> not healthy -> we skip ollama entirely
        _patch_httpx(
            monkeypatch,
            _build_handler(
                calls,
                ollama_health_status=503,
                cf_response_text="cf-says-hi",
            ),
        )

        result = await generate("hello", cfg=_cfg())

        assert result.provider == "workers-ai"
        assert result.text == "cf-says-hi"
        assert calls.ollama_health == 1
        assert calls.ollama_generate == 0
        assert calls.cf_generate == 1
        assert calls.hf_generate == 0

    async def test_ollama_generate_fails_falls_through(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        # Health says OK (200) but the actual generate call returns 500.
        _patch_httpx(
            monkeypatch,
            _build_handler(calls, ollama_generate_status=500, cf_response_text="cf-rescue"),
        )

        result = await generate("hello", cfg=_cfg())

        assert result.provider == "workers-ai"
        assert result.text == "cf-rescue"
        assert calls.ollama_health == 1
        assert calls.ollama_generate == 1
        assert calls.cf_generate == 1
        assert calls.hf_generate == 0

    async def test_ollama_down_workers_429_then_hf_succeeds(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(
                calls,
                ollama_health_status=503,
                cf_status=429,
                hf_response_text="hf-saves-the-day",
            ),
        )

        result = await generate("hello", cfg=_cfg())

        assert result.provider == "hf"
        assert result.text == "hf-saves-the-day"
        assert calls.ollama_health == 1
        assert calls.ollama_generate == 0
        assert calls.cf_generate == 1
        assert calls.hf_generate == 1

    async def test_all_three_fail_raises_combined_exception(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(
                calls,
                ollama_health_status=503,
                cf_status=500,
                hf_status=500,
            ),
        )

        with pytest.raises(AllProvidersFailedError) as excinfo:
            await generate("hello", cfg=_cfg())

        err = excinfo.value
        names = {n for n, _ in err.failures}
        assert names == {"ollama", "workers-ai", "hf"}
        msg = str(err)
        assert "ollama" in msg and "workers-ai" in msg and "hf" in msg
        assert calls.cf_generate == 1
        assert calls.hf_generate == 1


# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    async def test_all_providers_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls))

        out = await health(cfg=_cfg())

        assert out == {"ollama": True, "workers_ai": True, "hf": True}

    async def test_ollama_down_others_up(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls, ollama_health_status=503))

        out = await health(cfg=_cfg())

        assert out["ollama"] is False
        assert out["workers_ai"] is True
        assert out["hf"] is True

    async def test_missing_credentials_means_not_healthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls))
        cfg = RouterConfig(
            cloudflare_api_token=None,
            cloudflare_account_id=None,
            hf_token=None,
            ollama_url="http://localhost:11434",
            ollama_model_preference="gemma3:12b",
        )

        out = await health(cfg=cfg)

        assert out["ollama"] is True
        assert out["workers_ai"] is False
        assert out["hf"] is False

    async def test_hf_health_403_marks_unhealthy(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls, hf_health_status=403))

        out = await health(cfg=_cfg())

        assert out["hf"] is False


# ---------------------------------------------------------------------------
# Queue serialization
# ---------------------------------------------------------------------------

class TestQueueSerialization:
    async def test_concurrent_ollama_calls_serialize(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Two concurrent generate() calls must NOT overlap inside the Ollama generate path.

        We use an asyncio.Event + counter to detect overlap: the handler
        increments a "currently-inside" counter on entry, asserts it's never
        >1, sleeps briefly, decrements on exit. If the queue weren't
        serializing, both tasks would be inside at the same time.
        """
        calls = _Calls()
        active = {"count": 0, "max_seen": 0}

        def _on_enter() -> None:
            active["count"] += 1
            active["max_seen"] = max(active["max_seen"], active["count"])

        # Wrap the handler to also decrement after we return. We do this by
        # using a custom handler that awaits before decrementing.
        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/tags"):
                calls.ollama_health += 1
                return httpx.Response(200, json={"models": []})
            if url.endswith("/api/generate"):
                calls.ollama_generate += 1
                _on_enter()
                try:
                    await asyncio.sleep(0.05)
                    return httpx.Response(200, json={"response": "ok", "done": True})
                finally:
                    active["count"] -= 1
            return httpx.Response(404, text="unhandled")

        _patch_httpx(monkeypatch, handler)

        cfg = _cfg()
        results = await asyncio.gather(
            generate("a", cfg=cfg),
            generate("b", cfg=cfg),
            generate("c", cfg=cfg),
        )

        assert all(r.provider == "ollama" for r in results)
        assert calls.ollama_generate == 3
        # The whole point of the queue:
        assert active["max_seen"] == 1, (
            f"expected serialization (max 1 inflight), saw {active['max_seen']}"
        )

    async def test_serializer_inflight_counter(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Sanity: the serializer exposes an inflight counter and decrements properly."""
        from inference_router.queue import get_serializer

        ser = get_serializer()
        assert ser.inflight == 0

        async def _job() -> str:
            assert ser.inflight == 1
            return "x"

        out = await ser.run(_job)
        assert out == "x"
        assert ser.inflight == 0


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

class TestStreaming:
    async def test_stream_from_ollama(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls, ollama_response_text="streamed"))

        chunks: list[tuple[str, str]] = []
        async for prov, piece in generate_stream("hi", cfg=_cfg()):
            chunks.append((prov, piece))

        assert chunks
        assert all(p == "ollama" for p, _ in chunks)
        assert "".join(c for _, c in chunks) == "streamed"

    async def test_stream_falls_back_to_cloudflare(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(calls, ollama_health_status=503, cf_response_text="full-cf-text"),
        )

        chunks: list[tuple[str, str]] = []
        async for prov, piece in generate_stream("hi", cfg=_cfg()):
            chunks.append((prov, piece))

        assert chunks == [("workers-ai", "full-cf-text")]


# ---------------------------------------------------------------------------
# Server endpoints
# ---------------------------------------------------------------------------

class TestServer:
    async def test_generate_endpoint_returns_text_and_provider(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls, ollama_response_text="server-says-hi"))
        # Make load_config return our test config so the server doesn't try
        # to read real credential files.
        monkeypatch.setattr(router_mod, "load_config", _cfg)

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post("/v1/generate", json={"prompt": "hi", "model": "gemma3:12b"})
        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "server-says-hi"
        assert body["provider"] == "ollama"
        assert isinstance(body["elapsed_ms"], int)

    async def test_healthz_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls = _Calls()
        _patch_httpx(monkeypatch, _build_handler(calls))
        monkeypatch.setattr(router_mod, "load_config", _cfg)

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.get("/healthz")
        assert r.status_code == 200
        assert r.json() == {"ollama": True, "workers_ai": True, "hf": True}

    async def test_generate_endpoint_502_when_all_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(
                calls, ollama_health_status=503, cf_status=500, hf_status=500
            ),
        )
        monkeypatch.setattr(router_mod, "load_config", _cfg)

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post("/v1/generate", json={"prompt": "hi"})
        assert r.status_code == 502


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

class TestConfig:
    def test_load_config_from_env(self, monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
        from inference_router.config import load_config

        monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "cf-tok")
        monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "cf-acc")
        monkeypatch.setenv("HF_TOKEN", "hf-tok")
        monkeypatch.setenv("OLLAMA_URL", "http://example:9999/")
        monkeypatch.setenv("OLLAMA_MODEL_PREFERENCE", "gemma3:27b")
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        cfg = load_config()

        assert cfg.cloudflare_api_token == "cf-tok"
        assert cfg.cloudflare_account_id == "cf-acc"
        assert cfg.hf_token == "hf-tok"
        assert cfg.ollama_url == "http://example:9999"
        assert cfg.ollama_model_preference == "gemma3:27b"
        assert cfg.has_cloudflare is True
        assert cfg.has_hf is True

    def test_load_config_falls_back_to_files(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path
    ) -> None:
        from inference_router.config import load_config

        monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
        monkeypatch.delenv("CLOUDFLARE_ACCOUNT_ID", raising=False)
        monkeypatch.delenv("HF_TOKEN", raising=False)
        monkeypatch.delenv("HUGGINGFACE_TOKEN", raising=False)
        monkeypatch.delenv("OLLAMA_URL", raising=False)
        monkeypatch.delenv("OLLAMA_MODEL_PREFERENCE", raising=False)
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))

        (tmp_path / ".cloudflare").mkdir()
        (tmp_path / ".cloudflare" / "credentials").write_text(
            "CLOUDFLARE_API_TOKEN=file-cf\nCLOUDFLARE_ACCOUNT_ID=file-acc\n"
        )
        (tmp_path / ".huggingface").mkdir()
        (tmp_path / ".huggingface" / "token").write_text("file-hf\n")

        cfg = load_config()

        assert cfg.cloudflare_api_token == "file-cf"
        assert cfg.cloudflare_account_id == "file-acc"
        assert cfg.hf_token == "file-hf"
        assert cfg.ollama_url == "http://localhost:11434"
        assert cfg.ollama_model_preference == "gemma3:12b"


# ---------------------------------------------------------------------------
# Model name mapping
# ---------------------------------------------------------------------------

class TestModelMapping:
    def test_cf_model_mapping(self) -> None:
        from inference_router.router import _cf_model

        assert _cf_model("gemma3:12b") == "@cf/google/gemma-3-12b-it"
        assert _cf_model("gemma3:27b") == "@cf/google/gemma-3-27b-it"
        # Unknown models fall back to the Llama default.
        assert _cf_model("totally-unknown") == "@cf/meta/llama-3.1-8b-instruct"

    def test_hf_model_mapping(self) -> None:
        from inference_router.router import _hf_model

        assert _hf_model("gemma3:12b") == "google/gemma-3-12b-it"
        assert _hf_model("gemma3:e4b") == "google/gemma-3-4b-it"
        assert _hf_model("totally-unknown") == "google/gemma-3-12b-it"


# ---------------------------------------------------------------------------
# Provider-specific edge cases
# ---------------------------------------------------------------------------

class TestProviderEdgeCases:
    async def test_cloudflare_skipped_without_credentials(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """If CF credentials are missing we should fall through to HF immediately."""
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(calls, ollama_health_status=503, hf_response_text="hf-only"),
        )
        cfg = RouterConfig(
            cloudflare_api_token=None,
            cloudflare_account_id=None,
            hf_token="hf-test-token",
            ollama_url="http://localhost:11434",
            ollama_model_preference="gemma3:12b",
        )

        result = await generate("hi", cfg=cfg)

        assert result.provider == "hf"
        assert result.text == "hf-only"
        assert calls.cf_generate == 0

    async def test_hf_skipped_without_credentials_raises(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(calls, ollama_health_status=503, cf_status=500),
        )
        cfg = RouterConfig(
            cloudflare_api_token="cf-tok",
            cloudflare_account_id="cf-acc",
            hf_token=None,
            ollama_url="http://localhost:11434",
            ollama_model_preference="gemma3:12b",
        )

        with pytest.raises(AllProvidersFailedError):
            await generate("hi", cfg=cfg)

    async def test_hf_response_dict_shape(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """HF sometimes returns {'generated_text': ...} directly (not wrapped in a list)."""
        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/tags"):
                return httpx.Response(503, text="down")
            if "api.cloudflare.com" in url:
                return httpx.Response(500, text="cf err")
            if "api-inference.huggingface.co" in url:
                return httpx.Response(200, json={"generated_text": "dict-shape"})
            return httpx.Response(404)

        _patch_httpx(monkeypatch, handler)
        result = await generate("hi", cfg=_cfg())
        assert result.provider == "hf"
        assert result.text == "dict-shape"

    async def test_hf_response_error_field_falls_through(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/tags"):
                return httpx.Response(503)
            if "api.cloudflare.com" in url:
                return httpx.Response(500)
            if "api-inference.huggingface.co" in url:
                return httpx.Response(200, json={"error": "model loading"})
            return httpx.Response(404)

        _patch_httpx(monkeypatch, handler)
        with pytest.raises(AllProvidersFailedError) as excinfo:
            await generate("hi", cfg=_cfg())
        assert any("hf" == n for n, _ in excinfo.value.failures)

    async def test_workers_ai_success_false_response(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/tags"):
                return httpx.Response(503)
            if "api.cloudflare.com" in url and "/ai/run/" in url:
                return httpx.Response(
                    200,
                    json={"success": False, "errors": [{"message": "nope"}]},
                )
            if "api-inference.huggingface.co" in url:
                return httpx.Response(200, json=[{"generated_text": "hf-rescue"}])
            return httpx.Response(404)

        _patch_httpx(monkeypatch, handler)
        result = await generate("hi", cfg=_cfg())
        assert result.provider == "hf"
        assert result.text == "hf-rescue"

    async def test_kwargs_passed_to_cloudflare(self, monkeypatch: pytest.MonkeyPatch) -> None:
        captured: dict[str, Any] = {}

        async def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            if url.endswith("/api/tags"):
                return httpx.Response(503)
            if "/ai/run/" in url:
                captured["body"] = json.loads(request.content.decode())
                captured["auth"] = request.headers.get("authorization")
                return httpx.Response(
                    200, json={"success": True, "result": {"response": "ok"}}
                )
            return httpx.Response(404)

        _patch_httpx(monkeypatch, handler)
        result = await generate("hi", cfg=_cfg(), max_tokens=42, temperature=0.7)

        assert result.provider == "workers-ai"
        assert captured["body"]["max_tokens"] == 42
        assert captured["body"]["temperature"] == 0.7
        assert captured["auth"] == "Bearer cf-test-token"

    async def test_ollama_health_connection_error_returns_false(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused")

        _patch_httpx(monkeypatch, handler)
        out = await health(cfg=_cfg())
        assert out == {"ollama": False, "workers_ai": False, "hf": False}


# ---------------------------------------------------------------------------
# Server: streaming + extra parameters
# ---------------------------------------------------------------------------

class TestServerExtras:
    async def test_generate_stream_endpoint(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(calls, ollama_response_text="streamed-via-server"),
        )
        monkeypatch.setattr(router_mod, "load_config", _cfg)

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/v1/generate",
                json={
                    "prompt": "hi",
                    "model": "gemma3:12b",
                    "stream": True,
                    "max_tokens": 32,
                    "temperature": 0.5,
                },
            )

        assert r.status_code == 200
        body = r.json()
        assert body["text"] == "streamed-via-server"
        assert body["provider"] == "ollama"

    async def test_generate_stream_endpoint_502_when_all_fail(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = _Calls()
        _patch_httpx(
            monkeypatch,
            _build_handler(
                calls,
                ollama_health_status=503,
                cf_status=500,
                hf_status=500,
            ),
        )
        monkeypatch.setattr(router_mod, "load_config", _cfg)

        app = create_app()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            r = await client.post(
                "/v1/generate", json={"prompt": "hi", "stream": True}
            )
        assert r.status_code == 502
