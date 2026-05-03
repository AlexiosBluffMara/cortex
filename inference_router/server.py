"""FastAPI app — 3-tier inference router with Ollama-compat proxy endpoint.

Serves on a configurable port (default 8766 via env ROUTER_PORT).
The /api/generate endpoint is Ollama-wire-compatible so cortex/ollama_client.py
can point OLLAMA_URL at this router and get transparent multi-backend routing.
"""
from __future__ import annotations

import time
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from . import router as router_mod
from .config import load_config


class GenerateRequest(BaseModel):
    prompt: str = Field(..., min_length=1)
    model: str = "gemma4:e4b"
    stream: bool = False
    max_tokens: int | None = None
    temperature: float | None = None


class GenerateResponse(BaseModel):
    text: str
    provider: str
    elapsed_ms: int


# Ollama-wire-compat request — accepts the full Ollama payload shape.
class OllamaGenerateRequest(BaseModel):
    model: str = "gemma4:e4b"
    prompt: str = ""
    system: str = ""
    stream: bool = False
    think: bool = False
    keep_alive: Any = None
    options: dict[str, Any] | None = None
    images: list[str] | None = None
    format: str | None = None


def create_app() -> FastAPI:
    app = FastAPI(title="cortex-inference-router", version="0.1.0")

    @app.post("/v1/generate", response_model=GenerateResponse)
    async def generate(req: GenerateRequest) -> GenerateResponse:
        start = time.perf_counter()
        kwargs: dict[str, object] = {}
        if req.max_tokens is not None:
            kwargs["max_tokens"] = req.max_tokens
        if req.temperature is not None:
            kwargs["temperature"] = req.temperature
        if req.stream:
            chunks: list[str] = []
            provider = "ollama"
            try:
                async for prov, piece in router_mod.generate_stream(
                    req.prompt, req.model, **kwargs
                ):
                    provider = prov
                    chunks.append(piece)
            except router_mod.AllProvidersFailedError as exc:
                raise HTTPException(status_code=502, detail=str(exc)) from exc
            elapsed = int((time.perf_counter() - start) * 1000)
            return GenerateResponse(text="".join(chunks), provider=provider, elapsed_ms=elapsed)

        try:
            result = await router_mod.generate(req.prompt, req.model, **kwargs)
        except router_mod.AllProvidersFailedError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        elapsed = int((time.perf_counter() - start) * 1000)
        return GenerateResponse(text=result.text, provider=result.provider, elapsed_ms=elapsed)

    @app.post("/api/generate")
    async def ollama_compat_generate(req: OllamaGenerateRequest) -> JSONResponse:
        """Ollama-wire-compatible /api/generate proxy.

        Accepts the standard Ollama request body and returns a response that
        matches the Ollama non-streaming schema so cortex/ollama_client.py works
        without modification when OLLAMA_URL points here.

        Routing preference is read from:
          - request body's options.route_preference, if set, OR
          - ROUTE_PREFERENCE env var on the router process (default: cloud-first)
        """
        import os as _os
        full_prompt = f"{req.system}\n\n{req.prompt}".strip() if req.system else req.prompt
        kwargs: dict[str, object] = {}
        route_pref = _os.environ.get("ROUTE_PREFERENCE", "cloud-first")
        if req.options:
            if "temperature" in req.options:
                kwargs["temperature"] = req.options["temperature"]
            if "num_predict" in req.options:
                kwargs["max_tokens"] = req.options["num_predict"]
            if "route_preference" in req.options:
                route_pref = str(req.options["route_preference"])

        t0 = time.perf_counter()
        try:
            result = await router_mod.generate(
                full_prompt, req.model, route_preference=route_pref, **kwargs
            )
        except router_mod.AllProvidersFailedError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        elapsed_ns = int((time.perf_counter() - t0) * 1e9)
        return JSONResponse({
            "model": req.model,
            "response": result.text,
            "done": True,
            "provider": result.provider,
            "eval_count": len(result.text.split()),
            "eval_duration": elapsed_ns,
            "total_duration": elapsed_ns,
        })

    @app.get("/api/tags")
    async def ollama_compat_tags() -> JSONResponse:
        """Minimal Ollama /api/tags response so health checks pass."""
        cfg = load_config()
        return JSONResponse({"models": [{"name": cfg.ollama_model_preference}]})

    @app.get("/healthz")
    async def healthz() -> dict:
        return await router_mod.health()

    return app


app = create_app()


def main() -> None:
    import os
    import uvicorn

    port = int(os.environ.get("ROUTER_PORT", "8766"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    main()
