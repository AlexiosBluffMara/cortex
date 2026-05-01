"""SLURM-inspired job scheduler for Cortex.

Analogies:
  slurmctld  →  JobScheduler (decides what to run next)
  slurmd     →  _worker_loop (daemon thread, actually dispatches jobs)
  sbatch     →  store.create() + scheduler.notify()
  squeue     →  GET /api/queue
  sacct      →  GET /api/gallery

Single-GPU constraint: one job runs at a time, like a single-node partition.
Jobs are dispatched FIFO within priority class (priority 0 = urgent, 9 = batch).

Threading model
---------------
The daemon thread (_worker_loop) claims jobs from SQLite and submits each one
to the MAIN asyncio event loop via run_coroutine_threadsafe. It then blocks
with future.result() until the job finishes. This keeps GPU scheduler's
asyncio.Lock on the correct event loop and lets FastAPI keep serving requests
(the pipeline yields back to the main loop at every run_in_executor call).
"""
from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Awaitable, Callable

from cortex.job_store import JobStore, get_store
from cortex.logger import log

# WebSocket client registry — shared with server.py via register/unregister helpers.
# Maps job_id → list of active FastAPI WebSocket connections.
_ws_clients: dict[str, list] = {}


def register_ws(job_id: str, ws) -> None:
    _ws_clients.setdefault(job_id, []).append(ws)


def unregister_ws(job_id: str, ws) -> None:
    try:
        _ws_clients.get(job_id, []).remove(ws)
    except ValueError:
        pass


async def _ws_broadcast(job_id: str, message: dict) -> None:
    dead = []
    for ws in list(_ws_clients.get(job_id, [])):
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        unregister_ws(job_id, ws)


class JobScheduler:
    """Daemon scheduler: polls SQLite queue, dispatches jobs to the main event loop.

    Usage:
        scheduler = JobScheduler()
        scheduler.start(asyncio_main_loop, process_fn)

    process_fn is an async coroutine function with signature:
        async def process_fn(job_id: str, broadcast: Callable[[dict], Awaitable]) -> None
    """

    POLL_INTERVAL = 2.0

    def __init__(self, store: JobStore | None = None):
        self._store = store or get_store()
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._main_loop: asyncio.AbstractEventLoop | None = None
        self._process_fn: Callable | None = None
        self._current_job_id: str | None = None

    def start(
        self,
        main_loop: asyncio.AbstractEventLoop,
        process_fn: Callable[[str, Callable[[dict], Awaitable]], Awaitable],
    ) -> None:
        self._main_loop = main_loop
        self._process_fn = process_fn
        self._thread = threading.Thread(
            target=self._worker_loop, name="slurmd", daemon=True
        )
        self._thread.start()
        log.info("[scheduler] slurmd worker started")

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self) -> None:
        """Wake the worker immediately — call after enqueuing a new job."""
        self._wake.set()

    def status(self) -> dict:
        stats = self._store.queue_stats()
        stats["worker_alive"] = bool(self._thread and self._thread.is_alive())
        stats["current_job"] = self._current_job_id
        return stats

    # ── internal ──────────────────────────────────────────────────────────

    async def _run_on_main_loop(self, job_id: str) -> None:
        """Coroutine that runs on the main event loop for a single job.

        Creates an async broadcast callback and calls process_fn. Both the
        broadcast and process_fn run on the main loop, so GPUScheduler's
        asyncio.Lock works correctly.
        """
        async def broadcast(msg: dict) -> None:
            await _ws_broadcast(job_id, msg)

        await broadcast({"type": "status", "status": "running", "stage": "starting"})
        try:
            await self._process_fn(job_id, broadcast)  # type: ignore[misc]
        except Exception as exc:
            log.error("[scheduler] job %s failed: %s", job_id, exc, exc_info=True)
            self._store.set_error(job_id, str(exc))
            await broadcast({"type": "error", "message": str(exc)})

    def _worker_loop(self) -> None:
        log.info("[scheduler] worker loop running")
        while not self._stop.is_set():
            self._wake.clear()
            job = self._store.claim_next_pending()
            if job is None:
                self._wake.wait(timeout=self.POLL_INTERVAL)
                continue

            job_id = job["id"]
            self._current_job_id = job_id
            log.info("[scheduler] dispatching job %s", job_id)

            self._store.update(job_id, status="running", started_at=time.time())

            # Submit to main event loop and block this daemon thread until done.
            # This ensures GPU scheduler's asyncio.Lock stays on the correct loop.
            future = asyncio.run_coroutine_threadsafe(
                self._run_on_main_loop(job_id),
                self._main_loop,  # type: ignore[arg-type]
            )
            try:
                future.result()
            except Exception as exc:
                log.error("[scheduler] job %s unhandled: %s", job_id, exc)
                self._store.set_error(job_id, str(exc))
            finally:
                self._current_job_id = None

        log.info("[scheduler] worker loop exiting")


# ── singleton ──────────────────────────────────────────────────────────────

_instance: JobScheduler | None = None


def get_scheduler() -> JobScheduler:
    global _instance
    if _instance is None:
        _instance = JobScheduler()
    return _instance
