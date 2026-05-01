"""Persistent job store — SQLite-backed, analogous to SLURM's accounting database.

Jobs survive server restarts. Thread-safe via WAL mode + busy_timeout.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

_DB: JobStore | None = None

_DDL = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    file_path   TEXT NOT NULL,
    file_name   TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    priority    INTEGER NOT NULL DEFAULT 5,
    created_at  REAL NOT NULL,
    started_at  REAL,
    finished_at REAL,
    result_json TEXT,
    error       TEXT
);
CREATE INDEX IF NOT EXISTS jobs_status_idx ON jobs(status, created_at);
"""


class JobStore:
    def __init__(self, db_path: Path):
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._path = str(db_path)
        with self._conn() as conn:
            conn.executescript(_DDL)

    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        return conn

    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        d = dict(row)
        raw = d.pop("result_json", None)
        if raw:
            try:
                d.update(json.loads(raw))
            except Exception:
                pass
        d.pop("file_path", None)
        d["file"] = d.pop("file_name", d.get("id", ""))
        return d

    # ── write ──────────────────────────────────────────────────────────────

    def create(self, job_id: str, file_path: Path, priority: int = 5) -> dict:
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO jobs (id, file_path, file_name, status, priority, created_at)
                   VALUES (?, ?, ?, 'pending', ?, ?)""",
                (job_id, str(file_path), file_path.name, priority, time.time()),
            )
        return self.get(job_id)  # type: ignore[return-value]

    def update(self, job_id: str, **kwargs: Any) -> None:
        allowed = {"status", "started_at", "finished_at", "error"}
        sets = {k: v for k, v in kwargs.items() if k in allowed}
        if not sets:
            return
        sql = "UPDATE jobs SET " + ", ".join(f"{k}=?" for k in sets) + " WHERE id=?"
        with self._conn() as conn:
            conn.execute(sql, [*sets.values(), job_id])

    def set_result(self, job_id: str, result: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET result_json=?, status='done', finished_at=? WHERE id=?",
                (json.dumps(result), time.time(), job_id),
            )

    def set_error(self, job_id: str, error: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE jobs SET error=?, status='failed', finished_at=? WHERE id=?",
                (error, time.time(), job_id),
            )

    def reset_stale(self) -> int:
        """Reset jobs stuck in running/queued back to pending (recovery after crash)."""
        with self._conn() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='pending', started_at=NULL "
                "WHERE status IN ('running', 'queued')"
            )
            return cur.rowcount

    # ── read ───────────────────────────────────────────────────────────────

    def get(self, job_id: str) -> dict | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return self._row_to_dict(row) if row else None

    def get_file_path(self, job_id: str) -> Path | None:
        with self._conn() as conn:
            row = conn.execute("SELECT file_path FROM jobs WHERE id=?", (job_id,)).fetchone()
        return Path(row["file_path"]) if row else None

    def list_all(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_dict(r) for r in rows]

    def claim_next_pending(self) -> dict | None:
        """Atomically claim the next pending job (lowest priority, then FIFO).
        Returns None if queue is empty. Sets status → 'queued'."""
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM jobs WHERE status='pending' "
                "ORDER BY priority ASC, created_at ASC LIMIT 1"
            ).fetchone()
            if row:
                conn.execute("UPDATE jobs SET status='queued' WHERE id=?", (row["id"],))
        return self._row_to_dict(row) if row else None

    def queue_stats(self) -> dict:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS cnt FROM jobs GROUP BY status"
            ).fetchall()
        counts = {r["status"]: r["cnt"] for r in rows}
        return {
            "pending":  counts.get("pending", 0) + counts.get("queued", 0),
            "running":  counts.get("running", 0),
            "done":     counts.get("done", 0),
            "failed":   counts.get("failed", 0),
            "total":    sum(counts.values()),
        }

    def list_queue(self) -> list[dict]:
        """Pending + running jobs ordered by priority then submit time (like squeue)."""
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE status IN ('pending','queued','running') "
                "ORDER BY priority ASC, created_at ASC"
            ).fetchall()
        return [self._row_to_dict(r) for r in rows]


# ── singleton ──────────────────────────────────────────────────────────────

def init_store(db_path: Path) -> JobStore:
    global _DB
    _DB = JobStore(db_path)
    return _DB


def get_store() -> JobStore:
    if _DB is None:
        raise RuntimeError("JobStore not initialized — call init_store() first")
    return _DB
