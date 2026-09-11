"""kernel/memory/engine.py — P1-D: the memory engine v0 (research/04 §10).

SQLite (WAL) with the five §10 stores — semantic_facts, episodes, procedures,
people, preferences — plus two recall legs fused by Reciprocal Rank Fusion:
FTS5 (keyword) and vector cosine over stored BLOBs. The v0 vector leg is
brute-force cosine (personal-scale data); swapping in the sqlite-vec ANN index
later changes only this file's retrieval internals, not the schema or API —
vectors are already stored dimension-tagged per embedder.

P3-A additions (research/04 §6.6): tombstones, not deletes — consolidation
retires facts by marking them, keeping row/vector/FTS content for audit and
undo; every read path filters them out. `update_fact` keeps the bi-temporal
shape (valid_from preserved, known_at bumped only when content changes).

Thread model: one connection guarded by an RLock (mirrors the P1-E AuditLog);
every method is atomic. No method ever raises to a caller for "empty" results —
they return lists.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.memory.embedders import Embedder, HashingEmbedder, make_embedder

_SCHEMA = """
CREATE TABLE IF NOT EXISTS semantic_facts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity TEXT NOT NULL DEFAULT '',
    topic TEXT NOT NULL DEFAULT '',
    content TEXT NOT NULL,
    importance REAL NOT NULL DEFAULT 0.5,
    valid_from REAL,
    known_at REAL NOT NULL,
    expires_at REAL,
    source_ref TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    tombstone_reason TEXT,
    tombstoned_at REAL,
    last_decayed_at REAL
);
CREATE TABLE IF NOT EXISTS fact_vectors (
    fact_id INTEGER PRIMARY KEY REFERENCES semantic_facts(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS facts_fts USING fts5(content, entity, topic);
CREATE TABLE IF NOT EXISTS episodes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at REAL NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    source_ref TEXT
);
CREATE INDEX IF NOT EXISTS idx_episodes_started ON episodes(started_at DESC);
CREATE TABLE IF NOT EXISTS procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    steps_json TEXT NOT NULL DEFAULT '[]',
    summary TEXT NOT NULL DEFAULT '',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0,
    last_failure TEXT,
    updated_at REAL
);
CREATE TABLE IF NOT EXISTS procedure_vectors (
    procedure_id INTEGER PRIMARY KEY REFERENCES procedures(id) ON DELETE CASCADE,
    dim INTEGER NOT NULL,
    embedding BLOB NOT NULL
);
CREATE TABLE IF NOT EXISTS people (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    aliases_json TEXT NOT NULL DEFAULT '[]',
    notes TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS preferences (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Columns added after P1-D shipped — pre-existing DBs get them via ALTER TABLE
# in _ensure_columns (idempotent; fresh DBs already have them from _SCHEMA).
_P3A_COLUMNS: tuple[tuple[str, str], ...] = (
    ("status", "TEXT NOT NULL DEFAULT 'active'"),
    ("tombstone_reason", "TEXT"),
    ("tombstoned_at", "REAL"),
    ("last_decayed_at", "REAL"),
)
_P3C_PROCEDURE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("summary", "TEXT NOT NULL DEFAULT ''"),
    ("last_failure", "TEXT"),
    ("updated_at", "REAL"),
)

_RRF_K = 60  # standard RRF constant


@dataclass(frozen=True)
class SearchHit:
    """One recalled fact. `score` is the fused RRF score (0.0 for page walks)."""

    id: int
    content: str
    entity: str
    topic: str
    importance: float
    known_at: float
    source_ref: str | None
    score: float = 0.0


@dataclass(frozen=True)
class ProcedureRecord:
    """One replayable skill (research/04 §7): the tool-call script that
    worked, its summary (the retrieval key), and its outcome tally."""

    id: int
    name: str
    steps: tuple[dict[str, Any], ...]
    summary: str
    success_count: int = 0
    fail_count: int = 0
    last_failure: str | None = None
    updated_at: float = 0.0


@dataclass(frozen=True)
class ProcedureHit:
    """A procedure recalled by similarity; `score` is summary cosine."""

    record: ProcedureRecord
    score: float = 0.0


_FTS_TOKEN = re.compile(r"[a-z0-9]+")


def _fts_safe_query(text: str) -> str:
    """Build an FTS5 query from user text: each alphanumeric run becomes a
    PREFIX term (`plant*` matches plant/plants, `allerg*` matches allergic),
    joined by OR — natural questions must not require EVERY token to match;
    BM25 ranks the rows sharing the most/rarest terms. Prefix terms are
    [a-z0-9]+ by construction (FTS5 operators are uppercase, so lowercase
    tokens can't be syntax), making injection impossible."""
    return " OR ".join(f"{run}*" for run in _FTS_TOKEN.findall(text.lower()))


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = (sum(x * x for x in a) or 1.0) ** 0.5
    nb = (sum(y * y for y in b) or 1.0) ** 0.5
    return dot / (na * nb)


class MemoryEngine:
    """Long-term memory: remember, recall (FTS5 ⊕ vector via RRF), page, forget."""

    def __init__(
        self,
        path: str | Path,
        embedder: Embedder | None = None,
        *,
        embedder_name: str | None = None,
    ) -> None:
        if embedder is not None and embedder_name is not None:
            raise ValueError(
                "pass either embedder= or embedder_name=, not both")
        if embedder is not None:
            resolved = embedder
        elif embedder_name is not None:
            # Phase A4 config-key seam: "hashing" | "bge-m3" via make_embedder
            # (bge-m3 raises EmbedderUnavailable when fastembed is absent).
            resolved = make_embedder(embedder_name)
        else:
            resolved = HashingEmbedder()
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._ensure_columns()
        self._lock = threading.RLock()
        self._embedder = resolved

    def _ensure_columns(self) -> None:
        """Add post-P1-D columns to DBs created before P3-A/P3-C (idempotent)."""
        self._add_missing_columns("semantic_facts", _P3A_COLUMNS)
        self._add_missing_columns("procedures", _P3C_PROCEDURE_COLUMNS)
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_status_known ON semantic_facts(status, known_at DESC)"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_facts_importance ON semantic_facts(importance DESC, known_at DESC)"
        )
        self._conn.commit()

    def _add_missing_columns(
        self,
        table: str,
        columns: tuple[tuple[str, str], ...],
    ) -> None:
        existing = {row[1] for row in self._conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns:
            if name not in existing:
                self._conn.execute(
                    f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")

    # ------------------------------------------------------------ write ----

    def remember(
        self,
        content: str,
        *,
        entity: str = "",
        topic: str = "",
        importance: float = 0.5,
        source_ref: str | None = None,
        valid_from: float | None = None,
        expires_at: float | None = None,
        known_at: float | None = None,
    ) -> int:
        """Store one fact; returns its id. The vector leg indexes it at write
        time (write-time embedding, research/04 §10 write path, v0 flavor).
        `known_at` defaults to now — the eval harness backdates it to seed
        sessions from the past (bi-temporal backfill)."""
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        now = time.time() if known_at is None else known_at
        vector = self._embedder.embed(content)
        blob = struct.pack(f"<{len(vector)}f", *vector)
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO semantic_facts (entity, topic, content, importance,"
                " valid_from, known_at, expires_at, source_ref)"
                " VALUES (?,?,?,?,?,?,?,?)",
                (entity, topic, content, importance, valid_from, now, expires_at,
                 source_ref),
            )
            fact_id = int(cur.lastrowid or 0)
            self._conn.execute(
                "INSERT INTO fact_vectors (fact_id, dim, embedding) VALUES (?,?,?)",
                (fact_id, len(vector), blob),
            )
            self._conn.execute(
                "INSERT INTO facts_fts (rowid, content, entity, topic) VALUES (?,?,?,?)",
                (fact_id, content, entity, topic),
            )
            self._conn.commit()
        return fact_id

    def forget(self, fact_id: int) -> bool:
        """Delete one fact everywhere (row, vector, FTS). True if it existed."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM semantic_facts WHERE id = ?", (fact_id,)
            )
            existed = cur.rowcount > 0
            self._conn.execute(
                "DELETE FROM fact_vectors WHERE fact_id = ?", (fact_id,)
            )
            self._conn.execute(
                "DELETE FROM facts_fts WHERE rowid = ?", (fact_id,)
            )
            self._conn.commit()
        return existed

    def tombstone(self, fact_id: int, reason: str) -> bool:
        """Retire a fact without destroying it (research/04 §6.6): every read
        path filters it out, but row/vector/FTS content stays for audit and
        `restore`. Only active facts can be tombstoned. True if it retired."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE semantic_facts SET status = 'tombstoned',"
                " tombstone_reason = ?, tombstoned_at = ?"
                " WHERE id = ? AND status = 'active'",
                (reason, time.time(), fact_id),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def restore(self, fact_id: int) -> bool:
        """Undo a tombstone (audit + undo, §6.6). True if it came back."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE semantic_facts SET status = 'active',"
                " tombstone_reason = NULL, tombstoned_at = NULL"
                " WHERE id = ? AND status = 'tombstoned'",
                (fact_id,),
            )
            self._conn.commit()
            return cur.rowcount > 0

    def update_fact(
        self,
        fact_id: int,
        *,
        content: str | None = None,
        entity: str | None = None,
        topic: str | None = None,
        importance: float | None = None,
    ) -> bool:
        """Judge-driven UPDATE (mem0-style): replace fields on an active fact.
        Content changes re-embed and refresh FTS, and bump `known_at` (when we
        last learned it) while `valid_from` stays — bi-temporal, §1 Graphiti
        schema idea. False if the fact is missing or tombstoned."""
        with self._lock:
            row = self._conn.execute(
                "SELECT content, entity, topic, importance FROM semantic_facts"
                " WHERE id = ? AND status = 'active'",
                (fact_id,),
            ).fetchone()
            if row is None:
                return False
            old_content, old_entity, old_topic, old_importance = row
            new_content = old_content if content is None else content
            if not new_content or not new_content.strip():
                raise ValueError("content must be non-empty")
            content_changed = new_content != old_content
            known_at = time.time() if content_changed else None
            self._conn.execute(
                "UPDATE semantic_facts SET content = ?, entity = ?, topic = ?,"
                " importance = ?"
                + (", known_at = ?" if known_at is not None else "")
                + " WHERE id = ?",
                (
                    new_content,
                    old_entity if entity is None else entity,
                    old_topic if topic is None else topic,
                    old_importance if importance is None else importance,
                    *((known_at,) if known_at is not None else ()),
                    fact_id,
                ),
            )
            if content_changed:
                vector = self._embedder.embed(new_content)
                blob = struct.pack(f"<{len(vector)}f", *vector)
                # delete + reinsert, not UPDATE: rows that predate the indexes
                # (pre-P3-A DBs, raw migrations) may have no vector/FTS row at
                # all — this self-heals them
                self._conn.execute(
                    "DELETE FROM fact_vectors WHERE fact_id = ?", (fact_id,))
                self._conn.execute(
                    "INSERT INTO fact_vectors (fact_id, dim, embedding)"
                    " VALUES (?,?,?)",
                    (fact_id, len(vector), blob),
                )
                self._conn.execute(
                    "DELETE FROM facts_fts WHERE rowid = ?", (fact_id,))
                self._conn.execute(
                    "INSERT INTO facts_fts (rowid, content, entity, topic)"
                    " VALUES (?,?,?,?)",
                    (fact_id, new_content,
                     old_entity if entity is None else entity,
                     old_topic if topic is None else topic),
                )
            self._conn.commit()
        return True

    def set_importance(self, fact_id: int, importance: float, *,
                       last_decayed_at: float | None = None) -> bool:
        """Consolidation-time decay writes importance WITHOUT bumping known_at
        (decay must not make a fact look fresh). Clamps to [0.0, 1.0]."""
        clamped = min(1.0, max(0.0, float(importance)))
        with self._lock:
            if last_decayed_at is not None:
                cur = self._conn.execute(
                    "UPDATE semantic_facts SET importance = ?, last_decayed_at = ?"
                    " WHERE id = ? AND status = 'active'",
                    (clamped, float(last_decayed_at), fact_id),
                )
            else:
                cur = self._conn.execute(
                    "UPDATE semantic_facts SET importance = ?"
                    " WHERE id = ? AND status = 'active'",
                    (clamped, fact_id),
                )
            self._conn.commit()
            return cur.rowcount > 0

    # ------------------------------------------------------------ read ----

    def search(self, query: str, *, k: int = 8) -> list[SearchHit]:
        """Recall: RRF fusion of the FTS5 leg and the vector-cosine leg."""
        if not query or not query.strip():
            return []
        with self._lock:
            fts_ids = self._fts_leg(query, k)
            vec_ids = self._vector_leg(query, k)
            fused = _reciprocal_rank_fusion([fts_ids, vec_ids])[:k]
            hits: list[SearchHit] = []
            for rank, (fact_id, score) in enumerate(fused, 1):
                row = self._conn.execute(
                    "SELECT id, content, entity, topic, importance, known_at,"
                    " source_ref FROM semantic_facts WHERE id = ?"
                    " AND status = 'active'",
                    (fact_id,),
                ).fetchone()
                if row is not None:
                    hits.append(SearchHit(
                        id=row[0], content=row[1], entity=row[2], topic=row[3],
                        importance=row[4], known_at=row[5], source_ref=row[6],
                        score=score,
                    ))
        hits.sort(key=lambda h: -h.score)
        return hits

    def page(
        self,
        *,
        entity: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[SearchHit]:
        """Chronological walk (newest first) — the memory_page tool's listing."""
        with self._lock:
            if entity is None:
                rows = self._conn.execute(
                    "SELECT id, content, entity, topic, importance, known_at,"
                    " source_ref FROM semantic_facts WHERE status = 'active'"
                    " ORDER BY known_at DESC, id DESC LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, content, entity, topic, importance, known_at,"
                    " source_ref FROM semantic_facts WHERE entity = ?"
                    " AND status = 'active'"
                    " ORDER BY known_at DESC, id DESC LIMIT ? OFFSET ?",
                    (entity, limit, offset),
                ).fetchall()
        return [SearchHit(id=r[0], content=r[1], entity=r[2], topic=r[3],
                          importance=r[4], known_at=r[5], source_ref=r[6])
                for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM semantic_facts WHERE status = 'active'"
            ).fetchone()[0])

    def get_fact(self, fact_id: int) -> SearchHit | None:
        """One active fact by id (judge UPDATE/DELETE validation), else None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT id, content, entity, topic, importance, known_at,"
                " source_ref FROM semantic_facts WHERE id = ?"
                " AND status = 'active'",
                (fact_id,),
            ).fetchone()
        if row is None:
            return None
        return SearchHit(id=row[0], content=row[1], entity=row[2], topic=row[3],
                         importance=row[4], known_at=row[5], source_ref=row[6])

    def active_facts(self) -> list[dict[str, Any]]:
        """Every active fact incl. decay/expiry fields — the consolidation
        job's working set (research/04 §6). Values included: caller's care."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, entity, topic, content, importance, known_at,"
                " expires_at, source_ref, last_decayed_at FROM semantic_facts"
                " WHERE status = 'active' ORDER BY known_at DESC, id DESC"
            ).fetchall()
        return [dict(zip(
            ("id", "entity", "topic", "content", "importance", "known_at",
             "expires_at", "source_ref", "last_decayed_at"), r)) for r in rows]

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # ----------------------------------------------------------- legs -----

    def _fts_leg(self, query: str, k: int) -> list[int]:
        safe = _fts_safe_query(query)
        if not safe:
            return []
        try:
            rows = self._conn.execute(
                "SELECT rowid FROM facts_fts WHERE facts_fts MATCH ?"
                " ORDER BY rank LIMIT ?",
                (safe, k),
            ).fetchall()
        except sqlite3.OperationalError:
            return []
        return [int(r[0]) for r in rows]

    def _vector_leg(self, query: str, k: int) -> list[int]:
        qvec = self._embedder.embed(query)
        rows = self._conn.execute(
            "SELECT fv.fact_id, fv.dim, fv.embedding FROM fact_vectors fv"
            " JOIN semantic_facts sf ON sf.id = fv.fact_id"
            " WHERE sf.status = 'active'"
        ).fetchall()
        scored: list[tuple[float, int]] = []
        for fact_id, dim, blob in rows:
            if dim != len(qvec):
                continue  # foreign embedder dimension — skip, not crash
            stored = list(struct.unpack(f"<{dim}f", blob))
            scored.append((_cosine(qvec, stored), int(fact_id)))
        scored.sort(key=lambda pair: -pair[0])
        return [fact_id for _, fact_id in scored[:k]]

    # --------------------------------------------------- other stores ----

    def record_episode(self, summary: str, *, started_at: float | None = None,
                       source_ref: str | None = None) -> int:
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO episodes (started_at, summary, source_ref) VALUES (?,?,?)",
                (started_at if started_at is not None else time.time(),
                 summary, source_ref),
            )
            self._conn.commit()
            return int(cur.lastrowid or 0)

    # ------------------------------------------------ procedures (P3-C) --

    def save_procedure(
        self,
        name: str,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        summary: str,
    ) -> int:
        """Store (or update, keyed by the unique name) one replayable skill:
        the tool-call script plus a summary that doubles as the retrieval
        key (embedded like facts). Returns the procedure id."""
        if not name or not name.strip():
            raise ValueError("procedure name must be non-empty")
        if not summary or not summary.strip():
            raise ValueError("procedure summary must be non-empty")
        if not isinstance(steps, (list, tuple)) or not steps:
            raise ValueError("procedure steps must be a non-empty sequence")
        clean_steps = tuple(dict(step) for step in steps)
        for step in clean_steps:
            if not isinstance(step, dict):
                raise ValueError("each step must be a dict")
        now = time.time()
        vector = self._embedder.embed(summary)
        blob = struct.pack(f"<{len(vector)}f", *vector)
        with self._lock:
            row = self._conn.execute(
                "SELECT id FROM procedures WHERE name = ?", (name,)
            ).fetchone()
            if row is not None:
                proc_id = int(row[0])
                self._conn.execute(
                    "UPDATE procedures SET steps_json = ?, summary = ?,"
                    " updated_at = ? WHERE id = ?",
                    (json.dumps(clean_steps), summary, now, proc_id),
                )
            else:
                cur = self._conn.execute(
                    "INSERT INTO procedures (name, steps_json, summary,"
                    " updated_at) VALUES (?,?,?,?)",
                    (name, json.dumps(clean_steps), summary, now),
                )
                proc_id = int(cur.lastrowid or 0)
            self._conn.execute(
                "DELETE FROM procedure_vectors WHERE procedure_id = ?",
                (proc_id,))
            self._conn.execute(
                "INSERT INTO procedure_vectors (procedure_id, dim, embedding)"
                " VALUES (?,?,?)",
                (proc_id, len(vector), blob),
            )
            self._conn.commit()
        return proc_id

    def get_procedure(self, proc_id: int) -> ProcedureRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT id, name, steps_json, summary, success_count,"
                " fail_count, last_failure, updated_at FROM procedures"
                " WHERE id = ?",
                (proc_id,),
            ).fetchone()
        return self._procedure_row(row) if row is not None else None

    def all_procedures(self) -> list[ProcedureRecord]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, name, steps_json, summary, success_count,"
                " fail_count, last_failure, updated_at FROM procedures"
                " ORDER BY id"
            ).fetchall()
        return [r for row in rows if (r := self._procedure_row(row)) is not None]

    def record_procedure_outcome(
        self,
        proc_id: int,
        success: bool,
        failure_note: str | None = None,
    ) -> bool:
        """Tally one replay/attempt: success or failure (+ note). Skills that
        regress get pruned — the policy lives in kernel/memory/procedural."""
        with self._lock:
            if success:
                cur = self._conn.execute(
                    "UPDATE procedures SET success_count = success_count + 1,"
                    " updated_at = ? WHERE id = ?",
                    (time.time(), proc_id),
                )
            else:
                note = failure_note or "unspecified failure"
                cur = self._conn.execute(
                    "UPDATE procedures SET fail_count = fail_count + 1,"
                    " last_failure = ?, updated_at = ? WHERE id = ?",
                    (note, time.time(), proc_id),
                )
            self._conn.commit()
            return cur.rowcount > 0

    def search_procedures(
        self,
        query: str,
        *,
        k: int = 3,
    ) -> list[ProcedureHit]:
        """Recall similar past procedures by summary-embedding cosine
        (research/04 §7: retrieved by embedding on similar future tasks)."""
        if not query or not query.strip():
            return []
        qvec = self._embedder.embed(query)
        with self._lock:
            rows = self._conn.execute(
                "SELECT pv.procedure_id, pv.dim, pv.embedding FROM"
                " procedure_vectors pv"
            ).fetchall()
            scored: list[tuple[float, int]] = []
            for proc_id, dim, blob in rows:
                if dim != len(qvec):
                    continue          # foreign embedder dimension — skip
                stored = list(struct.unpack(f"<{dim}f", blob))
                scored.append((_cosine(qvec, stored), int(proc_id)))
        scored.sort(key=lambda pair: -pair[0])
        hits: list[ProcedureHit] = []
        for score, proc_id in scored[:k]:
            record = self.get_procedure(proc_id)
            if record is not None:
                hits.append(ProcedureHit(record=record, score=score))
        return hits

    def prune_procedure(self, proc_id: int) -> bool:
        """Hard-delete a regressed skill (row + FK-cascaded vector). Skills
        are pruned, not tombstoned — unlike facts, a stale script has no
        audit value worth keeping."""
        with self._lock:
            cur = self._conn.execute(
                "DELETE FROM procedures WHERE id = ?", (proc_id,))
            self._conn.commit()
            return cur.rowcount > 0

    @staticmethod
    def _procedure_row(row: tuple[Any, ...]) -> ProcedureRecord | None:
        try:
            steps = json.loads(row[2]) if row[2] else []
        except json.JSONDecodeError:
            steps = []             # pre-P3-C raw rows — defensive, not fatal
        if not isinstance(steps, list):
            steps = []
        return ProcedureRecord(
            id=int(row[0]), name=str(row[1]),
            steps=tuple(s for s in steps if isinstance(s, dict)),
            summary=str(row[3] or ""), success_count=int(row[4]),
            fail_count=int(row[5]), last_failure=row[6],
            updated_at=float(row[7] or 0.0),
        )

    def export_facts(self) -> list[dict[str, Any]]:
        """JSON-able dump for evals/backup (values included — caller's care).
        Active facts only: tombstones are audit residue, not memories."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, entity, topic, content, importance, known_at,"
                " source_ref FROM semantic_facts WHERE status = 'active'"
                " ORDER BY id"
            ).fetchall()
        return [dict(zip(
            ("id", "entity", "topic", "content", "importance", "known_at",
             "source_ref"), r)) for r in rows]


def _reciprocal_rank_fusion(
    rankings: list[list[int]],
) -> list[tuple[int, float]]:
    """Standard RRF: score(id) = Σ_legs 1/(RRF_K + rank_in_leg), rank from 1."""
    scores: dict[int, float] = {}
    for ranking in rankings:
        for rank, fact_id in enumerate(ranking, 1):
            scores[fact_id] = scores.get(fact_id, 0.0) + 1.0 / (_RRF_K + rank)
    return sorted(scores.items(), key=lambda pair: -pair[1])


__all__ = ["MemoryEngine", "ProcedureHit", "ProcedureRecord", "SearchHit"]
