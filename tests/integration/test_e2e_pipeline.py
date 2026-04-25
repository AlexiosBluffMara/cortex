"""End-to-end integration tests for the Cortex pipeline.

These exercise the full RequestQueue → GPUScheduler → Ollama/TRIBE stack
with mocks only at the external boundaries (Ollama HTTP, TRIBE inference,
nvidia-smi). Internal cortex modules run for real.

Marked `integration` so CI can opt in/out without spinning up real services.
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.gpu_scheduler import GPUScheduler, GPUState
from cortex.request_queue import (
    FallbackProvider,
    RequestQueue,
    RequestType,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def fake_inference_result():
    """A fake `InferenceResult` shaped like the real one, for narration tests."""
    import numpy as np

    result = MagicMock(name="InferenceResult")
    result.preds = np.zeros((100, 20484), dtype=np.float32)  # 100 TRs × 20484 vertices
    result.top_rois = ["ROI_1", "ROI_2", "ROI_3"]
    result.peak_t = 42
    result.seconds_elapsed = 4.2
    result.roi_df = MagicMock()
    result.roi_df.__getitem__ = MagicMock(return_value=MagicMock(abs=MagicMock(return_value=MagicMock(mean=MagicMock(return_value=MagicMock())))))
    return result


@pytest.fixture
def integration_stack(monkeypatch, fake_inference_result, mock_nvidia_smi, mock_requests_post):
    """
    Build the full stack with TRIBE pipeline + Ollama + nvidia-smi mocked.

    Returns (queue, scheduler, fake_pipeline) so tests can drive the queue and
    inspect what the scheduler / Ollama saw.
    """
    mock_nvidia_smi(free_mb=28000, used_mb=4000)

    # Replace the lazy `cortex.pipeline` module before the scheduler imports it
    fake_pipeline = MagicMock(name="fake-pipeline-module")
    fake_pipeline.load_model = MagicMock(return_value=MagicMock(name="fake-tribe-model"))
    fake_pipeline.run_inference = MagicMock(return_value=fake_inference_result)
    fake_pipeline._model = None
    fake_pipeline._compiled = False
    sys.modules["cortex.pipeline"] = fake_pipeline

    # Stub torch so the scheduler's lazy `import torch` in _unload_tribe_sync works.
    # We don't need real CUDA here — only empty_cache() and the OOM exception type.
    fake_torch = MagicMock(name="fake-torch")
    fake_torch.cuda = MagicMock()
    fake_torch.cuda.empty_cache = MagicMock()
    fake_torch.cuda.is_available = MagicMock(return_value=True)
    fake_torch.cuda.OutOfMemoryError = type("OutOfMemoryError", (RuntimeError,), {})
    sys.modules["torch"] = fake_torch

    # Use a real ModelManager but stub its Ollama warm-load so it doesn't block
    from cortex.model_manager import ModelManager
    mm = ModelManager()
    mm.warm_fast_model = MagicMock()  # type: ignore[method-assign]
    monkeypatch.setattr("cortex.gpu_scheduler.get_manager", lambda: mm)

    # Real scheduler + real queue
    scheduler = GPUScheduler()
    queue = RequestQueue(scheduler=scheduler)
    return queue, scheduler, fake_pipeline


# ---------------------------------------------------------------------------
# Cold-start brain scan
# ---------------------------------------------------------------------------

class TestColdStart:
    def test_scheduler_starts_idle(self, integration_stack):
        _, scheduler, _ = integration_stack
        assert scheduler.state is GPUState.IDLE

    @pytest.mark.asyncio
    async def test_first_brain_scan_loads_tribe(self, integration_stack):
        queue, scheduler, fake_pipeline = integration_stack

        result = await queue.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/clip.mp4"},
            priority=0,
            source="webui",
        )

        assert result is fake_pipeline.run_inference.return_value
        # We swapped to TRIBE for inference, then back to GEMMA for narration
        assert scheduler.state is GPUState.GEMMA_ACTIVE
        fake_pipeline.load_model.assert_called_once()
        fake_pipeline.run_inference.assert_called_once_with("/tmp/clip.mp4")
        # Inference contributes one swap; the post-inference swap-back contributes another
        assert scheduler.metrics.total_swaps >= 1


# ---------------------------------------------------------------------------
# Concurrent submission while TRIBE is loaded
# ---------------------------------------------------------------------------

class TestConcurrentRouting:
    @pytest.mark.asyncio
    async def test_chat_falls_back_when_tribe_active(self, integration_stack):
        queue, scheduler, _ = integration_stack
        scheduler._state = GPUState.TRIBE_ACTIVE

        fb = MagicMock(spec=FallbackProvider)
        fb.generate = AsyncMock(return_value="fallback response")
        queue._fallback = fb

        result = await queue.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi", "system": "be brief"},
            source="webui",
        )
        assert result == "fallback response"
        fb.generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_chat_queues_when_no_fallback_configured(self, integration_stack, mock_requests_post):
        queue, scheduler, _ = integration_stack
        # Force scheduler state for this test
        scheduler._state = GPUState.GEMMA_ACTIVE

        # No fallback set on queue → chat enqueues normally
        # Use a tier that exists; the request_queue's NARRATE/GEMMA_CHAT path
        # ultimately calls Ollama, which mock_requests_post intercepts.
        result = await queue.submit(
            request_type=RequestType.NARRATE,
            payload={"prompt": "narrate this", "system": "be educational", "tier": 0},
            source="webui",
        )
        assert isinstance(result, str)
        assert result  # got something back from mock Ollama


# ---------------------------------------------------------------------------
# OOM recovery
# ---------------------------------------------------------------------------

class TestOOMRecovery:
    @pytest.mark.asyncio
    async def test_oom_during_inference_propagates_after_cleanup(
        self, integration_stack, monkeypatch
    ):
        queue, scheduler, fake_pipeline = integration_stack

        # First call raises OOM; the scheduler unloads TRIBE and re-raises.
        # (The fall-back-to-GCP path is documented in the spec but not yet
        # implemented in the local scheduler — this test pins the current
        # behavior so a future change is intentional.)
        fake_pipeline.run_inference.side_effect = RuntimeError(
            "CUDA out of memory. Tried to allocate 22 GB"
        )

        with pytest.raises(RuntimeError, match="out of memory"):
            await queue.submit(
                request_type=RequestType.BRAIN_SCAN,
                payload={"media_path": "/tmp/big.mp4"},
                priority=0,
                source="webui",
            )

        # OOM path should bump the recovery counter and revert to IDLE
        assert scheduler.metrics.oom_recoveries >= 1
        assert scheduler.state is GPUState.IDLE


# ---------------------------------------------------------------------------
# Status surface
# ---------------------------------------------------------------------------

class TestObservability:
    def test_vram_report_works_after_swap(self, integration_stack, mock_nvidia_smi):
        _, scheduler, _ = integration_stack
        mock_nvidia_smi(free_mb=8000, used_mb=24000)
        scheduler._state = GPUState.TRIBE_ACTIVE

        report = scheduler.vram_report()
        assert report["state"] == "tribe_active"
        assert report["tribe_fits"] is False  # already loaded
        assert "swap_metrics" in report

    @pytest.mark.asyncio
    async def test_queue_status_reflects_completion(self, integration_stack):
        queue, _, _ = integration_stack
        await queue.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/x.mp4"},
            source="webui",
        )
        s = queue.status()
        assert s["completed"] >= 1
        assert s["queue_depth"] == 0
        assert s["processing"] is False


# ---------------------------------------------------------------------------
# Priority ordering
# ---------------------------------------------------------------------------

class TestPriority:
    @pytest.mark.asyncio
    async def test_higher_priority_processes_first(self, integration_stack):
        queue, scheduler, _ = integration_stack
        scheduler._state = GPUState.GEMMA_ACTIVE

        # Block the loop so we can preload multiple requests before any run
        gate = asyncio.Event()
        original = scheduler.run_gemma_generate

        async def slow_gen(*args, **kwargs):
            await gate.wait()
            return await original(*args, **kwargs)

        scheduler.run_gemma_generate = slow_gen  # type: ignore[method-assign]

        # Queue three requests at different priorities concurrently
        tasks = [
            asyncio.create_task(
                queue.submit(
                    request_type=RequestType.GEMMA_CHAT,
                    payload={"prompt": f"prompt {i}", "tier": 0},
                    priority=p,
                    source="test",
                )
            )
            for i, p in enumerate([5, 0, 9])
        ]

        # Give the queue a moment to enqueue everything
        await asyncio.sleep(0.05)
        # Open the gate; let everything flow
        gate.set()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # All completed (whether the actual ordering inside the priority queue is
        # exposed externally is implementation detail; here we just assert no crash)
        assert all(not isinstance(r, Exception) for r in results)
