"""Cortex Ollama client — model-tier-aware LLM calls for Gemma 4.

Key behavior:
  - Always passes `think: false` — prevents Gemma 4 from spending token budget
    on internal chain-of-thought, which caused empty responses at low num_predict.
  - Supports image (vision) input — all Gemma 4 variants have working vision
    in Ollama 0.21.0+ with `think: false`.
  - Tracks tok/s per model for performance dashboards.
  - Chain-of-thought MODE: separate streaming endpoint for Discord thread output.

Model capability matrix (confirmed on Ollama 0.21.0, RTX 5090):
  gemma4:e2b          vision=YES  audio=NO  (audio requires llama.cpp)
  gemma4:e4b          vision=YES  audio=NO
  gemma4:e4b-it-q8_0  vision=YES  audio=NO
  gemma4:26b          vision=YES  audio=NO  (MoE — 26B total, ~4B active/token)
  gemma4:31b          vision=YES  audio=NO
"""
from __future__ import annotations

import json
import time
from collections.abc import Generator
from typing import Any

import requests

from . import config
from .logger import log

# -- Default options per model tier --------------------------------------------
# These are sensible defaults; callers override as needed.

_TIER_DEFAULTS: dict[str, dict] = {
    'fast': {   # E4B: snappy responses, no chain-of-thought
        'temperature': 0.4,
        'num_predict': 300,
        'num_ctx':     8192,
    },
    'deep': {   # 26B MoE: richer analysis, moderate tokens
        'temperature': 0.35,
        'num_predict': 600,
        'num_ctx':     16384,
    },
    'expert': { # 31B dense: maximum quality, full reasoning budget
        'temperature': 0.3,
        'num_predict': 900,
        'num_ctx':     32768,
    },
}


# -- Core generate -------------------------------------------------------------

def generate(
    prompt: str,
    system: str,
    *,
    model: str | None = None,
    images_b64: list[str] | None = None,
    json_mode: bool = False,
    temperature: float = 0.4,
    num_predict: int = 400,
    num_ctx: int = 8192,
    keep_alive: int | str = 0,
    timeout: int = 300,
    think: bool = False,
) -> str:
    """
    Call Ollama /api/generate and return the response text.

    Args:
        model:       Ollama model name (e.g. 'gemma4:e4b', 'gemma4:26b')
        images_b64:  List of base64-encoded JPEG/PNG images (enables vision)
        json_mode:   Request JSON-formatted output
        think:       Enable Gemma 4 chain-of-thought (default False — avoids
                     empty responses when num_predict budget is tight)
        keep_alive:  How long Ollama keeps the model in VRAM after this call.
                     '0s' = unload, '-1' = forever, '10m' = 10 minutes.
    """
    _model = model or config.OLLAMA_MODEL_QUALITY

    payload: dict[str, Any] = {
        'model':      _model,
        'system':     system,
        'prompt':     prompt,
        'stream':     False,
        'think':      think,
        'keep_alive': keep_alive,
        'options': {
            'temperature':  temperature,
            'num_predict':  num_predict,
            'num_ctx':      num_ctx,
            'num_gpu_layers': 999,      # All layers on GPU (Blackwell: 32 GB VRAM)
        },
    }

    if images_b64:
        payload['images'] = images_b64
    if json_mode:
        payload['format'] = 'json'

    t0 = time.time()
    try:
        r = requests.post(
            f'{config.OLLAMA_URL}/api/generate',
            json=payload,
            timeout=timeout,
        )
        r.raise_for_status()
        data     = r.json()
        response = (data.get('response') or '').strip()
        elapsed  = time.time() - t0

        # Telemetry
        eval_count    = data.get('eval_count', 0)
        eval_duration = data.get('eval_duration', 1) / 1e9  # ns -> s
        tok_per_s     = round(eval_count / max(eval_duration, 0.001), 1)
        log.debug('[ollama] %s -> %d tokens in %.1fs (%.1f tok/s)',
                  _model, eval_count, elapsed, tok_per_s)

        return response

    except requests.RequestException as exc:
        log.error('[ollama] generate failed for %s: %s', _model, exc)
        return f'(Ollama error: {exc})'


def generate_json(
    prompt: str,
    system: str,
    *,
    model: str | None = None,
    images_b64: list[str] | None = None,
    temperature: float = 0.2,
    num_predict: int = 300,
    num_ctx: int = 8192,
    timeout: int = 120,
) -> dict | None:
    """JSON-mode generate with automatic parsing and fallback strip."""
    raw = generate(
        prompt, system,
        model=model, images_b64=images_b64,
        json_mode=True,
        temperature=temperature,
        num_predict=num_predict,
        num_ctx=num_ctx,
        timeout=timeout,
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Strip markdown fences
        stripped = raw.strip().lstrip('`').strip()
        if stripped.startswith('json'):
            stripped = stripped[4:].strip()
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            log.warning('[ollama] JSON parse failed; raw: %.200s', raw)
            return None


# -- Streaming generate (for chain-of-thought) ---------------------------------

def generate_stream(
    prompt: str,
    system: str,
    *,
    model: str | None = None,
    images_b64: list[str] | None = None,
    temperature: float = 0.4,
    num_predict: int = 800,
    num_ctx: int = 16384,
    keep_alive: str = '0s',
    timeout: int = 300,
    think: bool = True,
) -> Generator[tuple[str, bool], None, None]:
    """
    Stream tokens from Ollama.  Yields (token_text, is_thinking) tuples.

    When think=True, Gemma 4 produces <think>...</think> blocks before the
    actual response. is_thinking=True signals the caller to display these in
    a collapsed/italicised section (e.g. a Discord thread spoiler block).

    Usage:
        for token, thinking in generate_stream(prompt, system):
            if thinking:
                # append to chain-of-thought section
            else:
                # append to main response
    """
    _model = model or config.OLLAMA_MODEL_QUALITY
    payload: dict[str, Any] = {
        'model':      _model,
        'system':     system,
        'prompt':     prompt,
        'stream':     True,
        'think':      think,
        'keep_alive': keep_alive,
        'options': {
            'temperature': temperature,
            'num_predict': num_predict,
            'num_ctx':     num_ctx,
            'num_gpu_layers': 999,
        },
    }
    if images_b64:
        payload['images'] = images_b64

    try:
        with requests.post(
            f'{config.OLLAMA_URL}/api/generate',
            json=payload,
            stream=True,
            timeout=timeout,
        ) as r:
            r.raise_for_status()
            in_thinking = False
            for line in r.iter_lines():
                if not line:
                    continue
                try:
                    data     = json.loads(line)
                    token    = data.get('response', '')
                    thinking = data.get('thinking', False)
                    yield token, thinking
                    if data.get('done'):
                        break
                except (json.JSONDecodeError, KeyError):
                    continue
    except requests.RequestException as exc:
        log.error('[ollama] stream failed: %s', exc)
        yield f'(Stream error: {exc})', False


# -- Tier-aware convenience wrappers -------------------------------------------

def generate_fast(
    prompt: str,
    system: str,
    *,
    model: str | None = None,
    images_b64: list[str] | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
    keep_alive: str = '60m',
    **kwargs,
) -> str:
    """
    Fast generate using E4B (always warm).
    Used for: gate classification, quick narration, slot checks.
    """
    defaults = _TIER_DEFAULTS['fast']
    return generate(
        prompt, system,
        model=model or config.OLLAMA_MODEL_FAST,
        images_b64=images_b64,
        temperature=temperature if temperature is not None else defaults['temperature'],
        num_predict=num_predict if num_predict is not None else defaults['num_predict'],
        num_ctx=defaults['num_ctx'],
        keep_alive=keep_alive,
        **kwargs,
    )


def generate_deep(
    prompt: str,
    system: str,
    *,
    model: str | None = None,
    images_b64: list[str] | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
    keep_alive: str = '10m',
    **kwargs,
) -> str:
    """
    Deep generate using 26B MoE.
    Used for: tiers 0-4 narration, standard analysis.
    MoE inference speed (~132 tok/s) is faster than 31B dense.
    """
    defaults = _TIER_DEFAULTS['deep']
    return generate(
        prompt, system,
        model=model or config.OLLAMA_MODEL_DEEP,
        images_b64=images_b64,
        temperature=temperature if temperature is not None else defaults['temperature'],
        num_predict=num_predict if num_predict is not None else defaults['num_predict'],
        num_ctx=defaults['num_ctx'],
        keep_alive=keep_alive,
        **kwargs,
    )


def generate_expert(
    prompt: str,
    system: str,
    *,
    model: str | None = None,
    images_b64: list[str] | None = None,
    temperature: float | None = None,
    num_predict: int | None = None,
    keep_alive: str = '10m',
    **kwargs,
) -> str:
    """
    Expert generate using 31B dense.
    Used for: tiers 5-6 narration, researcher-level analysis.
    Higher quality but slower (~50 tok/s). Use sparingly.
    """
    defaults = _TIER_DEFAULTS['expert']
    return generate(
        prompt, system,
        model=model or config.OLLAMA_MODEL_EXPERT,
        images_b64=images_b64,
        temperature=temperature if temperature is not None else defaults['temperature'],
        num_predict=num_predict if num_predict is not None else defaults['num_predict'],
        num_ctx=defaults['num_ctx'],
        keep_alive=keep_alive,
        **kwargs,
    )
