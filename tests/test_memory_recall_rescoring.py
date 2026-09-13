"""tests/test_memory_recall_rescoring.py — §P1-C query-time re-ranking.

Pins the K9-derived recall upgrades on MemoryEngine.search():
1. recency re-ranking — an otherwise-equal fresh fact outranks a backdated one;
2. per-fact TTL — a fact with expires_at in the past is never recalled;
3. unexpired facts still recall, and scores stay positive and k-bounded.
"""

from __future__ import annotations

import time

import pytest

from kernel.memory.engine import MemoryEngine


@pytest.fixture()
def engine(tmp_path):
    eng = MemoryEngine(tmp_path / "memory.db")
    yield eng
    eng.close()


def test_fresh_fact_outranks_backdated_equal(engine):
    """Two facts with equal lexical/vector overlap: the fresh one ranks first."""
    engine.remember("The user's favorite editor is neovim",
                    importance=0.5, known_at=time.time() - 30 * 86400)
    engine.remember("The user's favorite editor is helix",
                    importance=0.5, known_at=time.time())
    hits = engine.search("favorite editor", k=2)
    assert len(hits) == 2
    assert "helix" in hits[0].content
    assert "neovim" in hits[1].content


def test_backdated_fact_still_recallable(engine):
    """Re-ranking must not bury the only match — a single matching fact
    (relative similarity 1.0) always clears the minimum-score gate."""
    engine.remember("The user's favorite editor is neovim",
                    importance=0.5, known_at=time.time() - 30 * 86400)
    hits = engine.search("favorite editor", k=1)
    assert len(hits) == 1
    assert "neovim" in hits[0].content
    assert hits[0].score > 0.0


def test_expired_fact_never_recalled(engine):
    """expires_at in the past — excluded from recall even when both legs match."""
    fid = engine.remember("The temporary deploy token is abc123",
                          importance=1.0,
                          expires_at=time.time() - 1.0)
    hits = engine.search("deploy token", k=5)
    assert all(h.id != fid for h in hits)


def test_unexpired_fact_recalled(engine):
    """expires_at in the future — still fully recallable."""
    fid = engine.remember("The temporary deploy token is abc123",
                          importance=1.0,
                          expires_at=time.time() + 3600.0)
    hits = engine.search("deploy token", k=5)
    assert any(h.id == fid for h in hits)


def test_k_bound_and_positive_scores(engine):
    for i in range(12):
        engine.remember(f"Project {i} uses the sampling pipeline",
                        importance=0.5)
    hits = engine.search("sampling pipeline", k=3)
    assert len(hits) <= 3
    assert all(h.score > 0.0 for h in hits)
    assert hits == sorted(hits, key=lambda h: -h.score)
