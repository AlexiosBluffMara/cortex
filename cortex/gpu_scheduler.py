"""
GPU Priority Scheduler — manages exclusive access to 5090 VRAM.

State machine:
  IDLE         → no models loaded
  GEMMA_ACTIVE → Gemma E4B (or higher tier) loaded in Ollama
  TRIBE_ACTIVE → TRIBE v2 loaded via PyTorch

Transitions:
  GEMMA_ACTIVE → TRIBE_ACTIVE:
    1. Unload all Gemma models (Ollama keep_alive=0s)
    2. Wait for Ollama to release VRAM (poll nvidia-smi)
    3. Load TRIBE v2 via pipeline.load_model()

  TRIBE_ACTIVE → GEMMA_ACTIVE:
    1. Delete TRIBE model reference, torch.cuda.empty_cache()
    2. gc.collect()
    3. Warm Gemma E4B via model_manager.warm_fast_model()

Swap latency (measured):
  GEMMA → TRIBE: ~15-20s (model load + CUDA graph warmup)
  TRIBE → GEMMA: ~5-8s (cache clear + E4B warm)
"""
from __future__ import annotations

import asyncio
import enum
import gc
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from cortex import config
from cortex.logger import log
from cortex.model_manager import ModelTier, get_manager


class GPUState(enum.Enum):
    IDLE = "idle"
    GEMMA_ACTIVE = "gemma_active"
    TRIBE_ACTIVE = "tribe_active"
    SWAPPING = "swapping"


@dataclass
class SwapMetrics:
    """Track swap performance for dashboards."""
    total_swaps: int = 0
    total_swap_time_s: float = 0.0
    last_swap_time_s: float = 0.0
    oom_recoveries: int = 0
    failed_swaps: int = 0

    @property
    def avg_swap_time_s(self) -> float:
        return self.total_swap_time_s / max(self.total_swaps, 1)


class GPUScheduler:
    """
    Coordinates exclusive GPU access between TRIBE v2 (PyTorch) and
    Gemma 4 models (Ollama). They cannot coexist in 32GB VRAM.
    """

    TOTAL_VRAM_GB = 32.0
    TRIBE_VRAM_GB = 22.4
    GEMMA_E4B_VRAM_GB = 10.0
    VRAM_SAFETY_MARGIN_GB = 1.0
    SWAP_TIMEOUT_S = 120
    VRAM_POLL_INTERVAL_S = 0.5
    VRAM_POLL_MAX_ATTEMPTS = 60  # 30 seconds

    # All known Gemma 4 models to unload before TRIBE
    _GEMMA_MODELS = ["gemma4:e4b", "gemma4:26b", "gemma4:31b",
                     "gemma4:e2b", "gemma4:e4b-q8_0", "gemma4:e4b-bf16"]

    def __init__(self):
        self._state = GPUState.IDLE
        self._lock = asyncio.Lock()
        self._mm = get_manager()
        self._ollama_url = config.OLLAMA_URL.rstrip("/")
        self._tribe_model = None
        self._metrics = SwapMetrics()
        self._state_listeners: list[Callable[[GPUState], None]] = []
        # Optional inference fallback (set via `set_inference_fallback`).
        # Defaults to None — OOM keeps the existing behavior of cleaning up
        # and re-raising. When set, OOM tries the fallback first before
        # raising a CortexException.
        self._inference_fallback: Any = None

    def set_inference_fallback(self, fallback: Any) -> None:
        """Register an InferenceFallback to use on local CUDA OOM.

        The fallback is called only when (1) the local TRIBE inference raises
        an OOM RuntimeError and (2) `fallback.available()` returns True. When
        it succeeds, `run_brain_scan` returns the remote result; when it
        fails, a `CortexException` is raised carrying the structured error.
        """
        self._inference_fallback = fallback

    @property
    def state(self) -> GPUState:
        return self._state

    @property
    def metrics(self) -> SwapMetrics:
        return self._metrics

    def on_state_change(self, listener: Callable[[GPUState], None]) -> None:
        """Register a callback for state transitions (for WebSocket push, etc.)."""
        self._state_listeners.append(listener)

    def _notify_state(self, new_state: GPUState) -> None:
        old = self._state
        self._state = new_state
        for fn in self._state_listeners:
            try:
                fn(new_state)
            except Exception:
                pass
        log.info("[gpu_scheduler] %s -> %s", old.value, new_state.value)

    # -- VRAM monitoring --

    def get_free_vram_gb(self) -> float:
        """Query nvidia-smi for actual free VRAM."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            return float(result.stdout.strip().split("\n")[0]) / 1024  # MB → GB
        except Exception as exc:
            log.warning("[gpu_scheduler] nvidia-smi failed: %s", exc)
            return 0.0

    def get_used_vram_gb(self) -> float:
        """Query nvidia-smi for used VRAM."""
        try:
            result = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5,
            )
            return float(result.stdout.strip().split("\n")[0]) / 1024
        except Exception:
            return 0.0

    def vram_report(self) -> dict:
        """Full VRAM status for dashboards."""
        free = self.get_free_vram_gb()
        used = self.get_used_vram_gb()
        return {
            "state": self._state.value,
            "total_gb": self.TOTAL_VRAM_GB,
            "used_gb": round(used, 2),
            "free_gb": round(free, 2),
            "tribe_fits": free >= (self.TRIBE_VRAM_GB + self.VRAM_SAFETY_MARGIN_GB),
            "gemma_e4b_fits": free >= self.GEMMA_E4B_VRAM_GB,
            "swap_metrics": {
                "total_swaps": self._metrics.total_swaps,
                "avg_swap_time_s": round(self._metrics.avg_swap_time_s, 2),
                "oom_recoveries": self._metrics.oom_recoveries,
            },
        }

    # -- Swap operations --

    async def _wait_for_vram(self, required_gb: float) -> bool:
        """Poll nvidia-smi until required VRAM is free. Returns True on success."""
        target = required_gb + self.VRAM_SAFETY_MARGIN_GB
        for attempt in range(self.VRAM_POLL_MAX_ATTEMPTS):
            free = self.get_free_vram_gb()
            if free >= target:
                log.info("[gpu_scheduler] VRAM ready: %.1fGB free (need %.1fGB)",
                         free, target)
                return True
            await asyncio.sleep(self.VRAM_POLL_INTERVAL_S)
        log.error("[gpu_scheduler] VRAM timeout: %.1fGB free, need %.1fGB",
                  self.get_free_vram_gb(), target)
        return False

    def _unload_all_gemma_sync(self) -> None:
        """Unload every Gemma model from Ollama."""
        import requests as req
        for model in self._GEMMA_MODELS:
            try:
                req.post(
                    f"{self._ollama_url}/api/generate",
                    json={"model": model, "keep_alive": "0s"},
                    timeout=10,
                )
            except Exception:
                pass
        log.info("[gpu_scheduler] All Gemma models unloaded from Ollama")

    def _unload_tribe_sync(self) -> None:
        """Force-unload TRIBE v2 from VRAM."""
        import torch

        self._tribe_model = None

        # Clear the pipeline singleton
        from cortex import pipeline
        pipeline._model = None
        pipeline._compiled = False

        torch.cuda.empty_cache()
        gc.collect()

        # Double-clear: sometimes PyTorch holds onto allocator blocks
        torch.cuda.empty_cache()
        log.info("[gpu_scheduler] TRIBE v2 unloaded. Free VRAM: %.1fGB",
                 self.get_free_vram_gb())

    def _load_tribe_sync(self):
        """Load TRIBE v2 into VRAM. Returns the model."""
        from cortex.pipeline import load_model
        model = load_model()
        self._tribe_model = model
        return model

    async def _swap_gemma_to_tribe(self) -> None:
        """Full swap: unload Gemma → wait for VRAM → load TRIBE."""
        t0 = time.time()
        self._notify_state(GPUState.SWAPPING)

        loop = asyncio.get_event_loop()

        # Step 1: Unload all Gemma models
        await loop.run_in_executor(None, self._unload_all_gemma_sync)

        # Step 2: Wait for VRAM
        if not await self._wait_for_vram(self.TRIBE_VRAM_GB):
            self._metrics.failed_swaps += 1
            # Emergency: force gc and retry once
            gc.collect()
            await asyncio.sleep(2.0)
            if not await self._wait_for_vram(self.TRIBE_VRAM_GB):
                self._notify_state(GPUState.IDLE)
                raise RuntimeError(
                    f"VRAM stuck at {self.get_free_vram_gb():.1f}GB free, "
                    f"need {self.TRIBE_VRAM_GB}GB for TRIBE v2"
                )

        # Step 3: Load TRIBE
        try:
            await loop.run_in_executor(None, self._load_tribe_sync)
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                self._metrics.oom_recoveries += 1
                log.error("[gpu_scheduler] OOM loading TRIBE. Attempting recovery...")
                await loop.run_in_executor(None, self._unload_tribe_sync)
                gc.collect()
                await asyncio.sleep(3.0)
                # Retry once
                await loop.run_in_executor(None, self._load_tribe_sync)
            else:
                raise

        elapsed = time.time() - t0
        self._metrics.total_swaps += 1
        self._metrics.total_swap_time_s += elapsed
        self._metrics.last_swap_time_s = elapsed
        self._notify_state(GPUState.TRIBE_ACTIVE)
        log.info("[gpu_scheduler] GEMMA -> TRIBE complete in %.1fs", elapsed)

    async def _swap_tribe_to_gemma(self) -> None:
        """Full swap: unload TRIBE → wait for VRAM → warm Gemma E4B."""
        t0 = time.time()
        self._notify_state(GPUState.SWAPPING)

        loop = asyncio.get_event_loop()

        # Step 1: Unload TRIBE
        await loop.run_in_executor(None, self._unload_tribe_sync)

        # Step 2: Wait for VRAM
        await self._wait_for_vram(self.GEMMA_E4B_VRAM_GB)

        # Step 3: Reload Gemma E4B
        await loop.run_in_executor(None, self._mm.warm_fast_model)

        elapsed = time.time() - t0
        self._metrics.total_swaps += 1
        self._metrics.total_swap_time_s += elapsed
        self._metrics.last_swap_time_s = elapsed
        self._notify_state(GPUState.GEMMA_ACTIVE)
        log.info("[gpu_scheduler] TRIBE -> GEMMA complete in %.1fs", elapsed)

    # -- Public interface --

    async def ensure_gemma(self) -> None:
        """Ensure Gemma E4B is loaded and ready. Swaps from TRIBE if needed."""
        async with self._lock:
            if self._state == GPUState.GEMMA_ACTIVE:
                return
            if self._state == GPUState.TRIBE_ACTIVE:
                await self._swap_tribe_to_gemma()
            elif self._state == GPUState.IDLE:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._mm.warm_fast_model)
                self._notify_state(GPUState.GEMMA_ACTIVE)

    async def ensure_tribe(self) -> None:
        """Ensure TRIBE v2 is loaded and ready. Swaps from Gemma if needed."""
        async with self._lock:
            if self._state == GPUState.TRIBE_ACTIVE:
                return
            if self._state == GPUState.GEMMA_ACTIVE:
                await self._swap_gemma_to_tribe()
            elif self._state == GPUState.IDLE:
                loop = asyncio.get_event_loop()
                await loop.run_in_executor(None, self._load_tribe_sync)
                self._notify_state(GPUState.TRIBE_ACTIVE)

    async def run_brain_scan(self, media_path: str, priority: int = 0) -> Any:
        """
        Full brain scan: swap to TRIBE → inference → swap back to Gemma.

        This is the primary entry point for brain scans. It handles the full
        GPU lifecycle: unload Gemma, load TRIBE, run inference, unload TRIBE,
        reload Gemma.

        Args:
            media_path: Path to preprocessed video/audio/text file
            priority: Request priority (0=highest). Used for queue ordering.

        Returns:
            InferenceResult from cortex.pipeline
        """
        await self.ensure_tribe()

        loop = asyncio.get_event_loop()
        try:
            result = await loop.run_in_executor(
                None,
                lambda: _import_run_inference()(media_path)
            )
        except RuntimeError as exc:
            if "out of memory" in str(exc).lower():
                self._metrics.oom_recoveries += 1
                log.error("[gpu_scheduler] OOM during inference. Cleaning up...")
                await loop.run_in_executor(None, self._unload_tribe_sync)
                self._notify_state(GPUState.IDLE)

                # Try the configured inference fallback (e.g. GCP A100) before
                # giving up. If the fallback is unset or can't run, preserve
                # the legacy behavior of re-raising the original OOM error so
                # callers that don't know about CortexError still see a
                # familiar exception.
                fallback = self._inference_fallback
                if fallback is not None and fallback.available():
                    log.warning(
                        "[gpu_scheduler] Routing to inference fallback %s",
                        getattr(fallback, "name", "<unknown>"),
                    )
                    fallback_result = await fallback.submit(media_path)
                    # Lazy-import to avoid a runtime dep when the fallback
                    # module isn't installed in a minimal environment.
                    from cortex.errors import CortexError, CortexException
                    if isinstance(fallback_result, CortexError):
                        raise CortexException(fallback_result)
                    # Don't swap back to GEMMA here — we never loaded TRIBE
                    # successfully, so VRAM is already free. Caller's next
                    # narrate request will trigger ensure_gemma().
                    return fallback_result

                raise
            raise

        # Swap back to Gemma for narration
        await self.ensure_gemma()
        return result

    async def run_gemma_generate(
        self,
        prompt: str,
        system: str,
        tier: ModelTier = ModelTier.FAST,
        **kwargs,
    ) -> str:
        """Run a Gemma generation, ensuring Gemma is loaded first."""
        await self.ensure_gemma()

        model = await self._mm.swap_in(tier)
        from cortex.ollama_client import generate
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(
            None,
            lambda: generate(prompt, system, model=model, **kwargs)
        )

    def status_line(self) -> str:
        """One-line status for embeds and dashboards."""
        free = self.get_free_vram_gb()
        return (
            f"[{self._state.value}] "
            f"VRAM: {self.TOTAL_VRAM_GB - free:.1f}/{self.TOTAL_VRAM_GB}GB used | "
            f"Swaps: {self._metrics.total_swaps} "
            f"(avg {self._metrics.avg_swap_time_s:.1f}s)"
        )


def _import_run_inference():
    """Lazy import to avoid circular dependency."""
    from cortex.pipeline import run_inference
    return run_inference


# -- Singleton --

_scheduler: GPUScheduler | None = None


def get_scheduler() -> GPUScheduler:
    global _scheduler
    if _scheduler is None:
        _scheduler = GPUScheduler()
    return _scheduler
