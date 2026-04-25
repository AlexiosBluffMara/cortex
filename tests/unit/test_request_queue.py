"""Tests for cortex.request_queue — priority queueing + fallback routing."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from cortex.gpu_scheduler import GPUState
from cortex.request_queue import (
    FallbackProvider,
    PendingRequest,
    RequestQueue,
    RequestType,
)

pytestmark = pytest.mark.unit


@pytest.fixture
def fake_scheduler():
    s = MagicMock()
    s.state = GPUState.GEMMA_ACTIVE
    s.run_brain_scan = AsyncMock(return_value={"top_rois": [1, 2, 3]})
    s.run_gemma_generate = AsyncMock(return_value="generated text")
    s.ensure_gemma = AsyncMock()
    s._ollama_url = "http://localhost:11434"
    return s


class TestPendingRequestOrdering:
    def test_lower_priority_value_orders_first(self):
        a = PendingRequest(priority=0, source="user")
        b = PendingRequest(priority=5, source="batch")
        assert a < b

    def test_age_increases_over_time(self, monkeypatch):
        # The dataclass captures time.time at class-definition time via
        # field(default_factory=time.time), so monkeypatching after import
        # won't influence created_at. Pass created_at explicitly and patch
        # time.time for the property's lazy call.
        import time
        monkeypatch.setattr(time, "time", lambda: 1010.0)
        r = PendingRequest(priority=0, created_at=1000.0)
        assert r.age_s == pytest.approx(10.0)


class TestRequestQueueSubmit:
    @pytest.mark.asyncio
    async def test_submit_brain_scan_runs_via_scheduler(self, fake_scheduler):
        q = RequestQueue(scheduler=fake_scheduler)
        result = await q.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/foo.mp4"},
            priority=0,
            source="webui",
        )
        assert result == {"top_rois": [1, 2, 3]}
        fake_scheduler.run_brain_scan.assert_awaited_once_with("/tmp/foo.mp4", priority=0)
        assert q._completed_count == 1

    @pytest.mark.asyncio
    async def test_submit_gemma_chat_when_idle(self, fake_scheduler):
        q = RequestQueue(scheduler=fake_scheduler)
        result = await q.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi", "system": "be helpful", "tier": 0},
            priority=5,
            source="discord",
        )
        assert result == "generated text"
        fake_scheduler.run_gemma_generate.assert_awaited_once()


class TestFallbackRouting:
    @pytest.mark.asyncio
    async def test_chat_routes_to_fallback_when_tribe_active(self, fake_scheduler):
        fake_scheduler.state = GPUState.TRIBE_ACTIVE
        fb = MagicMock(spec=FallbackProvider)
        fb.generate = AsyncMock(return_value="fallback text")

        q = RequestQueue(scheduler=fake_scheduler, fallback=fb)
        result = await q.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi"},
            source="webui",
        )
        assert result == "fallback text"
        fb.generate.assert_awaited_once()
        # Scheduler was NOT used because we routed straight to fallback
        fake_scheduler.run_gemma_generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_brain_scan_does_not_route_to_fallback_when_tribe_active(
        self, fake_scheduler
    ):
        fake_scheduler.state = GPUState.TRIBE_ACTIVE
        fb = MagicMock(spec=FallbackProvider)
        fb.generate = AsyncMock()

        q = RequestQueue(scheduler=fake_scheduler, fallback=fb)
        await q.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/x.mp4"},
            source="webui",
        )
        # Brain scan is the load-bearing thing; it stays on scheduler
        fb.generate.assert_not_called()
        fake_scheduler.run_brain_scan.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_fallback_means_chat_queues_normally(self, fake_scheduler):
        fake_scheduler.state = GPUState.TRIBE_ACTIVE
        q = RequestQueue(scheduler=fake_scheduler, fallback=None)
        # With no fallback, even during TRIBE_ACTIVE, the chat enqueues normally
        # and waits for processing (fake scheduler.run_gemma_generate is a stub).
        result = await q.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi"},
            source="webui",
        )
        assert result == "generated text"


class TestQueueFull:
    @pytest.mark.asyncio
    async def test_queue_full_raises_runtime_error(self, fake_scheduler, monkeypatch):
        q = RequestQueue(scheduler=fake_scheduler)
        # Prevent the processing loop from draining the queue while we fill it.
        monkeypatch.setattr(q, "_process_loop", AsyncMock())

        # Override the internal asyncio.PriorityQueue to a tiny one
        q._queue = asyncio.PriorityQueue(maxsize=1)
        # Drop one request in but never await its future
        await q._queue.put(
            PendingRequest(priority=0, payload={}, future=asyncio.get_event_loop().create_future())
        )

        with pytest.raises(RuntimeError, match="queue full"):
            await q.submit(
                request_type=RequestType.GEMMA_CHAT,
                payload={"prompt": "x"},
                source="test",
            )


class TestStatus:
    @pytest.mark.asyncio
    async def test_status_includes_required_fields(self, fake_scheduler):
        q = RequestQueue(scheduler=fake_scheduler)
        s = q.status()
        assert s["queue_depth"] == 0
        assert s["processing"] is False
        assert s["active_request"] is None
        assert s["gpu_state"] == "gemma_active"
        assert s["completed"] == 0
        assert s["failed"] == 0
