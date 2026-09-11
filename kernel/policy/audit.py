"""kernel/policy/audit.py — P1-E v0: SQLite audit trail for every policy decision.

Roadmap §3.4: "Safety is a subsystem, not a vibe" — every decision (allow, ask-yes,
ask-no, deny, unknown) lands here, timestamped, queryable, never silent.

- WAL journal when backed by a real file (":memory:" keeps whatever works).
- A lock guards writes: policy runs on the loop, executor threads may too.
- recent() returns newest-first dicts for dashboards and the eval harness.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

from kernel.types import RiskClass, ToolCall

_SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       REAL    NOT NULL,
    call_id  TEXT    NOT NULL,
    name     TEXT    NOT NULL,
    source   TEXT    NOT NULL,
    risk     TEXT    NOT NULL,
    decision TEXT    NOT NULL,
    ok       INTEGER,
    note     TEXT    NOT NULL DEFAULT ''
)
"""


class AuditLog:
    def __init__(self, path: str | Path | None = None) -> None:
        """path=None → in-memory (tests). Real deployments pass a file path
        (the kernel's config dir owns it, e.g. config/audit.db)."""
        target = str(path) if path is not None else ":memory:"
        self._conn = sqlite3.connect(target, check_same_thread=False)
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass  # :memory: has no WAL — harmless
        self._conn.execute(_SCHEMA)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_ts ON audit(ts DESC)"
        )
        self._conn.commit()
        self._lock = threading.Lock()

    def record(self, *, call: ToolCall, risk: RiskClass, decision: str,
               ok: bool | None = None, note: str = "") -> None:
        """Append one decision. Never raises to the caller (audit must not break the
        policy path); a failed write is logged and swallowed."""
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO audit (ts, call_id, name, source, risk, decision, ok, note) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (time.time(), call.id, call.name, call.source,
                     risk.value, decision,
                     None if ok is None else int(ok), note),
                )
                self._conn.commit()
        except sqlite3.Error:
            import logging
            logging.getLogger(__name__).exception("audit write failed")

    def recent(self, limit: int = 50) -> list[dict[str, Any]]:
        """Newest-first audit entries as dicts."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT ts, call_id, name, source, risk, decision, ok, note "
                "FROM audit ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        keys = ("ts", "call_id", "name", "source", "risk", "decision", "ok", "note")
        return [dict(zip(keys, row)) for row in rows]

    def count(self) -> int:
        with self._lock:
            return self._conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["AuditLog"]
