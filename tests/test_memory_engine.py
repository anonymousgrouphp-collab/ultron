"""tests/test_memory_engine.py — P1-D: memory engine v0 semantics.

Hermetic: tmp_path SQLite files + HashingEmbedder (stdlib, deterministic).
Pins: WAL mode, FTS5+vector RRF fusion, forget/page/persistence, FTS-injection
safety, the idempotent long_term.json migration, and the READ-risk kernel
tools. Migrated memory content in tests is synthetic — the real file's
contents are never printed or committed.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from kernel.memory import (
    MemoryEngine,
    make_embedder,
    migrate_long_term_json,
    register_memory_tools,
)
from kernel.memory.embedders import EmbedderUnavailable, HashingEmbedder
from kernel.tools import ToolRegistry
from kernel.types import ToolCall


@pytest.fixture()
def engine(tmp_path):
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    yield eng
    eng.close()


def test_remember_and_count(engine) -> None:
    fid = engine.remember("The sky is blue.", entity="nature", topic="sky")
    assert fid == 1
    assert engine.count() == 1
    with pytest.raises(ValueError):
        engine.remember("   ")


def test_fts_keyword_leg(engine) -> None:
    engine.remember("Quarterly report is stored in documents folder",
                    entity="work")
    hits = engine.search("quarterly report documents")
    assert hits and "Quarterly report" in hits[0].content


def test_rrf_fusion_ranks_consensus_fact_first(engine) -> None:
    engine.remember("The sky is blue today with clear weather", topic="sky")
    engine.remember("Blueberries are a fruit", topic="food")
    hits = engine.search("blue sky weather")
    assert hits[0].topic == "sky"
    # both legs contributed to the fused score
    assert hits[0].score > 0.0


def test_search_hit_shape_and_limit(engine) -> None:
    for i in range(5):
        engine.remember(f"fact number {i} about planets", topic="space")
    hits = engine.search("planets", k=3)
    assert len(hits) == 3
    hit = hits[0]
    assert hit.id >= 1 and hit.content and hit.known_at > 0


def test_forget_removes_fact_everywhere(engine) -> None:
    fid = engine.remember("temporary secret note")
    assert engine.search("secret note")
    assert engine.forget(fid) is True
    assert engine.forget(fid) is False  # already gone
    assert engine.count() == 0
    assert engine.search("secret note") == []


def test_page_walks_newest_first_with_entity_filter(engine) -> None:
    first = engine.remember("old fact", entity="work")
    second = engine.remember("new fact", entity="home")
    page = engine.page()
    assert [r.id for r in page] == [second, first]
    assert engine.page(entity="work")[0].content == "old fact"
    assert engine.page(entity="nothing") == []


def test_wal_mode_and_persistence(tmp_path) -> None:
    db = tmp_path / "persist.sqlite3"
    eng = MemoryEngine(db)
    assert eng._conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    eng.remember("durable fact about persistence")
    eng.close()
    eng2 = MemoryEngine(db)
    try:
        assert eng2.count() == 1
        assert eng2.search("persistence")
    finally:
        eng2.close()


def test_fts_syntax_in_user_query_is_safe(engine) -> None:
    engine.remember("plain fact content")
    for nasty in ['"(*', "OR AND NOT", "a b '", "NEAR("]:
        hits = engine.search(nasty)
        assert isinstance(hits, list)
    assert engine.search("") == []


def test_foreign_dimension_vectors_are_skipped_not_crashes(engine) -> None:
    engine.remember("sky weather fact")
    engine._embedder = HashingEmbedder(dim=64)  # different dim mid-life
    hits = engine.search("sky weather")  # FTS leg still works
    assert hits and "sky weather fact" == hits[0].content


def test_migration_is_complete_and_idempotent(tmp_path) -> None:
    legacy = tmp_path / "long_term.json"
    legacy.write_text(json.dumps({
        "identity": {"name": {"value": "Test User"}},
        "preferences": {"editor": {"value": "nvim"}},
        "projects": {"ultron": "the agent harness"},
        "notes": ["first note", "second note"],
    }), encoding="utf-8")
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    try:
        added = migrate_long_term_json(eng, legacy)
        assert added == 5  # name + editor + ultron project + 2 list notes
        assert eng.count() == added
        assert migrate_long_term_json(eng, legacy) == 0  # re-run adds nothing
        hits = eng.search("Test User")
        assert hits and hits[0].source_ref == "long_term.json"
        assert hits[0].entity == "identity"
    finally:
        eng.close()


def test_migration_rejects_non_object_root(tmp_path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("[1, 2, 3]", encoding="utf-8")
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    try:
        with pytest.raises(ValueError, match="JSON object"):
            migrate_long_term_json(eng, bad)
    finally:
        eng.close()


def test_memory_tools_registered_as_read(tmp_path) -> None:
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    try:
        eng.remember("user likes dark themes", entity="preferences")
        reg = ToolRegistry()
        register_memory_tools(reg, eng)
        search = asyncio.run(reg.execute(
            ToolCall("c1", "memory_search", {"query": "dark themes"})))
        assert search.ok and search.data
        assert search.data[0]["content"] == "user likes dark themes"
        page = asyncio.run(reg.execute(
            ToolCall("c2", "memory_page", {"entity": "preferences"})))
        assert page.ok and len(page.data) == 1
        # declarations are gateway-ready and both tools are READ risk
        names = {d["name"] for d in reg.declarations()}
        assert {"memory_search", "memory_page"} <= names
        assert all(r is not None for r in reg.risks().values())
    finally:
        eng.close()


def test_make_embedder_factory() -> None:
    emb = make_embedder("hashing")
    assert emb.dim == 256
    vec = emb.embed("hello world")
    assert len(vec) == 256
    assert abs(sum(v * v for v in vec) - 1.0) < 1e-9  # L2 normalized
    with pytest.raises(ValueError, match="unknown embedder"):
        make_embedder("word2vec")
    # A4: the missing optional dep raises the user-safe contract
    # (EmbedderUnavailable names the pip package — kernel.voice precedent).
    with pytest.raises(EmbedderUnavailable, match="fastembed"):
        make_embedder("bge-m3")  # optional dep absent in CI


def test_episodes_and_export(tmp_path) -> None:
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    try:
        eng.remember("exported fact")
        eng.record_episode("session one summary")
        facts = eng.export_facts()
        assert facts[0]["content"] == "exported fact"
        assert facts[0]["source_ref"] is None
    finally:
        eng.close()
