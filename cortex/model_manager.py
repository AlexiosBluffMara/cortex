"""
Cortex Model Manager — dynamic Gemma 4 model swapping for Ollama.

Architecture:
  - Tier 0 (fast / gate):    gemma4:e4b        ~9.6 GB  ~197 tok/s  always warm
  - Tier 1 (analysis):       gemma4:26b        ~18 GB   ~132 tok/s  loaded on demand
  - Tier 2 (deep / quality): gemma4:31b        ~19.9 GB ~51 tok/s   loaded on demand

The E4B is kept alive permanently (keep_alive=-1 in Ollama).
The 26B/31B are loaded only while an analysis job is running, then unloaded.

Ollama on Windows with RTX 5090 (Blackwell sm_120):
  - OLLAMA_FLASH_ATTENTION=1 reduces VRAM at long contexts
  - OLLAMA_KV_CACHE_TYPE=q8_0 compresses KV cache
  - num_gpu_layers: 999 to ensure 100% VRAM usage (zero CPU offload)

Model latency profile (measured on RTX 5090, 32 GB GDDR7):
  Model              Size    VRAM    Load    Img     Txt     Tok/s
  gemma4:e2b         7.2 GB  ~8 GB   10s     366ms   184ms   232.7
  gemma4:e4b         9.6 GB  ~10 GB  3s      419ms   214ms   195.7
  gemma4:e4b-q8_0   11.6 GB  ~12 GB  10s     566ms   266ms   116.3
  gemma4:e4b-bf16   16.0 GB  ~16 GB  12s     681ms   348ms   88.9
  gemma4:26b        18.0 GB  ~19 GB  8s      560ms   219ms   132.2   <- MoE sweet spot
  gemma4:31b        19.9 GB  ~21 GB  9s      938ms   455ms   50.5
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from enum import IntEnum

import requests

from . import config
from .logger import log

# -- Model tier definitions ----------------------------------------------------

class ModelTier(IntEnum):
    FAST   = 0   # E4B Q4 -- gate, classify, quick narration
    DEEP   = 1   # 26B MoE Q4 -- analysis, standard narration (tiers 0-4)
    EXPERT = 2   # 31B dense Q4 -- premium narration (tiers 5-6, researchers)


_TIER_MODELS: dict[ModelTier, str] = {
    ModelTier.FAST:   os.environ.get('MODEL_FAST',   'gemma4:e4b'),
    ModelTier.DEEP:   os.environ.get('MODEL_DEEP',   'gemma4:26b'),
    ModelTier.EXPERT: os.environ.get('MODEL_EXPERT', 'gemma4:31b'),
}

# Ollama keep_alive values
_KEEP_ALIVE_PERMANENT = '60m'  # E4B always warm
_KEEP_ALIVE_SESSION   = '10m'  # 26B/31B kept warm during a job
_KEEP_ALIVE_ZERO      = '0s'   # Unload immediately

# Estimated VRAM for each tier (GB) -- for VRAM headroom checks
_TIER_VRAM_GB: dict[ModelTier, float] = {
    ModelTier.FAST:   10.0,
    ModelTier.DEEP:   19.0,
    ModelTier.EXPERT: 21.0,
}

TOTAL_VRAM_GB = float(os.environ.get('TOTAL_VRAM_GB', '32.0'))


# -- Model Manager -------------------------------------------------------------

@dataclass
class ModelStatus:
    name: str
    loaded: bool = False
    load_time_s: float = 0.0
    last_used: float = 0.0
    tok_per_s: float = 0.0
    total_calls: int = 0


class ModelManager:
    """
    Manages lifecycle of Gemma 4 models in Ollama.

    Usage:
        mm = ModelManager()
        async with mm.using(ModelTier.DEEP):
            text = mm.generate(prompt, system, tier=ModelTier.DEEP)
    """

    def __init__(self):
        self._ollama_url = config.OLLAMA_URL.rstrip('/')
        self._lock = asyncio.Lock()
        self._status: dict[ModelTier, ModelStatus] = {
            t: ModelStatus(name=_TIER_MODELS[t]) for t in ModelTier
        }
        self._active_tier: ModelTier | None = None

    # -- Properties ------------------------------------------------------------

    def model_name(self, tier: ModelTier) -> str:
        return _TIER_MODELS[tier]

    def fast_model(self) -> str:
        return _TIER_MODELS[ModelTier.FAST]

    def deep_model(self) -> str:
        return _TIER_MODELS[ModelTier.DEEP]

    def expert_model(self) -> str:
        return _TIER_MODELS[ModelTier.EXPERT]

    # -- Ollama API helpers ----------------------------------------------------

    def _unload(self, model: str) -> None:
        """Ask Ollama to unload a model immediately."""
        try:
            requests.post(
                f'{self._ollama_url}/api/generate',
                json={'model': model, 'keep_alive': '0s'},
                timeout=10,
            )
            log.info('[model_manager] unloaded %s', model)
        except Exception as exc:
            log.warning('[model_manager] unload %s failed: %s', model, exc)

    def _preload(self, model: str, keep_alive: str = _KEEP_ALIVE_SESSION) -> float:
        """Warm-load a model into VRAM. Returns load time in seconds."""
        t0 = time.time()
        try:
            r = requests.post(
                f'{self._ollama_url}/api/generate',
                json={
                    'model':      model,
                    'prompt':     '',
                    'keep_alive': keep_alive,
                    'stream':     False,
                    'options':    {'num_predict': 0},
                },
                timeout=120,
            )
            r.raise_for_status()
            elapsed = time.time() - t0
            log.info('[model_manager] preloaded %s in %.1fs', model, elapsed)
            return elapsed
        except Exception as exc:
            log.error('[model_manager] preload %s failed: %s', model, exc)
            return 0.0

    # -- Public interface ------------------------------------------------------

    def warm_fast_model(self) -> None:
        """
        Keep the FAST model (E4B) warm permanently.
        Call once on startup; it stays resident until Ollama is restarted.
        """
        model = self.fast_model()
        elapsed = self._preload(model, keep_alive=_KEEP_ALIVE_PERMANENT)
        st = self._status[ModelTier.FAST]
        st.loaded = True
        st.load_time_s = elapsed
        st.last_used = time.time()

    async def async_warm_fast_model(self) -> None:
        """Async wrapper for warm_fast_model."""
        await asyncio.get_event_loop().run_in_executor(None, self.warm_fast_model)

    async def swap_in(self, tier: ModelTier) -> str:
        """
        Load the requested tier's model, unloading others if needed.
        Returns the model name to pass to Ollama.
        Thread-safe via asyncio.Lock.
        """
        model = _TIER_MODELS[tier]

        async with self._lock:
            if tier == ModelTier.FAST:
                # E4B is always already warm
                return model

            # Unload any active deep/expert model (they're too large to coexist with 31B)
            if self._active_tier is not None and self._active_tier != tier:
                old_model = _TIER_MODELS[self._active_tier]
                await asyncio.get_event_loop().run_in_executor(
                    None, self._unload, old_model
                )
                self._status[self._active_tier].loaded = False

            # Load the new tier
            t0 = time.time()
            await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._preload(model, _KEEP_ALIVE_SESSION)
            )
            self._active_tier = tier
            st = self._status[tier]
            st.loaded = True
            st.load_time_s = time.time() - t0
            st.last_used = time.time()
            log.info('[model_manager] swapped in %s (tier=%s)', model, tier.name)
            return model

    async def swap_out(self, tier: ModelTier) -> None:
        """Unload tier's model after a job completes."""
        if tier == ModelTier.FAST:
            return  # Never unload E4B

        async with self._lock:
            if self._active_tier == tier:
                model = _TIER_MODELS[tier]
                await asyncio.get_event_loop().run_in_executor(
                    None, self._unload, model
                )
                self._status[tier].loaded = False
                self._active_tier = None

    def using(self, tier: ModelTier) -> _ModelContext:
        """Context manager that loads on enter, unloads on exit."""
        return _ModelContext(self, tier)

    def track_call(self, tier: ModelTier, tok_per_s: float) -> None:
        """Record a completed generate call for telemetry."""
        st = self._status[tier]
        st.total_calls += 1
        st.last_used = time.time()
        # Exponential moving average
        if st.tok_per_s == 0:
            st.tok_per_s = tok_per_s
        else:
            st.tok_per_s = 0.9 * st.tok_per_s + 0.1 * tok_per_s

    def status_report(self) -> str:
        """Return a one-line status string for embeds."""
        parts = []
        for tier, st in self._status.items():
            icon = '[ON]' if st.loaded else '[OFF]'
            parts.append(f'{icon} {st.name} ({st.tok_per_s:.0f} t/s)' if st.tok_per_s else f'{icon} {st.name}')
        return '  |  '.join(parts)


class _ModelContext:
    """Async context manager returned by ModelManager.using()."""

    def __init__(self, manager: ModelManager, tier: ModelTier):
        self._manager = manager
        self._tier = tier
        self._model_name: str = ''

    async def __aenter__(self) -> str:
        self._model_name = await self._manager.swap_in(self._tier)
        return self._model_name

    async def __aexit__(self, *exc_info) -> None:
        await self._manager.swap_out(self._tier)


# -- Singleton -----------------------------------------------------------------

_manager: ModelManager | None = None


def get_manager() -> ModelManager:
    global _manager
    if _manager is None:
        _manager = ModelManager()
    return _manager
