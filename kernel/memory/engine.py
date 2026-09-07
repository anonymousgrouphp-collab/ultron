"""kernel/memory/engine.py — P1-D: the memory engine v0 (research/04 §10).

SQLite (WAL) with the five §10 stores — semantic_facts, episodes, procedures,
people, preferences — plus two recall legs fused by Reciprocal Rank Fusion:
FTS5 (keyword) and vector cosine over stored BLOBs. The v0 vector leg is
brute-force cosine (personal-scale data); swapping in the sqlite-vec ANN index
later changes only this file's retrieval internals, not the schema or API —
vectors are already stored dimension-tagged per embedder.

Thread model: one connection guarded by an RLock (mirrors the P1-E AuditLog);
every method is atomic. No method ever raises to a caller for "empty" results —
they return lists.
"""

from __future__ import annotations

import sqlite3
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.memory.embedders import Embedder, HashingEmbedder

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
    source_ref TEXT
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
CREATE TABLE IF NOT EXISTS procedures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    steps_json TEXT NOT NULL DEFAULT '[]',
    success_count INTEGER NOT NULL DEFAULT 0,
    fail_count INTEGER NOT NULL DEFAULT 0
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


def _fts_safe_query(text: str) -> str:
    """Quote each token so user text can't inject FTS5 syntax."""
    tokens = text.lower().split()
    return " ".join(f'"{token}"' for token in tokens if token)


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
    ) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.executescript(_SCHEMA)
        self._lock = threading.RLock()
        self._embedder = embedder if embedder is not None else HashingEmbedder()

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
    ) -> int:
        """Store one fact; returns its id. The vector leg indexes it at write
        time (write-time embedding, research/04 §10 write path, v0 flavor)."""
        if not content or not content.strip():
            raise ValueError("content must be non-empty")
        now = time.time()
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
                    " source_ref FROM semantic_facts WHERE id = ?",
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
                    " source_ref FROM semantic_facts ORDER BY known_at DESC, id DESC"
                    " LIMIT ? OFFSET ?",
                    (limit, offset),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT id, content, entity, topic, importance, known_at,"
                    " source_ref FROM semantic_facts WHERE entity = ?"
                    " ORDER BY known_at DESC, id DESC LIMIT ? OFFSET ?",
                    (entity, limit, offset),
                ).fetchall()
        return [SearchHit(id=r[0], content=r[1], entity=r[2], topic=r[3],
                          importance=r[4], known_at=r[5], source_ref=r[6])
                for r in rows]

    def count(self) -> int:
        with self._lock:
            return int(self._conn.execute(
                "SELECT COUNT(*) FROM semantic_facts").fetchone()[0])

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
            "SELECT fact_id, dim, embedding FROM fact_vectors"
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

    def export_facts(self) -> list[dict[str, Any]]:
        """JSON-able dump for evals/backup (values included — caller's care)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT id, entity, topic, content, importance, known_at,"
                " source_ref FROM semantic_facts ORDER BY id"
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


__all__ = ["MemoryEngine", "SearchHit"]
