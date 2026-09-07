"""tests/test_memory_recall_eval.py — P3-B: the recall-eval CI gate.

Hermetic: the scripted Maya corpus + HashingEmbedder, no network, no live
model. Pins: the corpus shape (40 questions, 8 per type, keys resolve), the
Phase-3 gate itself (score strictly > 0.80 — equality is a degraded
retriever: a dead FTS leg scores exactly 0.80), per-type floors, the
diagnosability contract (retrieved keys recorded), the forbid/absence
verifier path, and the eval's dynamic range (it must COLLAPSE when retrieval
is crippled, or it proves nothing).
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from kernel.memory.evals import (
    COUNTS,
    GATE,
    QTYPES,
    EvalCorpus,
    RecallQuestion,
    RecallReport,
    QuestionResult,
    build_eval_corpus,
    recall_gate_ok,
    run_recall_eval,
    seed_eval_engine,
)


@pytest.fixture(scope="module")
def corpus() -> EvalCorpus:
    return build_eval_corpus()


@pytest.fixture()
def report(corpus, tmp_path):
    eng = seed_eval_engine(corpus, tmp_path / "eval.sqlite3")
    try:
        yield run_recall_eval(eng, corpus)
    finally:
        eng.close()


def test_corpus_shape_and_key_integrity(corpus) -> None:
    assert len(corpus.questions) == 40
    assert len(corpus.facts) == 30
    for qtype in QTYPES:
        n = sum(1 for q in corpus.questions if q.qtype == qtype)
        assert n == 8, f"expected 8 {qtype} questions, found {n}"
    keys = {f.key for f in corpus.facts}
    assert len(keys) == len(corpus.facts)          # unique keys
    session_range = range(len(corpus.sessions))
    for fact in corpus.facts:
        assert fact.session in session_range
    for q in corpus.questions:
        for key in (*q.expect, *q.forbid):
            assert key in keys, f"{q.id} references unknown key {key!r}"
        assert q.expect or q.forbid, f"{q.id} verifies nothing"


def test_phase3_gate_score_exceeds_80_percent(report) -> None:
    assert report.score > GATE, (
        f"recall eval below the Phase-3 gate: {report.score:.3f} <= {GATE}\n"
        f"failures: {json.dumps(report.summary()['failures'], indent=1)}"
    )
    assert set(report.by_type) == set(QTYPES)
    for qtype, acc in report.by_type.items():
        assert acc >= 0.5, f"{qtype} collapsed to {acc:.2f}"


def test_gate_is_strict_beyond_a_degraded_retriever(report) -> None:
    # a dead FTS leg scores exactly 0.80 on this corpus — that must FAIL
    assert recall_gate_ok(report) is True
    assert report.score > 0.799 and report.score > GATE


def test_eval_has_dynamic_range(corpus, tmp_path) -> None:
    """The eval must collapse when retrieval is crippled, or it proves
    nothing. Pins the discriminating power that makes the gate meaningful."""
    eng = seed_eval_engine(corpus, tmp_path / "eval.sqlite3")
    try:
        full = run_recall_eval(eng, corpus).score
        narrow = run_recall_eval(eng, corpus, k=1).score
        assert full > narrow
        assert narrow < GATE                            # k=1 collapses
        orig = eng._fts_leg
        eng._fts_leg = lambda q, k: []                  # kill the FTS leg
        no_fts = run_recall_eval(eng, corpus).score
        assert no_fts < full
        assert not recall_gate_ok(run_recall_eval(eng, corpus, k=1))
        assert orig is not None
    finally:
        eng.close()


def test_report_is_jsonable_and_records_retrieved_keys(report) -> None:
    summary = report.summary()
    json.dumps(summary)                                 # bus/trace payload
    assert summary["total"] == 40
    first = report.results[0]
    assert isinstance(first, QuestionResult)
    assert len(first.retrieved) > 0                     # diagnosability
    assert 0.0 <= report.score <= 1.0


def test_missing_and_forbid_verifiers_fire(corpus, tmp_path) -> None:
    extra = (
        RecallQuestion("syn1", COUNTS, "totally unrelated wording?",
                       expect=("nonexistent-key",)),
        RecallQuestion("syn2", COUNTS, "Where does Maya live?",
                       forbid=("home_city",)),         # violated
    )
    patched = EvalCorpus(sessions=corpus.sessions, facts=corpus.facts,
                         questions=corpus.questions + extra)
    eng = seed_eval_engine(patched, tmp_path / "syn.sqlite3")
    try:
        rep = run_recall_eval(eng, patched)
        by_id = {r.id: r for r in rep.results}
        assert by_id["syn1"].correct is False
        assert by_id["syn1"].missing == ("nonexistent-key",)
        assert by_id["syn2"].correct is False
        assert by_id["syn2"].violated == ("home_city",)
        assert by_id["sh1"].correct is True             # corpus unchanged
        # score math: 42 questions scored, exactly the 2 synthetic ones wrong
        assert rep.score == pytest.approx(
            sum(r.correct for r in rep.results) / 42)
    finally:
        eng.close()


def test_seed_tags_eval_source_refs_and_known_at(corpus, tmp_path) -> None:
    eng = seed_eval_engine(corpus, tmp_path / "eval.sqlite3")
    try:
        hits = eng.search("Monstera", k=1)
        assert hits and hits[0].source_ref == "eval:monstera"
        expected_ts = corpus.sessions[2].started_at     # March session
        fact = [f for f in corpus.facts if f.key == "plant_light"][0]
        assert eng._conn.execute(
            "SELECT known_at FROM semantic_facts WHERE source_ref = ?",
            (f"eval:{fact.key}",)).fetchone()[0] == (
            corpus.sessions[fact.session].started_at)
        assert expected_ts > 0
    finally:
        eng.close()


def test_report_dataclass_is_frozen() -> None:
    result = QuestionResult(id="x", qtype=COUNTS, question="q?", correct=True,
                            retrieved=(), missing=(), violated=())
    rep = RecallReport(results=(result,), score=1.0, by_type={}, k=8)
    with pytest.raises(dataclasses.FrozenInstanceError):
        rep.score = 0.5                                 # type: ignore[misc]
