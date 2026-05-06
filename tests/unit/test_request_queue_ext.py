"""Extended tests for cortex.request_queue — covers paths not reached by
test_request_queue.py (singleton, _execute branches, process_loop, FallbackProvider).
"""
from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from cortex.gpu_scheduler import GPUState
from cortex.request_queue import (
    FallbackProvider,
    PendingRequest,
    RequestQueue,
    RequestType,
    get_queue,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def idle_scheduler():
    s = MagicMock()
    s.state = GPUState.IDLE
    s.run_brain_scan = AsyncMock(return_value={"rois": []})
    s.run_gemma_generate = AsyncMock(return_value="gemma reply")
    s.ensure_gemma = AsyncMock()
    s._ollama_url = "http://localhost:11434"
    return s


@pytest.fixture
def tribe_scheduler():
    s = MagicMock()
    s.state = GPUState.TRIBE_ACTIVE
    s.run_brain_scan = AsyncMock(return_value={"rois": ["DMN"]})
    s.run_gemma_generate = AsyncMock(return_value="gemma reply")
    s.ensure_gemma = AsyncMock()
    s._ollama_url = "http://localhost:11434"
    return s


@pytest.fixture(autouse=True)
def reset_queue_singleton():
    """Each test gets a clean singleton."""
    import cortex.request_queue as rq
    rq._queue = None
    yield
    rq._queue = None


# ---------------------------------------------------------------------------
# PendingRequest
# ---------------------------------------------------------------------------

class TestPendingRequest:
    def test_age_s_property(self, monkeypatch):
        import cortex.request_queue as rq
        monkeypatch.setattr(time, "time", lambda: 2000.0)
        req = PendingRequest(priority=0, created_at=1990.0)
        assert req.age_s == pytest.approx(10.0)

    def test_age_s_near_zero_for_new_request(self):
        req = PendingRequest(priority=5)
        assert req.age_s >= 0.0
        assert req.age_s < 2.0  # sanity: created within the last 2 s

    def test_id_is_12_hex_chars(self):
        req = PendingRequest(priority=0)
        assert len(req.id) == 12
        assert all(c in "0123456789abcdef" for c in req.id)

    def test_default_source(self):
        req = PendingRequest(priority=0)
        assert req.source == "unknown"

    def test_default_request_type(self):
        req = PendingRequest(priority=0)
        assert req.request_type == RequestType.GEMMA_CHAT


# ---------------------------------------------------------------------------
# submit — fast path (TRIBE_ACTIVE + fallback)
# ---------------------------------------------------------------------------

class TestSubmitFastPath:
    @pytest.mark.asyncio
    async def test_tribe_active_with_fallback_bypasses_queue(self, tribe_scheduler):
        fb = MagicMock(spec=FallbackProvider)
        fb.generate = AsyncMock(return_value="fast fallback")
        q = RequestQueue(scheduler=tribe_scheduler, fallback=fb)

        result = await q.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi"},
            source="test",
        )
        assert result == "fast fallback"
        # Queue should remain empty — we bypassed it
        assert q.queue_depth == 0

    @pytest.mark.asyncio
    async def test_tribe_active_no_fallback_enqueues_and_processes(
        self, tribe_scheduler
    ):
        q = RequestQueue(scheduler=tribe_scheduler, fallback=None)
        result = await q.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi", "tier": 0},
            source="test",
        )
        assert result == "gemma reply"

    @pytest.mark.asyncio
    async def test_tribe_active_brain_scan_not_redirected_to_fallback(
        self, tribe_scheduler
    ):
        fb = MagicMock(spec=FallbackProvider)
        fb.generate = AsyncMock(return_value="should not be called")
        q = RequestQueue(scheduler=tribe_scheduler, fallback=fb)

        result = await q.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/x.mp4"},
            source="test",
        )
        assert result == {"rois": ["DMN"]}
        fb.generate.assert_not_awaited()


# ---------------------------------------------------------------------------
# submit — queue full
# ---------------------------------------------------------------------------

class TestSubmitQueueFull:
    @pytest.mark.asyncio
    async def test_queue_full_raises_runtime_error(self, idle_scheduler, monkeypatch):
        q = RequestQueue(scheduler=idle_scheduler)
        monkeypatch.setattr(q, "_process_loop", AsyncMock())
        q._queue = asyncio.PriorityQueue(maxsize=1)
        # Fill it
        dummy_future = asyncio.get_event_loop().create_future()
        await q._queue.put(PendingRequest(priority=0, future=dummy_future))

        with pytest.raises(RuntimeError, match="queue full"):
            await q.submit(
                request_type=RequestType.GEMMA_CHAT,
                payload={"prompt": "overflow"},
                source="test",
            )


# ---------------------------------------------------------------------------
# _process_loop — multiple sequential requests
# ---------------------------------------------------------------------------

class TestProcessLoop:
    @pytest.mark.asyncio
    async def test_processes_multiple_requests_sequentially(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        results = await asyncio.gather(
            q.submit(
                request_type=RequestType.BRAIN_SCAN,
                payload={"media_path": "/tmp/a.mp4"},
                priority=0,
                source="batch",
            ),
            q.submit(
                request_type=RequestType.GEMMA_CHAT,
                payload={"prompt": "hello", "tier": 0},
                priority=5,
                source="discord",
            ),
        )
        assert results[0] == {"rois": []}
        assert results[1] == "gemma reply"
        assert q._completed_count == 2

    @pytest.mark.asyncio
    async def test_failed_request_increments_failed_count(self, idle_scheduler):
        idle_scheduler.run_brain_scan = AsyncMock(side_effect=RuntimeError("GPU OOM"))
        q = RequestQueue(scheduler=idle_scheduler)

        with pytest.raises(RuntimeError, match="GPU OOM"):
            await q.submit(
                request_type=RequestType.BRAIN_SCAN,
                payload={"media_path": "/tmp/boom.mp4"},
                source="test",
            )
        assert q._failed_count == 1

    @pytest.mark.asyncio
    async def test_processing_flag_resets_after_queue_drains(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        await q.submit(
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "hi", "tier": 0},
        )
        # Give the loop a chance to finish
        await asyncio.sleep(0)
        assert q._processing is False


# ---------------------------------------------------------------------------
# _execute — per request type
# ---------------------------------------------------------------------------

class TestExecute:
    @pytest.mark.asyncio
    async def test_execute_brain_scan(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        req = PendingRequest(
            priority=0,
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/clip.mp4"},
            future=asyncio.get_event_loop().create_future(),
        )
        result = await q._execute(req)
        assert result == {"rois": []}
        idle_scheduler.run_brain_scan.assert_awaited_once_with(
            "/tmp/clip.mp4", priority=0
        )

    @pytest.mark.asyncio
    async def test_execute_gemma_chat(self, idle_scheduler, monkeypatch):
        q = RequestQueue(scheduler=idle_scheduler)
        req = PendingRequest(
            priority=5,
            request_type=RequestType.GEMMA_CHAT,
            payload={"prompt": "explain DMN", "system": "be brief", "tier": 0},
            future=asyncio.get_event_loop().create_future(),
        )
        result = await q._execute(req)
        assert result == "gemma reply"
        idle_scheduler.run_gemma_generate.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_narrate(self, idle_scheduler, monkeypatch):
        from unittest.mock import patch as _patch

        mock_generate = MagicMock(return_value="narration text")
        mock_tiers = MagicMock()
        mock_tiers._TIER_MODEL_MAP = {i: "gemma4:26b" for i in range(7)}

        q = RequestQueue(scheduler=idle_scheduler)
        req = PendingRequest(
            priority=5,
            request_type=RequestType.NARRATE,
            payload={
                "prompt": "Describe the brain activity",
                "system": "Be scientific.",
                "tier": 2,
                "temperature": 0.35,
                "num_predict": 600,
            },
            future=asyncio.get_event_loop().create_future(),
        )
        with _patch.dict(
            "sys.modules",
            {
                "cortex.tiers": mock_tiers,
                "cortex.ollama_client": MagicMock(generate=mock_generate),
            },
        ):
            result = await q._execute(req)
        assert result == "narration text"
        idle_scheduler.ensure_gemma.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_vision_gate(self, idle_scheduler, monkeypatch):
        mock_vision_gate = AsyncMock(return_value={"gate": "pass"})
        mock_gemma_vision = MagicMock()
        mock_gemma_vision.vision_gate = mock_vision_gate

        q = RequestQueue(scheduler=idle_scheduler)
        req = PendingRequest(
            priority=3,
            request_type=RequestType.VISION_GATE,
            payload={"keyframe_paths": ["/tmp/kf1.jpg", "/tmp/kf2.jpg"]},
            future=asyncio.get_event_loop().create_future(),
        )
        with patch.dict("sys.modules", {"cortex.gemma_vision": mock_gemma_vision}):
            result = await q._execute(req)
        assert result == {"gate": "pass"}
        idle_scheduler.ensure_gemma.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_execute_unknown_type_raises_value_error(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        req = PendingRequest(
            priority=0,
            request_type=RequestType.BRAIN_SCAN,
            payload={},
            future=asyncio.get_event_loop().create_future(),
        )
        # Forcefully set an invalid type int so _execute hits the else branch
        object.__setattr__(req, "request_type", 99)

        with pytest.raises((ValueError, AttributeError)):
            await q._execute(req)


# ---------------------------------------------------------------------------
# status()
# ---------------------------------------------------------------------------

class TestStatus:
    def test_status_fields_present(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        s = q.status()
        assert set(s.keys()) >= {"queue_depth", "processing", "active_request",
                                  "gpu_state", "completed", "failed"}

    def test_status_reflects_completion_count(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        q._completed_count = 7
        q._failed_count = 2
        s = q.status()
        assert s["completed"] == 7
        assert s["failed"] == 2

    @pytest.mark.asyncio
    async def test_status_active_request_is_none_when_idle(self, idle_scheduler):
        q = RequestQueue(scheduler=idle_scheduler)
        await q.submit(
            request_type=RequestType.BRAIN_SCAN,
            payload={"media_path": "/tmp/x.mp4"},
        )
        # After processing, active_request should be cleared
        s = q.status()
        assert s["active_request"] is None

    def test_status_gpu_state_reflects_scheduler(self, idle_scheduler):
        idle_scheduler.state = GPUState.GEMMA_ACTIVE
        q = RequestQueue(scheduler=idle_scheduler)
        assert q.status()["gpu_state"] == "gemma_active"


# ---------------------------------------------------------------------------
# get_queue() singleton
# ---------------------------------------------------------------------------

class TestGetQueueSingleton:
    def test_second_call_returns_same_instance(self, idle_scheduler):
        import cortex.request_queue as rq
        rq._queue = None

        with patch("cortex.request_queue.get_scheduler", return_value=idle_scheduler):
            q1 = get_queue()
            q2 = get_queue()

        assert q1 is q2

    def test_singleton_reset_after_fixture(self):
        import cortex.request_queue as rq
        # autouse fixture resets _queue = None before each test
        assert rq._queue is None

    def test_fallback_passed_on_first_call(self, idle_scheduler):
        import cortex.request_queue as rq
        rq._queue = None
        fb = FallbackProvider("http://example.com", "key123")

        with patch("cortex.request_queue.get_scheduler", return_value=idle_scheduler):
            q = get_queue(fallback=fb)

        assert q._fallback is fb

    def test_fallback_ignored_on_second_call(self, idle_scheduler):
        import cortex.request_queue as rq
        rq._queue = None

        with patch("cortex.request_queue.get_scheduler", return_value=idle_scheduler):
            q1 = get_queue()
            q2 = get_queue(fallback=FallbackProvider("http://x.com", "k"))

        assert q1 is q2
        assert q2._fallback is None


# ---------------------------------------------------------------------------
# FallbackProvider
# ---------------------------------------------------------------------------

class TestFallbackProvider:
    @pytest.mark.asyncio
    async def test_generate_happy_path(self, monkeypatch):
        fb = FallbackProvider("http://api.example.com", "token123", model="gpt-4o-mini")

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "fallback answer"}}]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fb.generate({"prompt": "hello", "system": "be helpful"})

        assert result == "fallback answer"

    @pytest.mark.asyncio
    async def test_generate_error_returns_fallback_message(self):
        fb = FallbackProvider("http://api.example.com", "bad-token")

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = AsyncMock(side_effect=Exception("connection refused"))

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await fb.generate({"prompt": "hi"})

        assert "Fallback unavailable" in result
        assert "connection refused" in result

    @pytest.mark.asyncio
    async def test_generate_includes_system_and_prompt(self):
        fb = FallbackProvider("http://api.example.com", "token")
        captured_json: list[dict] = []

        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "ok"}}]
        }

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        async def _capture_post(url, headers=None, json=None):
            captured_json.append(json or {})
            return mock_response

        mock_client.post = _capture_post

        with patch("httpx.AsyncClient", return_value=mock_client):
            await fb.generate({"prompt": "my question", "system": "my system"})

        body = captured_json[0]
        messages = body["messages"]
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == "my system"
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == "my question"
