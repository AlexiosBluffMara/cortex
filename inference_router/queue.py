"""Single-slot asyncio queue for serializing Ollama requests on the 5090.

The RTX 5090 has 32 GB of VRAM but it is one GPU — running multiple Gemma
generations through Ollama in parallel just thrashes KV cache. So we funnel
every Ollama-bound request through a single global queue with one worker.

Cloudflare Workers AI and HF Inference fall through this queue entirely:
those backends are rate-limited externally, so concurrent requests are fine.
"""
from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

T = TypeVar("T")


class OllamaSerializer:
    """Run coroutines one-at-a-time. Unbounded queue — slow is fine, paid is not."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._inflight = 0

    @property
    def inflight(self) -> int:
        return self._inflight

    async def run(self, fn: Callable[[], Awaitable[T]]) -> T:
        async with self._lock:
            self._inflight += 1
            try:
                return await fn()
            finally:
                self._inflight -= 1


_GLOBAL_SERIALIZER: OllamaSerializer | None = None


def get_serializer() -> OllamaSerializer:
    global _GLOBAL_SERIALIZER
    if _GLOBAL_SERIALIZER is None:
        _GLOBAL_SERIALIZER = OllamaSerializer()
    return _GLOBAL_SERIALIZER


def reset_serializer() -> None:
    """Used by tests to ensure independent state per test."""
    global _GLOBAL_SERIALIZER
    _GLOBAL_SERIALIZER = None


async def serialize_ollama(fn: Callable[[], Awaitable[Any]]) -> Any:
    return await get_serializer().run(fn)
