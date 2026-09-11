"""kernel/orchestrator/queue.py — P2-C: the durable job queue (SQLite WAL).

Research/05 §5 pick: an own SQLite-backed queue (~300 lines, zero new daemons)
— desktop-friendly, survives restarts, auditable in the same engine family as
memory and policy audit. Job state lives in ONE row:

    queued ──claim──► running ──► done
                        │  ▲
              fail(retry)│  │lease expiry (crash recovery)
                        ▼  │
                     queued
                        │ fail(no retry) / cancel / attempts exhausted
                        ▼
                  failed / canceled

Checkpoint semantics: a job's `checkpoint` (JSON: done-step ids + outputs) is
written by the runner after EVERY completed step and survives crashes — a
re-claimed job resumes from it. `attempts` increments on every claim; when a
recovered job has no attempts left it fails instead of re-running.

Writes are guarded by a lock (loop + executor threads, as AuditLog) and each
claim re-checks its WHERE clause, so stale-lease recovery is race-safe. The
queue deliberately does NOT publish bus events — that is the runner's job.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id           TEXT PRIMARY KEY,
    kind         TEXT NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    payload      TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'queued',
    priority     INTEGER NOT NULL DEFAULT 0,
    attempts     INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    checkpoint   TEXT,
    result       TEXT,
    error        TEXT,
    leased_by    TEXT,
    lease_until  REAL,
    created_ts   REAL NOT NULL,
    updated_ts   REAL NOT NULL
)
"""
_TERMINAL = ("done", "failed", "canceled")


@dataclass(frozen=True)
class Job:
    """One durable job. `payload`/`checkpoint`/`result` are parsed JSON."""

    id: str
    kind: str
    payload: Mapping[str, Any]
    status: str = "queued"
    title: str = ""
    priority: int = 0
    attempts: int = 0
    max_attempts: int = 3
    checkpoint: Mapping[str, Any] | None = None
    result: Mapping[str, Any] | None = None
    error: str | None = None
    leased_by: str | None = None
    lease_until: float | None = None
    created_ts: float = 0.0
    updated_ts: float = 0.0

    @property
    def terminal(self) -> bool:
        return self.status in _TERMINAL

    def checkpoint_outputs(self) -> dict[str, Any]:
        """Outputs recorded by the last completed step (resume seed)."""
        if not self.checkpoint:
            return {}
        return dict(self.checkpoint.get("outputs") or {})

    def done_steps(self) -> set[str]:
        if not self.checkpoint:
            return set()
        return set(self.checkpoint.get("done") or ())


def _row_to_job(row: sqlite3.Row) -> Job:
    def _maybe_json(text: str | None) -> Any:
        return json.loads(text) if text else None

    return Job(
        id=row["id"], kind=row["kind"], title=row["title"],
        payload=json.loads(row["payload"]), status=row["status"],
        priority=row["priority"], attempts=row["attempts"],
        max_attempts=row["max_attempts"],
        checkpoint=_maybe_json(row["checkpoint"]),
        result=_maybe_json(row["result"]),
        error=row["error"], leased_by=row["leased_by"],
        lease_until=row["lease_until"],
        created_ts=row["created_ts"], updated_ts=row["updated_ts"],
    )


class JobQueue:
    """Durable job queue on SQLite (WAL). One instance per process; safe from
    multiple threads; safe across processes (claim rechecks its predicate)."""

    def __init__(self, path: str | Path | None = None) -> None:
        target = str(path) if path is not None else ":memory:"
        if target != ":memory:":
            Path(target).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(target, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.DatabaseError:
            pass  # :memory: has no WAL — harmless
        self._conn.execute(_SCHEMA)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_claim ON jobs(status, priority DESC, created_ts ASC, id)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_jobs_status_created ON jobs(status, created_ts DESC)"
        )
        self._conn.commit()
        self._lock = threading.RLock()

    # -- enqueue / introspection -------------------------------------------

    def enqueue(self, kind: str, payload: Mapping[str, Any], *, title: str = "",
                priority: int = 0, max_attempts: int = 3,
                job_id: str | None = None) -> str:
        if not kind:
            raise ValueError("job kind is required")
        now = time.time()
        jid = job_id or f"job-{uuid.uuid4().hex[:12]}"
        with self._lock:
            self._conn.execute(
                "INSERT INTO jobs (id, kind, title, payload, status, priority, "
                "max_attempts, created_ts, updated_ts) "
                "VALUES (?, ?, ?, ?, 'queued', ?, ?, ?, ?)",
                (jid, kind, title, json.dumps(dict(payload)), priority,
                 max(1, max_attempts), now, now))
            self._conn.commit()
        return jid

    def get(self, job_id: str) -> Job | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        return _row_to_job(row) if row is not None else None

    def list(self, status: str | None = None, limit: int = 50) -> list[Job]:
        with self._lock:
            if status is None:
                rows = self._conn.execute(
                    "SELECT * FROM jobs ORDER BY created_ts DESC, id LIMIT ?",
                    (limit,)).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT * FROM jobs WHERE status = ? "
                    "ORDER BY created_ts DESC, id LIMIT ?",
                    (status, limit)).fetchall()
        return [_row_to_job(row) for row in rows]

    # -- worker side -------------------------------------------------------

    def claim(self, worker: str, lease_s: float) -> Job | None:
        """Atomically claim the next queued job (or recover one whose lease
        expired — crash recovery). Keeps the checkpoint on recovery, bumps
        attempts, and fails the job instead of re-running when exhausted."""
        if lease_s <= 0:
            raise ValueError("lease_s must be > 0")
        while True:
            with self._lock:
                now = time.time()
                row = self._conn.execute(
                    "SELECT * FROM jobs WHERE status = 'queued' OR "
                    "(status = 'running' AND lease_until IS NOT NULL "
                    " AND lease_until <= ?) "
                    "ORDER BY priority DESC, created_ts ASC, id LIMIT 1",
                    (now,)).fetchone()
                if row is None:
                    return None
                if (row["status"] == "running"
                        and row["attempts"] + 1 > row["max_attempts"]):
                    self._conn.execute(
                        "UPDATE jobs SET status = 'failed', "
                        "error = 'attempts exhausted (crash recovery)', "
                        "updated_ts = ? WHERE id = ?", (now, row["id"]))
                    self._conn.commit()
                    continue
                cur = self._conn.execute(
                    "UPDATE jobs SET status = 'running', leased_by = ?, "
                    "lease_until = ?, attempts = attempts + 1, updated_ts = ? "
                    "WHERE id = ? AND (status = 'queued' OR "
                    "(status = 'running' AND lease_until IS NOT NULL "
                    " AND lease_until <= ?))",
                    (worker, now + lease_s, now, row["id"], now))
                self._conn.commit()
                if cur.rowcount != 1:
                    continue  # another process won the race — reselect
                claimed = self._conn.execute(
                    "SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone()
                return _row_to_job(claimed)

    def heartbeat(self, job_id: str, worker: str, lease_s: float) -> bool:
        """Extend the lease; only while this worker still owns a running job."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET lease_until = ?, updated_ts = ? "
                "WHERE id = ? AND status = 'running' AND leased_by = ?",
                (time.time() + lease_s, time.time(), job_id, worker))
            self._conn.commit()
            return cur.rowcount == 1

    def checkpoint(self, job_id: str, data: Mapping[str, Any], worker: str) -> bool:
        """Persist a mid-job checkpoint (owner-only; False once canceled)."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET checkpoint = ?, updated_ts = ? "
                "WHERE id = ? AND status = 'running' AND leased_by = ?",
                (json.dumps(dict(data)), time.time(), job_id, worker))
            self._conn.commit()
            return cur.rowcount == 1

    def complete(self, job_id: str, result: Mapping[str, Any], worker: str) -> bool:
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'done', result = ?, lease_until = NULL, "
                "updated_ts = ? WHERE id = ? AND status = 'running' "
                "AND leased_by = ?",
                (json.dumps(dict(result)), time.time(), job_id, worker))
            self._conn.commit()
            return cur.rowcount == 1

    def fail(self, job_id: str, error: str, *, worker: str,
             retry: bool) -> bool:
        """Record a failure. retry=True requeues (checkpoint kept) while
        attempts remain; otherwise the job lands in 'failed'."""
        if not error:
            raise ValueError("fail() requires a clean error message")
        with self._lock:
            now = time.time()
            row = self._conn.execute(
                "SELECT attempts, max_attempts FROM jobs WHERE id = ?",
                (job_id,)).fetchone()
            if row is None:
                return False
            requeue = retry and row["attempts"] < row["max_attempts"]
            status = "queued" if requeue else "failed"
            cur = self._conn.execute(
                "UPDATE jobs SET status = ?, error = ?, lease_until = NULL, "
                "updated_ts = ? WHERE id = ? AND status = 'running' "
                "AND leased_by = ?",
                (status, error, now, job_id, worker))
            self._conn.commit()
            return cur.rowcount == 1

    def cancel(self, job_id: str) -> bool:
        """Cancel a queued or running job (cooperative: workers notice at the
        next step boundary). Terminal jobs are untouched."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE jobs SET status = 'canceled', lease_until = NULL, "
                "updated_ts = ? WHERE id = ? AND status IN ('queued', 'running')",
                (time.time(), job_id))
            self._conn.commit()
            return cur.rowcount == 1


__all__ = ["Job", "JobQueue"]
