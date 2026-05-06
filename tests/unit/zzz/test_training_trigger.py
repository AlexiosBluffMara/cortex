"""Tests for cortex.training_trigger — decide local vs cloud retraining."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


def _run(coro):
    """Run a coroutine synchronously."""
    return asyncio.run(coro)


# training_trigger only uses stdlib + httpx, so no stubbing needed at import time.
import cortex.training_trigger as tt  # noqa: E402


# ---------------------------------------------------------------------------
# _local_utilization
# ---------------------------------------------------------------------------

class TestLocalUtilization:
    def _make_client(self, status=200, json_payload=None, side_effect=None):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = json_payload or {}

        mc = AsyncMock()
        if side_effect:
            mc.get = AsyncMock(side_effect=side_effect)
        else:
            mc.get = AsyncMock(return_value=resp)
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__  = AsyncMock(return_value=False)
        return mc

    def test_returns_json_on_200(self):
        payload = {"accepting": True, "vram": {"free_gb": 28.0}}
        mc = self._make_client(status=200, json_payload=payload)
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._local_utilization())
        assert result == payload

    def test_non_200_returns_default(self):
        mc = self._make_client(status=503)
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._local_utilization())
        assert result["accepting"] is False
        assert result["scheduler_state"] == "unreachable"

    def test_exception_returns_default(self):
        mc = self._make_client(side_effect=ConnectionError("no route"))
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._local_utilization())
        assert result["accepting"] is False

    def test_timeout_exception_returns_default(self):
        import httpx
        mc = self._make_client(side_effect=httpx.TimeoutException("timed out"))
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._local_utilization())
        assert result["accepting"] is False

    def test_200_response_has_scheduler_state_key_from_server(self):
        payload = {"accepting": True, "scheduler_state": "idle"}
        mc = self._make_client(status=200, json_payload=payload)
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._local_utilization())
        assert result["scheduler_state"] == "idle"


# ---------------------------------------------------------------------------
# _trigger_local
# ---------------------------------------------------------------------------

class TestTriggerLocal:
    def _make_client(self, status=200, job_id="job-abc", side_effect=None):
        resp = MagicMock()
        resp.status_code = status
        resp.json.return_value = {"job_id": job_id} if job_id else {}

        mc = AsyncMock()
        if side_effect:
            mc.post = AsyncMock(side_effect=side_effect)
        else:
            mc.post = AsyncMock(return_value=resp)
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__  = AsyncMock(return_value=False)
        return mc

    def test_200_returns_local_action(self):
        mc = self._make_client(status=200, job_id="job-abc-123")
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._trigger_local(["scan1", "scan2"]))
        assert result["action"] == "local"
        assert result["job_id"] == "job-abc-123"
        assert result["scans"] == 2

    def test_202_returns_local_action(self):
        mc = self._make_client(status=202, job_id="job-202")
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._trigger_local(["s1"]))
        assert result["action"] == "local"

    def test_500_returns_error(self):
        mc = self._make_client(status=500, job_id=None)
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._trigger_local(["s1"]))
        assert result["action"] == "error"

    def test_exception_returns_error_action_with_message(self):
        mc = self._make_client(side_effect=ConnectionError("refused"))
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._trigger_local(["s1"]))
        assert result["action"] == "error"
        assert "refused" in result.get("error", "")

    def test_missing_job_id_defaults_to_unknown(self):
        mc = self._make_client(status=200, job_id=None)
        mc.post.return_value.json.return_value = {}   # no job_id key
        with patch("httpx.AsyncClient", return_value=mc):
            result = _run(tt._trigger_local(["s1"]))
        assert result["action"] == "local"
        assert result["job_id"] == "unknown"


# ---------------------------------------------------------------------------
# _trigger_vertex
# ---------------------------------------------------------------------------

class TestTriggerVertex:
    def test_returns_cloud_action(self):
        assert tt._trigger_vertex(["s1", "s2"])["action"] == "cloud"

    def test_returns_job_id_string(self):
        result = tt._trigger_vertex(["s1"])
        assert "job_id" in result and isinstance(result["job_id"], str)

    def test_scans_count_correct(self):
        assert tt._trigger_vertex(["a", "b", "c", "d"])["scans"] == 4

    def test_job_id_contains_scan_prefix(self):
        result = tt._trigger_vertex(["abcdef123"])
        assert "abcdef" in result["job_id"]

    def test_empty_scan_ids(self):
        result = tt._trigger_vertex([])
        assert result["action"] == "cloud"
        assert "empty" in result["job_id"]

    def test_no_http_calls_made(self):
        # _trigger_vertex is a stub — must not call out to the network.
        with patch("httpx.AsyncClient") as mock_cls:
            tt._trigger_vertex(["s1"])
        mock_cls.assert_not_called()


# ---------------------------------------------------------------------------
# check_and_trigger_training
# ---------------------------------------------------------------------------

class TestCheckAndTriggerTraining:
    def test_fewer_scans_than_threshold_returns_wait(self):
        result = _run(tt.check_and_trigger_training(["s1", "s2"], threshold=10))
        assert result["action"] == "wait"
        assert result["count"] == 2
        assert result["threshold"] == 10

    def test_zero_scans_returns_wait(self):
        result = _run(tt.check_and_trigger_training([], threshold=5))
        assert result["action"] == "wait"

    def test_5090_accepting_and_enough_vram_triggers_local(self):
        util = {"accepting": True, "vram": {"free_gb": 28.0, "tribe_active": False}}
        local_result = {"action": "local", "job_id": "local-job", "scans": 5}
        scan_ids = [f"s{i}" for i in range(5)]

        with patch.object(tt, "_local_utilization", AsyncMock(return_value=util)), \
             patch.object(tt, "_trigger_local", AsyncMock(return_value=local_result)):
            result = _run(tt.check_and_trigger_training(scan_ids, threshold=5))

        assert result["action"] == "local"

    def test_5090_not_accepting_triggers_vertex(self):
        util = {"accepting": False, "vram": {"free_gb": 5.0, "tribe_active": False}}
        scan_ids = [f"s{i}" for i in range(5)]

        with patch.object(tt, "_local_utilization", AsyncMock(return_value=util)):
            result = _run(tt.check_and_trigger_training(scan_ids, threshold=5))

        assert result["action"] == "cloud"

    def test_tribe_active_triggers_vertex_even_if_accepting(self):
        util = {"accepting": True, "vram": {"free_gb": 28.0, "tribe_active": True}}
        scan_ids = [f"s{i}" for i in range(5)]

        with patch.object(tt, "_local_utilization", AsyncMock(return_value=util)):
            result = _run(tt.check_and_trigger_training(scan_ids, threshold=5))

        assert result["action"] == "cloud"

    def test_insufficient_free_vram_triggers_vertex(self):
        util = {"accepting": True, "vram": {"free_gb": 2.0, "tribe_active": False}}
        scan_ids = [f"s{i}" for i in range(5)]

        with patch.object(tt, "_local_utilization", AsyncMock(return_value=util)):
            result = _run(tt.check_and_trigger_training(scan_ids, threshold=5))

        assert result["action"] == "cloud"

    def test_wait_result_contains_threshold(self):
        result = _run(tt.check_and_trigger_training(["a"], threshold=42))
        assert result["threshold"] == 42

    def test_exactly_threshold_count_does_not_wait(self):
        util = {"accepting": True, "vram": {"free_gb": 28.0, "tribe_active": False}}
        local_result = {"action": "local", "job_id": "j", "scans": 5}
        scan_ids = [f"s{i}" for i in range(5)]

        with patch.object(tt, "_local_utilization", AsyncMock(return_value=util)), \
             patch.object(tt, "_trigger_local", AsyncMock(return_value=local_result)):
            result = _run(tt.check_and_trigger_training(scan_ids, threshold=5))

        assert result["action"] != "wait"


# ---------------------------------------------------------------------------
# sweep_pending_scans
# ---------------------------------------------------------------------------

def _make_doc(doc_id: str):
    doc = MagicMock()
    doc.id = doc_id
    return doc


def _make_db(docs, track_updates: list | None = None):
    """Build a minimal async-iterable Firestore mock."""
    db = MagicMock()
    query = MagicMock()

    async def _stream():
        for d in docs:
            yield d

    query.stream  = _stream
    query.where   = MagicMock(return_value=query)
    query.limit   = MagicMock(return_value=query)

    async def _update(data):
        if track_updates is not None:
            track_updates.append(data)

    doc_ref = AsyncMock()
    doc_ref.update = _update

    coll = MagicMock()
    coll.where    = MagicMock(return_value=query)
    coll.document = MagicMock(return_value=doc_ref)

    db.collection = MagicMock(return_value=coll)
    return db


class TestSweepPendingScans:
    def test_no_docs_returns_wait(self):
        db = _make_db([])
        result = _run(tt.sweep_pending_scans(db))
        assert result["action"] == "wait"
        assert result["count"] == 0

    def test_below_threshold_returns_wait(self):
        docs = [_make_doc(f"s{i}") for i in range(3)]
        db   = _make_db(docs)
        with patch.object(tt, "TRAINING_THRESHOLD", 100):
            result = _run(tt.sweep_pending_scans(db))
        assert result["action"] == "wait"

    def test_above_threshold_triggers(self):
        docs  = [_make_doc(f"s{i}") for i in range(5)]
        db    = _make_db(docs)
        cloud = {"action": "cloud", "job_id": "v-job-1"}

        with patch.object(tt, "TRAINING_THRESHOLD", 5), \
             patch.object(tt, "check_and_trigger_training", AsyncMock(return_value=cloud)):
            result = _run(tt.sweep_pending_scans(db))

        assert result["action"] == "cloud"

    def test_scans_marked_trained_on_local_success(self):
        updates: list = []
        docs = [_make_doc(f"s{i}") for i in range(5)]
        db   = _make_db(docs, track_updates=updates)
        trig = {"action": "local", "job_id": "local-job-99"}

        with patch.object(tt, "TRAINING_THRESHOLD", 5), \
             patch.object(tt, "check_and_trigger_training", AsyncMock(return_value=trig)):
            _run(tt.sweep_pending_scans(db))

        assert len(updates) == 5
        for data in updates:
            assert data["trained"] is True
            assert data["training_job"] == "local-job-99"

    def test_scans_marked_trained_on_cloud_success(self):
        updates: list = []
        docs = [_make_doc(f"s{i}") for i in range(3)]
        db   = _make_db(docs, track_updates=updates)
        trig = {"action": "cloud", "job_id": "cloud-job-42"}

        with patch.object(tt, "TRAINING_THRESHOLD", 3), \
             patch.object(tt, "check_and_trigger_training", AsyncMock(return_value=trig)):
            _run(tt.sweep_pending_scans(db))

        assert len(updates) == 3
        for data in updates:
            assert data["training_job"] == "cloud-job-42"

    def test_wait_action_does_not_mark_scans(self):
        updates: list = []
        docs = [_make_doc(f"s{i}") for i in range(3)]
        db   = _make_db(docs, track_updates=updates)

        with patch.object(tt, "TRAINING_THRESHOLD", 100):
            _run(tt.sweep_pending_scans(db))

        assert len(updates) == 0

    def test_sweep_passes_correct_scan_ids_to_trigger(self):
        docs = [_make_doc("scan_A"), _make_doc("scan_B")]
        db   = _make_db(docs)
        collected: list[list[str]] = []

        async def _fake(scan_ids, *, threshold=tt.TRAINING_THRESHOLD):
            collected.append(list(scan_ids))
            return {"action": "wait", "count": len(scan_ids), "threshold": threshold}

        with patch.object(tt, "check_and_trigger_training", _fake):
            _run(tt.sweep_pending_scans(db))

        assert collected[0] == ["scan_A", "scan_B"]
