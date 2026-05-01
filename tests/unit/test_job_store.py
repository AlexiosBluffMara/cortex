"""Unit tests for cortex.job_store — SQLite-backed job persistence."""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from cortex.job_store import JobStore


@pytest.fixture
def store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.db")


def _fake_path(job_id: str, tmp_path: Path, ext: str = ".mp4") -> Path:
    p = tmp_path / f"{job_id}{ext}"
    p.write_bytes(b"fake")
    return p


class TestCreate:
    def test_create_returns_dict(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("abc123", tmp_path)
        job = store.create("abc123", p)
        assert job["id"] == "abc123"
        assert job["status"] == "pending"

    def test_file_field_is_filename(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("xyz789", tmp_path)
        job = store.create("xyz789", p)
        assert job["file"] == p.name

    def test_created_at_is_recent(self, store: JobStore, tmp_path: Path) -> None:
        before = time.time()
        p = _fake_path("t1", tmp_path)
        store.create("t1", p)
        job = store.get("t1")
        assert job is not None
        assert before <= job["created_at"] <= time.time() + 1


class TestGet:
    def test_get_missing_returns_none(self, store: JobStore) -> None:
        assert store.get("nonexistent") is None

    def test_get_returns_job(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("g1", tmp_path)
        store.create("g1", p)
        job = store.get("g1")
        assert job is not None
        assert job["id"] == "g1"

    def test_get_file_path(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("g2", tmp_path)
        store.create("g2", p)
        retrieved = store.get_file_path("g2")
        assert retrieved == p

    def test_get_file_path_missing(self, store: JobStore) -> None:
        assert store.get_file_path("nope") is None


class TestUpdate:
    def test_status_update(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("u1", tmp_path)
        store.create("u1", p)
        store.update("u1", status="running")
        assert store.get("u1")["status"] == "running"

    def test_unknown_fields_ignored(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("u2", tmp_path)
        store.create("u2", p)
        store.update("u2", status="running", banana="yellow")
        assert store.get("u2")["status"] == "running"

    def test_empty_update_is_noop(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("u3", tmp_path)
        store.create("u3", p)
        store.update("u3")
        assert store.get("u3")["status"] == "pending"


class TestSetResult:
    def test_sets_done_status(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("r1", tmp_path)
        store.create("r1", p)
        store.set_result("r1", {"top_rois": ["Vis_lh"]})
        job = store.get("r1")
        assert job is not None
        assert job["status"] == "done"

    def test_result_fields_merged_into_dict(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("r2", tmp_path)
        store.create("r2", p)
        store.set_result("r2", {"peak_frame": 42, "duration_s": 50.0})
        job = store.get("r2")
        assert job is not None
        assert job["peak_frame"] == 42
        assert job["duration_s"] == 50.0

    def test_finished_at_set(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("r3", tmp_path)
        store.create("r3", p)
        before = time.time()
        store.set_result("r3", {})
        job = store.get("r3")
        assert job is not None
        assert job["finished_at"] >= before


class TestSetError:
    def test_sets_failed_status(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("e1", tmp_path)
        store.create("e1", p)
        store.set_error("e1", "GPU OOM")
        job = store.get("e1")
        assert job is not None
        assert job["status"] == "failed"
        assert job["error"] == "GPU OOM"


class TestResetStale:
    def test_running_reset_to_pending(self, store: JobStore, tmp_path: Path) -> None:
        for i in range(3):
            p = _fake_path(f"s{i}", tmp_path)
            store.create(f"s{i}", p)
            store.update(f"s{i}", status="running")
        n = store.reset_stale()
        assert n == 3
        for i in range(3):
            assert store.get(f"s{i}")["status"] == "pending"

    def test_done_jobs_not_reset(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("sd", tmp_path)
        store.create("sd", p)
        store.set_result("sd", {})
        n = store.reset_stale()
        assert n == 0
        assert store.get("sd")["status"] == "done"


class TestClaimNextPending:
    def test_returns_none_when_empty(self, store: JobStore) -> None:
        assert store.claim_next_pending() is None

    def test_claims_and_sets_queued(self, store: JobStore, tmp_path: Path) -> None:
        p = _fake_path("c1", tmp_path)
        store.create("c1", p)
        job = store.claim_next_pending()
        assert job is not None
        assert job["id"] == "c1"
        assert store.get("c1")["status"] == "queued"

    def test_fifo_within_same_priority(self, store: JobStore, tmp_path: Path) -> None:
        for name in ("first", "second", "third"):
            p = _fake_path(name, tmp_path)
            store.create(name, p)
            time.sleep(0.01)
        j1 = store.claim_next_pending()
        j2 = store.claim_next_pending()
        j3 = store.claim_next_pending()
        assert j1 is not None and j1["id"] == "first"
        assert j2 is not None and j2["id"] == "second"
        assert j3 is not None and j3["id"] == "third"

    def test_lower_priority_number_claimed_first(self, store: JobStore, tmp_path: Path) -> None:
        p_hi = _fake_path("high", tmp_path)
        p_lo = _fake_path("low", tmp_path)
        store.create("high", p_hi, priority=0)
        store.create("low", p_lo, priority=9)
        job = store.claim_next_pending()
        assert job is not None
        assert job["id"] == "high"


class TestListAll:
    def test_empty_returns_empty_list(self, store: JobStore) -> None:
        assert store.list_all() == []

    def test_ordered_newest_first(self, store: JobStore, tmp_path: Path) -> None:
        for name in ("a", "b", "c"):
            p = _fake_path(name, tmp_path)
            store.create(name, p)
            time.sleep(0.01)
        ids = [j["id"] for j in store.list_all()]
        assert ids == ["c", "b", "a"]


class TestQueueStats:
    def test_counts_by_status(self, store: JobStore, tmp_path: Path) -> None:
        for name in ("p1", "p2", "r1"):
            p = _fake_path(name, tmp_path)
            store.create(name, p)
        store.update("r1", status="running")
        stats = store.queue_stats()
        assert stats["pending"] >= 2
        assert stats["running"] == 1
        assert stats["total"] == 3


class TestListQueue:
    def test_excludes_done_and_failed(self, store: JobStore, tmp_path: Path) -> None:
        for name in ("q1", "q2", "q3"):
            p = _fake_path(name, tmp_path)
            store.create(name, p)
        store.set_result("q1", {})
        store.set_error("q2", "boom")
        queue = store.list_queue()
        ids = {j["id"] for j in queue}
        assert "q3" in ids
        assert "q1" not in ids
        assert "q2" not in ids
