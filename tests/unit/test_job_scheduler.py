"""Unit tests for cortex.job_scheduler — SLURM-style daemon scheduler."""
from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from cortex.job_scheduler import JobScheduler, register_ws, unregister_ws
from cortex.job_store import JobStore


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.db")


@pytest.fixture
def fake_path(tmp_path: Path) -> Path:
    p = tmp_path / "test.mp4"
    p.write_bytes(b"fake")
    return p


class TestWebSocketRegistry:
    def test_register_and_unregister(self) -> None:
        ws = MagicMock()
        register_ws("job1", ws)
        unregister_ws("job1", ws)

    def test_unregister_nonexistent_is_noop(self) -> None:
        ws = MagicMock()
        unregister_ws("nope", ws)

    def test_multiple_clients_per_job(self) -> None:
        ws1, ws2 = MagicMock(), MagicMock()
        register_ws("job2", ws1)
        register_ws("job2", ws2)
        unregister_ws("job2", ws1)
        unregister_ws("job2", ws2)


class TestJobSchedulerStatus:
    def test_status_reflects_store(self, store: JobStore, fake_path: Path) -> None:
        store.create("j1", fake_path)
        scheduler = JobScheduler(store=store)
        s = scheduler.status()
        assert "pending" in s
        assert "running" in s
        assert "worker_alive" in s

    def test_worker_not_alive_before_start(self, store: JobStore) -> None:
        scheduler = JobScheduler(store=store)
        assert scheduler.status()["worker_alive"] is False


class TestJobSchedulerStartStop:
    def test_start_spawns_daemon_thread(self, store: JobStore) -> None:
        scheduler = JobScheduler(store=store)
        loop = asyncio.new_event_loop()

        async def dummy(job_id: str, broadcast) -> None:
            pass

        scheduler.start(loop, dummy)
        try:
            assert scheduler.status()["worker_alive"] is True
        finally:
            scheduler.stop()
            loop.close()

    def test_stop_terminates_worker(self, store: JobStore) -> None:
        scheduler = JobScheduler(store=store)
        loop = asyncio.new_event_loop()

        async def dummy(job_id: str, broadcast) -> None:
            pass

        scheduler.start(loop, dummy)
        scheduler.stop()
        time.sleep(0.1)
        assert scheduler.status()["worker_alive"] is False
        loop.close()


class TestJobSchedulerDispatch:
    def test_pending_job_gets_processed(self, store: JobStore, fake_path: Path) -> None:
        """A pending job is claimed, processed, and the process_fn is called."""
        processed: list[str] = []
        done = threading.Event()

        async def process_fn(job_id: str, broadcast) -> None:
            processed.append(job_id)
            store.set_result(job_id, {"ok": True})
            done.set()

        store.create("dispatch1", fake_path)
        scheduler = JobScheduler(store=store)
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        scheduler.start(loop, process_fn)
        scheduler.notify()

        try:
            assert done.wait(timeout=5.0), "process_fn was never called"
            assert "dispatch1" in processed
            assert store.get("dispatch1")["status"] == "done"
        finally:
            scheduler.stop()
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_failed_job_status_set_to_failed(
        self, store: JobStore, fake_path: Path
    ) -> None:
        done = threading.Event()

        async def failing_fn(job_id: str, broadcast) -> None:
            done.set()
            raise RuntimeError("intentional test failure")

        store.create("fail1", fake_path)
        scheduler = JobScheduler(store=store)
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()

        scheduler.start(loop, failing_fn)
        scheduler.notify()

        try:
            assert done.wait(timeout=5.0)
            time.sleep(0.2)
            job = store.get("fail1")
            assert job is not None
            assert job["status"] == "failed"
            assert "intentional test failure" in (job.get("error") or "")
        finally:
            scheduler.stop()
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()

    def test_notify_wakes_worker_immediately(
        self, store: JobStore, fake_path: Path
    ) -> None:
        processed = threading.Event()

        async def fast_fn(job_id: str, broadcast) -> None:
            processed.set()

        scheduler = JobScheduler(store=store)
        loop = asyncio.new_event_loop()
        t = threading.Thread(target=loop.run_forever, daemon=True)
        t.start()
        scheduler.start(loop, fast_fn)

        store.create("notify1", fake_path)
        scheduler.notify()

        try:
            assert processed.wait(timeout=5.0), "Worker not woken by notify()"
        finally:
            scheduler.stop()
            loop.call_soon_threadsafe(loop.stop)
            t.join(timeout=2)
            loop.close()
