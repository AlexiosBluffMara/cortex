"""
Priority queue for GPU requests.

During TRIBE v2 inference, Gemma requests queue up and are processed
after TRIBE finishes. Optionally falls back to an external provider
(GitHub Copilot / 0x models) for chat requests when GPU is busy.

Queue priorities:
  0 = urgent (user actively waiting — WhatsApp/Discord direct message)
  5 = normal (WebUI gallery generation)
  9 = batch  (overnight processing, dataset generation)
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any

from cortex.gpu_scheduler import GPUScheduler, GPUState, get_scheduler
from cortex.logger import log


class RequestType(IntEnum):
    BRAIN_SCAN = 0
    GEMMA_CHAT = 1
    NARRATE = 2
    VISION_GATE = 3


@dataclass(order=True)
class PendingRequest:
    """A queued GPU request. Ordered by priority (lower = higher priority)."""
    priority: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12], compare=False)
    source: str = field(default="unknown", compare=False)
    request_type: RequestType = field(default=RequestType.GEMMA_CHAT, compare=False)
    payload: dict = field(default_factory=dict, compare=False)
    future: asyncio.Future = field(default=None, compare=False)
    created_at: float = field(default_factory=time.time, compare=False)

    @property
    def age_s(self) -> float:
        return time.time() - self.created_at


class FallbackProvider:
    """
    External LLM fallback when GPU is busy with TRIBE.
    Wraps GitHub Copilot / 0x models or any OpenAI-compatible endpoint.
    """

    def __init__(self, endpoint: str, api_key: str, model: str = "gpt-4o-mini"):
        self._endpoint = endpoint
        self._api_key = api_key
        self._model = model

    async def generate(self, payload: dict) -> str:
        import httpx
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                resp = await client.post(
                    f"{self._endpoint}/v1/chat/completions",
                    headers={"Authorization": f"Bearer {self._api_key}"},
                    json={
                        "model": self._model,
                        "messages": [
                            {"role": "system", "content": payload.get("system", "")},
                            {"role": "user", "content": payload.get("prompt", "")},
                        ],
                        "temperature": payload.get("temperature", 0.4),
                        "max_tokens": payload.get("num_predict", 400),
                    },
                )
                resp.raise_for_status()
                return resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:
            log.error("[fallback] External provider failed: %s", exc)
            return f"(Fallback unavailable: {exc})"


class RequestQueue:
    """
    Manages async request queuing with GPU-aware routing.

    When TRIBE is active:
      - Brain scan requests queue (TRIBE is already loaded)
      - Gemma chat/narrate requests either queue or fall back to external provider
      - Users get a status message: "Brain scan in progress (~5 min)..."

    When Gemma is active:
      - Gemma requests process immediately
      - Brain scan requests trigger a swap
    """

    MAX_QUEUE_SIZE = 50
    REQUEST_TIMEOUT_S = 600  # 10 minutes max wait

    def __init__(
        self,
        scheduler: GPUScheduler | None = None,
        fallback: FallbackProvider | None = None,
    ):
        self._scheduler = scheduler or get_scheduler()
        self._fallback = fallback
        self._queue: asyncio.PriorityQueue[PendingRequest] = asyncio.PriorityQueue(
            maxsize=self.MAX_QUEUE_SIZE
        )
        self._processing = False
        self._active_request: PendingRequest | None = None
        self._completed_count = 0
        self._failed_count = 0

    @property
    def queue_depth(self) -> int:
        return self._queue.qsize()

    @property
    def is_processing(self) -> bool:
        return self._processing

    def status(self) -> dict:
        return {
            "queue_depth": self.queue_depth,
            "processing": self._processing,
            "active_request": self._active_request.id if self._active_request else None,
            "gpu_state": self._scheduler.state.value,
            "completed": self._completed_count,
            "failed": self._failed_count,
        }

    async def submit(
        self,
        request_type: RequestType,
        payload: dict,
        priority: int = 5,
        source: str = "unknown",
    ) -> Any:
        """
        Submit a request and wait for the result.

        Returns the result directly (blocks until complete or timeout).
        Raises asyncio.TimeoutError if the request isn't processed in time.
        """
        loop = asyncio.get_event_loop()
        future = loop.create_future()

        request = PendingRequest(
            priority=priority,
            source=source,
            request_type=request_type,
            payload=payload,
            future=future,
        )

        # Fast path: if GPU is busy with TRIBE and this is a chat request,
        # try fallback immediately instead of queueing
        if (
            self._scheduler.state == GPUState.TRIBE_ACTIVE
            and request_type == RequestType.GEMMA_CHAT
            and self._fallback is not None
        ):
            log.info("[queue] GPU busy with TRIBE, routing %s to fallback", request.id)
            result = await self._fallback.generate(payload)
            return result

        # Enqueue
        try:
            self._queue.put_nowait(request)
        except asyncio.QueueFull:
            raise RuntimeError(
                f"Request queue full ({self.MAX_QUEUE_SIZE}). "
                "Try again later or increase queue size."
            )

        log.info("[queue] Enqueued %s (type=%s, priority=%d, source=%s, depth=%d)",
                 request.id, request_type.name, priority, source, self.queue_depth)

        # Start processing if not already running
        if not self._processing:
            asyncio.create_task(self._process_loop())

        # Wait for result with timeout
        try:
            return await asyncio.wait_for(future, timeout=self.REQUEST_TIMEOUT_S)
        except TimeoutError:
            self._failed_count += 1
            log.error("[queue] Request %s timed out after %ds",
                      request.id, self.REQUEST_TIMEOUT_S)
            raise

    async def _process_loop(self) -> None:
        """Process queued requests sequentially."""
        self._processing = True
        try:
            while not self._queue.empty():
                request = await self._queue.get()
                self._active_request = request

                try:
                    result = await self._execute(request)
                    if not request.future.done():
                        request.future.set_result(result)
                    self._completed_count += 1
                except Exception as exc:
                    if not request.future.done():
                        request.future.set_exception(exc)
                    self._failed_count += 1
                    log.error("[queue] Request %s failed: %s", request.id, exc)
                finally:
                    self._active_request = None
        finally:
            self._processing = False

    async def _execute(self, request: PendingRequest) -> Any:
        """Execute a single request against the GPU scheduler."""
        rt = request.request_type
        payload = request.payload

        if rt == RequestType.BRAIN_SCAN:
            return await self._scheduler.run_brain_scan(
                payload["media_path"],
                priority=request.priority,
            )

        elif rt == RequestType.GEMMA_CHAT:
            from cortex.model_manager import ModelTier
            tier = ModelTier(payload.get("tier", 0))
            return await self._scheduler.run_gemma_generate(
                prompt=payload["prompt"],
                system=payload.get("system", ""),
                tier=tier,
                temperature=payload.get("temperature", 0.4),
                num_predict=payload.get("num_predict", 400),
            )

        elif rt == RequestType.NARRATE:
            await self._scheduler.ensure_gemma()
            from cortex.ollama_client import generate
            tier_model_map = {0: "gemma4:e4b", 1: "gemma4:26b", 2: "gemma4:31b"}
            model = tier_model_map.get(payload.get("tier", 0), "gemma4:e4b")
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                None,
                lambda: generate(
                    payload["prompt"],
                    payload.get("system", ""),
                    model=model,
                    temperature=payload.get("temperature", 0.35),
                    num_predict=payload.get("num_predict", 600),
                ),
            )

        elif rt == RequestType.VISION_GATE:
            await self._scheduler.ensure_gemma()
            from pathlib import Path

            from cortex.gemma_vision import vision_gate
            keyframes = [Path(p) for p in payload["keyframe_paths"]]
            return await vision_gate(keyframes, self._scheduler._ollama_url)

        else:
            raise ValueError(f"Unknown request type: {rt}")


# -- Singleton --

_queue: RequestQueue | None = None


def get_queue(fallback: FallbackProvider | None = None) -> RequestQueue:
    global _queue
    if _queue is None:
        _queue = RequestQueue(fallback=fallback)
    return _queue
