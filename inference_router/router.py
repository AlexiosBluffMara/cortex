"""3-tier inference router: Ollama (RTX 5090) -> Cloudflare Workers AI -> HuggingFace.

Provider order is fixed and intentional:

  1. Local Ollama on the 5090. Free, fast on Blackwell, no per-token bill.
     If healthy, we wait as long as it takes — request itself has no timeout,
     only connection establishment does.
  2. Cloudflare Workers AI. Cheap, paid, rate-limited.
  3. HuggingFace Inference API. Last-resort, often slow / cold-start.

A single failure in one provider is logged and we fall through silently.
Only when all three fail do we raise (a single combined exception).

There are NO retries inside a single provider. Retries are how you turn a $200
Gemini bill into a $2K one — we don't do that here.
"""
from __future__ import annotations

import itertools
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from .config import RouterConfig, load_config
from .queue import serialize_ollama

# Module-level alias used for *all* outbound HTTP calls in this file. Tests
# monkeypatch this name to inject an httpx.MockTransport without affecting
# httpx.AsyncClient elsewhere (e.g. the FastAPI test client).
_AsyncClient = httpx.AsyncClient

log = logging.getLogger(__name__)

# Connection-establishment timeout. Keep tight: if Ollama isn't even listening
# we want to fall through quickly.
CONNECT_TIMEOUT_S = 5.0

# Round-robin counter across all Ollama backends.
_rr_counter = itertools.count()


def _pick_backends(cfg: RouterConfig, model: str | None = None) -> list[str]:
    """Return all backends in priority order for this model.

    Backend order is configuration-driven. We rotate through configured
    Ollama URLs to avoid pinning all local traffic to the same process, but we
    do not infer machine roles from hostnames or tailnet IPs.
    """
    backends = list(cfg.ollama_backends)
    if len(backends) <= 1:
        return backends

    start = next(_rr_counter) % len(backends)
    return backends[start:] + backends[:start]

# Bounded total timeout for the paid providers — we never want to sit on a
# rate-limited socket forever and rack up charges.
PAID_TOTAL_TIMEOUT_S = 300.0


# ---------------------------------------------------------------------------
# Model name mapping
# ---------------------------------------------------------------------------

# Internal canonical name -> Cloudflare Workers AI model id.
# Gemma 4 only. If Workers AI doesn't have a given Gemma 4 variant yet, the
# catalog lookup falls through to _CF_FALLBACK (Llama 3.1 8B as the last-resort
# Cloudflare path); the router's primary path is local Ollama / MLX anyway.
_CF_MODEL_MAP: dict[str, str] = {
    "gemma4:26b":     "@cf/google/gemma-4-26b-a4b-it",
    "gemma4:26b-a4b": "@cf/google/gemma-4-26b-a4b-it",
    "gemma4:31b":     "@cf/google/gemma-4-31b-it",
    "gemma4:e4b":     "@cf/google/gemma-4-e4b-it",
    "gemma4:e2b":     "@cf/google/gemma-4-e2b-it",
}
_CF_FALLBACK = "@cf/meta/llama-3.1-8b-instruct"


# Internal canonical name -> HuggingFace model repo id (Gemma 4 only).
_HF_MODEL_MAP: dict[str, str] = {
    "gemma4:26b":     "google/gemma-4-26b-a4b-it",
    "gemma4:26b-a4b": "google/gemma-4-26b-a4b-it",
    "gemma4:31b":     "google/gemma-4-31b-it",
    "gemma4:e4b":     "google/gemma-4-e4b-it",
    "gemma4:e2b":     "google/gemma-4-e2b-it",
}
_HF_FALLBACK = "google/gemma-4-e4b-it"


def _cf_model(name: str) -> str:
    return _CF_MODEL_MAP.get(name, _CF_FALLBACK)


def _hf_model(name: str) -> str:
    return _HF_MODEL_MAP.get(name, _HF_FALLBACK)


# ---------------------------------------------------------------------------
# Result + error types
# ---------------------------------------------------------------------------

@dataclass
class GenerationResult:
    text: str
    provider: str  # "ollama" | "workers-ai" | "hf"


class AllProvidersFailedError(RuntimeError):
    """Every provider in the chain returned an error."""

    def __init__(self, failures: list[tuple[str, str]]):
        self.failures = failures
        msg = "all inference providers failed: " + "; ".join(
            f"{name}: {err}" for name, err in failures
        )
        super().__init__(msg)


# ---------------------------------------------------------------------------
# Ollama provider
# ---------------------------------------------------------------------------

async def _ollama_healthy(cfg: RouterConfig) -> bool:
    """Return True if at least one backend is reachable."""
    for backend in cfg.ollama_backends:
        url = f"{backend}/api/tags"
        try:
            async with _AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
            ) as client:
                r = await client.get(url)
                if r.status_code == 200:
                    return True
        except (httpx.HTTPError, OSError) as exc:
            log.debug("ollama backend %s health check failed: %s", backend, exc)
    return False


async def _ollama_backends_health(cfg: RouterConfig) -> dict[str, bool]:
    """Return health status for every configured backend."""
    results: dict[str, bool] = {}
    for backend in cfg.ollama_backends:
        url = f"{backend}/api/tags"
        try:
            async with _AsyncClient(
                timeout=httpx.Timeout(CONNECT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
            ) as client:
                r = await client.get(url)
                results[backend] = r.status_code == 200
        except (httpx.HTTPError, OSError):
            results[backend] = False
    return results


async def _ollama_generate(prompt: str, model: str, cfg: RouterConfig, **kwargs: Any) -> str:
    """Try each backend in round-robin order; failover automatically on error."""
    backends = _pick_backends(cfg, model=model)
    last_exc: Exception | None = None
    for backend in backends:
        url = f"{backend}/api/generate"
        log.debug("routing ollama generate to %s (model=%s)", backend, model)
        body: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            "think": False,
        }
        if kwargs:
            body["options"] = {k: v for k, v in kwargs.items() if v is not None}
        timeout = httpx.Timeout(None, connect=CONNECT_TIMEOUT_S)
        try:
            async with _AsyncClient(timeout=timeout) as client:
                r = await client.post(url, json=body)
                if r.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        f"ollama {backend} returned {r.status_code}: {r.text[:200]}",
                        request=r.request,
                        response=r,
                    )
            data = r.json()
            text = data.get("response", "")
            if not isinstance(text, str):
                raise ValueError(f"ollama {backend} returned non-string response")
            return text
        except Exception as exc:
            log.warning("backend %s failed for model %s: %s", backend, model, exc)
            last_exc = exc
    raise last_exc or RuntimeError("no ollama backends configured")


async def _ollama_stream(prompt: str, model: str, cfg: RouterConfig, **kwargs: Any) -> AsyncIterator[str]:
    import json as _json

    # Pick the first healthy backend; streaming doesn't support mid-stream failover
    backends = _pick_backends(cfg, model=model)
    backend = backends[0]
    url = f"{backend}/api/generate"
    log.debug("routing ollama stream to %s (model=%s)", backend, model)
    body: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "stream": True,
        "think": False,
    }
    if kwargs:
        body["options"] = {k: v for k, v in kwargs.items() if v is not None}
    timeout = httpx.Timeout(None, connect=CONNECT_TIMEOUT_S)
    async with _AsyncClient(timeout=timeout) as client:
        async with client.stream("POST", url, json=body) as r:
            if r.status_code >= 400:
                txt = (await r.aread()).decode("utf-8", "replace")[:200]
                raise httpx.HTTPStatusError(
                    f"ollama stream returned {r.status_code}: {txt}",
                    request=r.request,
                    response=r,
                )
            async for line in r.aiter_lines():
                if not line:
                    continue
                try:
                    chunk = _json.loads(line)
                except ValueError:
                    continue
                piece = chunk.get("response")
                if piece:
                    yield piece
                if chunk.get("done"):
                    return


# ---------------------------------------------------------------------------
# Cloudflare Workers AI provider
# ---------------------------------------------------------------------------

async def _cloudflare_generate(prompt: str, model: str, cfg: RouterConfig, **kwargs: Any) -> str:
    if not cfg.has_cloudflare:
        raise RuntimeError("cloudflare credentials not configured")
    cf_model = _cf_model(model)
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{cfg.cloudflare_account_id}/ai/run/{cf_model}"
    )
    headers = {"Authorization": f"Bearer {cfg.cloudflare_api_token}"}
    body: dict[str, Any] = {"prompt": prompt}
    if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
        body["max_tokens"] = kwargs["max_tokens"]
    if "temperature" in kwargs and kwargs["temperature"] is not None:
        body["temperature"] = kwargs["temperature"]
    timeout = httpx.Timeout(PAID_TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    async with _AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"workers-ai returned {r.status_code}: {r.text[:200]}",
                request=r.request,
                response=r,
            )
        data = r.json()
        # Workers AI shape: {"result": {"response": "..."}, "success": true, ...}
        if data.get("success") is False:
            errors = data.get("errors") or []
            raise RuntimeError(f"workers-ai error: {errors}")
        result = data.get("result") or {}
        text = result.get("response")
        if text is None:
            text = result.get("output") or ""
        if not isinstance(text, str):
            text = str(text)
        return text


# ---------------------------------------------------------------------------
# HuggingFace Inference API provider
# ---------------------------------------------------------------------------

async def _hf_generate(prompt: str, model: str, cfg: RouterConfig, **kwargs: Any) -> str:
    if not cfg.has_hf:
        raise RuntimeError("huggingface token not configured")
    hf_model = _hf_model(model)
    url = f"https://api-inference.huggingface.co/models/{hf_model}"
    headers = {"Authorization": f"Bearer {cfg.hf_token}"}
    body: dict[str, Any] = {"inputs": prompt}
    parameters: dict[str, Any] = {}
    if "max_tokens" in kwargs and kwargs["max_tokens"] is not None:
        parameters["max_new_tokens"] = kwargs["max_tokens"]
    if "temperature" in kwargs and kwargs["temperature"] is not None:
        parameters["temperature"] = kwargs["temperature"]
    if parameters:
        body["parameters"] = parameters
    timeout = httpx.Timeout(PAID_TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    async with _AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"hf returned {r.status_code}: {r.text[:200]}",
                request=r.request,
                response=r,
            )
        data = r.json()
        # HF returns either [{"generated_text": "..."}] or {"generated_text": "..."}
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and "generated_text" in first:
                return str(first["generated_text"])
        if isinstance(data, dict):
            if "generated_text" in data:
                return str(data["generated_text"])
            if "error" in data:
                raise RuntimeError(f"hf error: {data['error']}")
        raise ValueError(f"unexpected hf response shape: {type(data).__name__}")


# ---------------------------------------------------------------------------
# OpenRouter provider (free tier: gemma-4-26b-a4b-it:free, gemma-4-31b-it:free)
# Rate limit: 20 req/min, 200 req/day. Used as Tier 1.5 cloud fallback.
# ---------------------------------------------------------------------------

# Canonical model name → OpenRouter model ID
# Cloud-first preference: 31b is the cloud default (free, no local VRAM cost,
# best quality available on the OpenRouter free tier). Smaller local models
# upgrade to 31b when the request reaches the cloud hop.
_OR_MODEL_MAP: dict[str, str] = {
    "gemma4:31b":     "google/gemma-4-31b-it:free",
    "gemma4:26b":     "google/gemma-4-31b-it:free",       # upgrade in cloud
    "gemma4:26b-a4b": "google/gemma-4-31b-it:free",       # upgrade in cloud
    "gemma4:e4b":     "google/gemma-4-31b-it:free",       # upgrade in cloud
    "gemma4:e2b":     "google/gemma-4-31b-it:free",       # upgrade in cloud
}
_OR_FALLBACK = "google/gemma-4-31b-it:free"


def _or_model(name: str) -> str:
    return _OR_MODEL_MAP.get(name, _OR_FALLBACK)


async def _openrouter_generate(prompt: str, model: str, cfg: RouterConfig, **kwargs: Any) -> str:
    if not cfg.openrouter_api_key:
        raise RuntimeError("openrouter_api_key not configured")
    # Try the preferred OpenRouter model, then fall back to the other free
    # model (31b ↔ 26b) if the upstream is rate-limited (429). e4b/e2b aren't
    # available on OpenRouter free, so we only juggle 31b and 26b.
    primary = _or_model(model)
    secondary = (
        "google/gemma-4-26b-a4b-it:free"
        if "31b" in primary
        else "google/gemma-4-31b-it:free"
    )
    candidates: list[str] = [primary]
    if secondary not in candidates:
        candidates.append(secondary)
    last_err: Exception | None = None
    for or_model in candidates:
        try:
            text = await _openrouter_call_one(prompt, or_model, cfg, **kwargs)
            log.info("openrouter served via %s", or_model)
            return text
        except _OpenRouterRateLimited as exc:
            log.info("openrouter %s rate-limited, trying next: %s", or_model, exc)
            last_err = exc
            continue
        except Exception as exc:
            # Non-rate-limit error — give up immediately so we fall through to Ollama
            raise
    if last_err:
        raise last_err
    raise RuntimeError("no openrouter candidates")


class _OpenRouterRateLimited(RuntimeError):
    pass


async def _openrouter_call_one(prompt: str, or_model: str, cfg: RouterConfig, **kwargs: Any) -> str:
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg.openrouter_api_key}",
        "HTTP-Referer": "https://redteamkitchen.com",
        "X-Title": "Cortex by Red Team Kitchen",
    }
    body: dict[str, Any] = {
        "model": or_model,
        "messages": [{"role": "user", "content": prompt}],
    }
    if "max_tokens" in kwargs and kwargs["max_tokens"]:
        body["max_tokens"] = kwargs["max_tokens"]
    if "temperature" in kwargs and kwargs["temperature"]:
        body["temperature"] = kwargs["temperature"]
    timeout = httpx.Timeout(PAID_TOTAL_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
    async with _AsyncClient(timeout=timeout) as client:
        r = await client.post(url, headers=headers, json=body)
        if r.status_code == 429:
            raise _OpenRouterRateLimited(
                f"openrouter 429 for {or_model}: {r.text[:160]}"
            )
        if r.status_code >= 400:
            # Some providers return 200 with an error body containing 429 inside
            raise httpx.HTTPStatusError(
                f"openrouter returned {r.status_code}: {r.text[:200]}",
                request=r.request,
                response=r,
            )
    data = r.json()
    # OpenRouter sometimes returns 200 with an error body when the upstream
    # provider rate-limits a free model. Detect that and raise so we can fall
    # through to the next candidate.
    if isinstance(data.get("error"), dict):
        err = data["error"]
        if err.get("code") == 429 or "rate" in str(err.get("message", "")).lower():
            raise _OpenRouterRateLimited(f"openrouter inline 429 for {or_model}: {err}")
    try:
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError) as exc:
        raise ValueError(f"unexpected openrouter response: {data}") from exc


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

async def generate(
    prompt: str,
    model: str = "gemma4:e4b",
    cfg: RouterConfig | None = None,
    *,
    route_preference: str = "auto",
    **kwargs: Any,
) -> GenerationResult:
    """Run a prompt through the tiered router; return text + provider used.

    `route_preference` selects the tier order:
      "cloud-first" (default for demo) — OpenRouter free → Ollama → paid clouds.
                    Best end-user latency, lowest local-GPU contention.
      "local-first" — configured Ollama backends → OpenRouter → paid clouds.
                    Best for sovereignty / sustained batch workloads.
      "openrouter-only" — OpenRouter only (skips local Ollama entirely).
      "ollama-only"     — Ollama only (skips cloud entirely).
      "auto" — alias for "cloud-first" right now.
    """
    cfg = cfg or load_config()
    failures: list[tuple[str, str]] = []
    pref = (route_preference or "auto").lower()
    if pref == "auto":
        pref = "cloud-first"

    # Build the tier sequence based on preference
    async def _try_openrouter():
        if not cfg.openrouter_api_key:
            failures.append(("openrouter", "no api key"))
            return None
        try:
            text = await _openrouter_generate(prompt, model, cfg, **kwargs)
            return GenerationResult(text=text, provider="openrouter")
        except Exception as exc:  # noqa: BLE001
            log.warning("openrouter provider failed: %s", exc)
            failures.append(("openrouter", str(exc)))
            return None

    async def _try_ollama():
        if not await _ollama_healthy(cfg):
            failures.append(("ollama", "not healthy"))
            return None
        try:
            async def _do() -> str:
                return await _ollama_generate(prompt, model, cfg, **kwargs)
            text = await serialize_ollama(_do)
            return GenerationResult(text=text, provider="ollama")
        except Exception as exc:  # noqa: BLE001
            log.warning("ollama provider failed: %s", exc)
            failures.append(("ollama", str(exc)))
            return None

    async def _try_workers_ai():
        try:
            text = await _cloudflare_generate(prompt, model, cfg, **kwargs)
            return GenerationResult(text=text, provider="workers-ai")
        except Exception as exc:  # noqa: BLE001
            log.warning("workers-ai provider failed: %s", exc)
            failures.append(("workers-ai", str(exc)))
            return None

    async def _try_hf():
        try:
            text = await _hf_generate(prompt, model, cfg, **kwargs)
            return GenerationResult(text=text, provider="hf")
        except Exception as exc:  # noqa: BLE001
            log.warning("hf provider failed: %s", exc)
            failures.append(("hf", str(exc)))
            return None

    if pref == "openrouter-only":
        chain = [_try_openrouter]
    elif pref == "ollama-only":
        chain = [_try_ollama]
    elif pref == "local-first":
        chain = [_try_ollama, _try_openrouter, _try_workers_ai, _try_hf]
    else:  # cloud-first / auto
        chain = [_try_openrouter, _try_ollama, _try_workers_ai, _try_hf]

    for fn in chain:
        result = await fn()
        if result is not None:
            return result

    raise AllProvidersFailedError(failures)


async def generate_stream(
    prompt: str,
    model: str = "gemma4:e4b",
    cfg: RouterConfig | None = None,
    **kwargs: Any,
) -> AsyncIterator[tuple[str, str]]:
    """Stream tokens through the router. Yields (provider, chunk) tuples.

    Streaming is only supported on Ollama. If Ollama is down, we fall back to
    the non-streaming providers and yield their full response as a single chunk.
    """
    cfg = cfg or load_config()
    failures: list[tuple[str, str]] = []

    if await _ollama_healthy(cfg):
        try:
            async def _do() -> list[str]:
                pieces: list[str] = []
                async for piece in _ollama_stream(prompt, model, cfg, **kwargs):
                    pieces.append(piece)
                return pieces

            # Note: queue serializes the *whole* stream as one Ollama job.
            pieces = await serialize_ollama(_do)
            for p in pieces:
                yield ("ollama", p)
            return
        except Exception as exc:  # noqa: BLE001
            log.warning("ollama stream failed: %s", exc)
            failures.append(("ollama", str(exc)))
    else:
        failures.append(("ollama", "not healthy"))

    try:
        text = await _cloudflare_generate(prompt, model, cfg, **kwargs)
        yield ("workers-ai", text)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("workers-ai stream-fallback failed: %s", exc)
        failures.append(("workers-ai", str(exc)))

    try:
        text = await _hf_generate(prompt, model, cfg, **kwargs)
        yield ("hf", text)
        return
    except Exception as exc:  # noqa: BLE001
        log.warning("hf stream-fallback failed: %s", exc)
        failures.append(("hf", str(exc)))

    raise AllProvidersFailedError(failures)


# ---------------------------------------------------------------------------
# Health-check helpers (used by /healthz)
# ---------------------------------------------------------------------------

async def _check_ollama(cfg: RouterConfig) -> bool:
    return await _ollama_healthy(cfg)


async def _check_cloudflare(cfg: RouterConfig) -> bool:
    if not cfg.has_cloudflare:
        return False
    url = (
        f"https://api.cloudflare.com/client/v4/accounts/"
        f"{cfg.cloudflare_account_id}/ai/models/search?per_page=1"
    )
    headers = {"Authorization": f"Bearer {cfg.cloudflare_api_token}"}
    try:
        async with _AsyncClient(
            timeout=httpx.Timeout(CONNECT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
        ) as client:
            r = await client.get(url, headers=headers)
            return r.status_code < 500 and r.status_code != 401 and r.status_code != 403
    except (httpx.HTTPError, OSError) as exc:
        log.debug("cloudflare health check failed: %s", exc)
        return False


async def _check_hf(cfg: RouterConfig) -> bool:
    if not cfg.has_hf:
        return False
    url = "https://huggingface.co/api/whoami-v2"
    headers = {"Authorization": f"Bearer {cfg.hf_token}"}
    try:
        async with _AsyncClient(
            timeout=httpx.Timeout(CONNECT_TIMEOUT_S, connect=CONNECT_TIMEOUT_S)
        ) as client:
            r = await client.get(url, headers=headers)
            return r.status_code == 200
    except (httpx.HTTPError, OSError) as exc:
        log.debug("hf health check failed: %s", exc)
        return False


async def health(cfg: RouterConfig | None = None) -> dict[str, bool | dict]:
    cfg = cfg or load_config()
    backend_health = await _ollama_backends_health(cfg)
    return {
        "ollama": any(backend_health.values()),
        "ollama_backends": backend_health,
        "openrouter": cfg.has_openrouter,
        "workers_ai": await _check_cloudflare(cfg),
        "hf": await _check_hf(cfg),
    }
