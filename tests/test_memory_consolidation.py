"""tests/test_memory_consolidation.py — P3-A: write policy + consolidation.

Hermetic: tmp_path SQLite + HashingEmbedder + a ScriptedGateway that stands in
for the judge (no network, no model strings). Pins: the policy gate (importance,
budget, op validation), judge-output normalization (untrusted input), extraction
ADD/UPDATE/DELETE/NOOP application with target validation, tombstone semantics
(hidden from every read path, restorable), decay half-life + floor + expiry,
reflection synthesis + source_ref lineage, clean failure on gateway errors, and
the memory.consolidated bus event.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from collections.abc import Mapping, Sequence

import pytest

from kernel.bus import EventBus
from kernel.gateway.base import (
    Gateway,
    GatewayError,
    GatewaySettings,
    Message,
    Provider,
    Response,
)
from kernel.memory import (
    Consolidator,
    MemoryEngine,
    MemoryOp,
    WritePolicy,
    parse_ops_json,
)
from kernel.memory.policy import OPS_SCHEMA
from kernel.types import Event

# ----------------------------------------------------------- fakes ------


class ScriptedGateway(Gateway):
    """Stands in for the judge: pops scripted Responses (or raises)."""

    provider = Provider.GEMINI

    def __init__(
        self, responses: Sequence[Response | Exception]
    ) -> None:
        super().__init__(GatewaySettings(provider=Provider.GEMINI))
        self._responses = list(responses)
        self.calls: list[tuple[list[Message], Mapping[str, object] | None]] = []

    @property
    def model(self) -> str:
        return "scripted-judge"

    def _url(self) -> str:
        return ""

    def _headers(self) -> dict[str, str]:
        return {}

    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, object]],
        response_schema: Mapping[str, object] | None,
    ) -> dict[str, object]:
        return {}

    def _parse(self, data: Mapping[str, object]) -> Response:
        return Response(text="")

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, object]] = (),
        response_schema: Mapping[str, object] | None = None,
    ) -> Response:
        self.calls.append((list(messages), response_schema))
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def ops_response(ops: list[dict[str, object]]) -> Response:
    return Response(text=json.dumps({"ops": ops}))


def reflect_response(reflection: str, importance: float) -> Response:
    return Response(text=json.dumps(
        {"reflection": reflection, "importance": importance}))


def op(**kwargs: object) -> dict[str, object]:
    base: dict[str, object] = {
        "op": "ADD", "content": "", "entity": "", "topic": "",
        "importance": 0.5, "justification": "stated by the user",
    }
    base.update(kwargs)
    return base


@pytest.fixture()
def engine(tmp_path):
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    yield eng
    eng.close()


def make_consolidator(engine, responses, bus=None):
    gateway = ScriptedGateway(responses)
    return Consolidator(engine, gateway, bus=bus), gateway


def age_fact(engine: MemoryEngine, fact_id: int, days: float) -> None:
    """Test-only: backdate a fact's known_at to exercise decay math."""
    engine._conn.execute(
        "UPDATE semantic_facts SET known_at = ?, last_decayed_at = NULL WHERE id = ?",
        (time.time() - days * 86400.0, fact_id),
    )
    engine._conn.commit()


# ------------------------------------------------- policy validation ----


def test_policy_gate_and_validation() -> None:
    policy = WritePolicy()
    assert policy.validate(MemoryOp(op="ADD", content="fact",
                                    importance=0.3)) is None  # at gate → pass
    assert "below gate" in (policy.validate(MemoryOp(
        op="ADD", content="fact", importance=0.2)) or "")
    assert policy.validate(MemoryOp(op="ADD", importance=0.9))  # no content
    assert policy.validate(MemoryOp(op="UPDATE", content="x",
                                    importance=0.9))  # no target_id
    assert policy.validate(MemoryOp(op="DELETE", target_id=0))  # invalid id
    assert policy.validate(MemoryOp(op="TRANSMUTE", importance=0.9))
    assert policy.validate(MemoryOp(op="NOOP", justification="nothing new")) \
        is None


def test_parse_ops_json_normalizes_untrusted_output() -> None:
    policy = WritePolicy()
    result = parse_ops_json(json.dumps({"ops": [
        op(op="add", content="  user prefers tea  ", importance="0.9"),
        op(op="ADD", content="", importance=0.9),          # no content
        op(op="UPDATE", content="x", target_id="abc"),     # invalid target
        op(op="ADD", content="tiny", importance=0.1),      # below gate
        "not-an-object",
    ]}), policy)
    assert result.error is None
    assert len(result.ops) == 1
    assert result.ops[0].op == "ADD"
    assert result.ops[0].content == "user prefers tea"
    assert result.ops[0].importance == 0.9
    reasons = " | ".join(reason for _, reason in result.rejected)
    assert "without content" in reasons
    assert "valid target_id" in reasons
    assert "below gate" in reasons
    assert "not an object" in reasons


def test_parse_ops_json_budget_and_garbage() -> None:
    policy = WritePolicy(max_ops=2)
    result = parse_ops_json(json.dumps({"ops": [
        op(content=f"fact {i}", importance=0.9) for i in range(3)
    ]}), policy)
    assert len(result.ops) == 2
    assert result.rejected[-1][1] == "over the operation budget"

    garbage = parse_ops_json("not json at all", policy)
    assert garbage.error == "judge returned non-JSON output"
    missing = parse_ops_json('{"ops": "nope"}', policy)
    assert missing.error is not None
    fenced = parse_ops_json(
        '```json\n{"ops": [{"op": "NOOP", "justification": "small talk"}]}\n```',
        policy)
    assert fenced.ops[0].op == "NOOP"


# ------------------------------------------------- engine P3-A adds ----


def test_tombstone_hides_from_every_read_and_restore_undo(engine) -> None:
    fid = engine.remember("user's birthday is March 3", entity="identity")
    assert engine.tombstone(fid, "user retracted this") is True
    assert engine.tombstone(fid, "again") is False       # already retired
    assert engine.count() == 0
    assert engine.search("birthday March") == []          # FTS leg hidden
    assert engine.search("user birthday") == []           # vector leg hidden
    assert engine.page() == []
    assert engine.get_fact(fid) is None
    assert engine.export_facts() == []
    assert engine.active_facts() == []
    row = engine._conn.execute(
        "SELECT status, tombstone_reason, tombstoned_at IS NOT NULL"
        " FROM semantic_facts WHERE id = ?", (fid,)).fetchone()
    assert row == ("tombstoned", "user retracted this", 1)
    assert engine.restore(fid) is True
    assert engine.restore(fid) is False
    assert engine.count() == 1
    assert engine.search("birthday March")[0].id == fid


def test_update_fact_reindexes_and_bumps_known_at_only_for_content(
    engine,
) -> None:
    fid = engine.remember("user drives a red car", topic="vehicles",
                          valid_from=123.0)
    time.sleep(0.02)  # ensure the clock visibly moved
    before = engine.get_fact(fid)
    assert engine.update_fact(fid, content="user drives a blue car") is True
    after = engine.get_fact(fid)
    assert after is not None and before is not None
    assert after.known_at > before.known_at               # re-learned
    assert engine.search("blue car")[0].content == "user drives a blue car"
    # FTS no longer matches the old text (any remaining hit comes from the
    # semantic vector leg and must show the NEW content, never the old)
    for hit in engine.search("red car"):
        assert "red car" not in hit.content
    valid_from = engine._conn.execute(
        "SELECT valid_from FROM semantic_facts WHERE id = ?",
        (fid,)).fetchone()[0]
    assert valid_from == 123.0                            # bi-temporal kept

    time.sleep(0.02)
    before2 = engine.get_fact(fid)
    assert engine.update_fact(fid, topic="transport") is True  # metadata only
    after2 = engine.get_fact(fid)
    assert after2 is not None and before2 is not None
    assert after2.known_at == before2.known_at            # not re-learned
    assert engine.get_fact(fid).topic == "transport"


def test_update_fact_refuses_empty_content_and_tombstoned_rows(engine) -> None:
    fid = engine.remember("some fact")
    with pytest.raises(ValueError):
        engine.update_fact(fid, content="   ")
    engine.tombstone(fid, "gone")
    assert engine.update_fact(fid, content="revived content") is False


def test_set_importance_clamps_without_bumping_known_at(engine) -> None:
    fid = engine.remember("fact subject to decay", importance=0.5)
    time.sleep(0.02)
    before = engine.get_fact(fid)
    assert engine.set_importance(fid, 0.9) is True
    after = engine.get_fact(fid)
    assert after is not None and before is not None
    assert after.importance == 0.9
    assert after.known_at == before.known_at              # decay ≠ re-learning
    assert engine.set_importance(fid, 5.0) is True        # clamped
    assert engine.get_fact(fid).importance == 1.0
    assert engine.set_importance(fid, -3.0) is True
    assert engine.get_fact(fid).importance == 0.0


def test_pre_p3a_database_is_migrated_in_place(tmp_path) -> None:
    db = tmp_path / "legacy.sqlite3"
    old_schema = """
    CREATE TABLE semantic_facts (
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
    """
    conn = sqlite3.connect(str(db))
    conn.executescript(old_schema)
    conn.execute(
        "INSERT INTO semantic_facts (entity, content, importance, known_at)"
        " VALUES ('legacy', 'pre-P3A fact survives migration', 0.5, ?)",
        (time.time(),),
    )
    conn.commit()
    conn.close()

    eng = MemoryEngine(db)
    try:
        assert eng.count() == 1
        fid = eng.active_facts()[0]["id"]
        assert eng.tombstone(fid, "consolidation") is True
        assert eng.count() == 0
        assert eng.restore(fid) is True
        # legacy rows predate the vector/FTS indexes — an UPDATE re-indexes
        assert eng.update_fact(fid, content="pre-P3A fact survives migration"
                                              " and re-indexing") is True
        assert eng.search("survives migration")
    finally:
        eng.close()


# ------------------------------------------------------ extraction ----


def test_extract_applies_add_update_delete_and_noop(engine) -> None:
    update_target = engine.remember("user lives on Elm Street",
                                    entity="home", importance=0.7)
    delete_target = engine.remember("user works at Initech", entity="work",
                                    importance=0.7)
    consolidator, gateway = make_consolidator(engine, [ops_response([
        op(op="ADD", content="user's sister is called Dana", entity="family",
           topic="people", importance=0.8),
        op(op="UPDATE", target_id=update_target,
           content="user lives on Oak Avenue", entity="home",
           justification="moved, stated explicitly"),
        op(op="DELETE", target_id=delete_target,
           justification="job change retracted the Initech fact"),
        op(op="NOOP", justification="rest is small talk"),
    ])])
    transcript = [Message(role="user", text="I moved to Oak Avenue and left "
                                          "Initech; my sister Dana visited."),
                  Message(role="assistant", text="Noted!")]
    report = asyncio.run(consolidator.extract(transcript))

    assert report.errors == []
    assert len(report.added) == 1
    assert report.updated == [update_target]
    assert report.deleted == [delete_target]
    assert report.noops == 1
    assert engine.count() == 2                            # 2 seeded +1 ADD −1 retired
    added = engine.get_fact(report.added[0])
    assert added is not None and "Dana" in added.content
    assert added.source_ref == "consolidation"            # default source_ref
    assert engine.search("Oak Avenue")[0].id == update_target
    # the tombstoned Initech fact is gone from recall (other facts may still
    # surface on the semantic leg — they must not show the retired content)
    for hit in engine.search("Initech"):
        assert hit.id != delete_target and "Initech" not in hit.content
    # the judge saw the dedupe context (existing facts) and the ops schema
    messages, schema = gateway.calls[0]
    assert schema == OPS_SCHEMA
    prompt = messages[1].text
    assert "Elm Street" in prompt and "Initech" in prompt


def test_extract_dedupe_second_run_does_not_duplicate(engine) -> None:
    consolidator, _ = make_consolidator(engine, [
        ops_response([op(content="user prefers tea over coffee",
                         importance=0.7)]),
        ops_response([op(op="NOOP", justification="already stored")]),
    ])
    transcript = [Message(role="user",
                          text="I prefer tea over coffee these days.")]
    first = asyncio.run(consolidator.extract(transcript))
    second = asyncio.run(consolidator.extract(transcript))
    assert len(first.added) == 1
    assert second.noops == 1 and second.added == []
    assert engine.count() == 1
    # second judge call received the just-added fact as dedupe context
    second_prompt = asyncio.run(_last_prompt_text(consolidator))
    assert "user prefers tea over coffee" in second_prompt


async def _last_prompt_text(consolidator: Consolidator) -> str:
    gateway = consolidator._gateway
    assert isinstance(gateway, ScriptedGateway)
    return gateway.calls[-1][0][1].text


def test_extract_rejects_unknown_targets(engine) -> None:
    consolidator, _ = make_consolidator(engine, [ops_response([
        op(op="UPDATE", target_id=999, content="ghost",
           justification="hallucinated id"),
        op(op="DELETE", target_id=999, justification="hallucinated id"),
    ])])
    report = asyncio.run(consolidator.extract(
        [Message(role="user", text="anything")]))
    assert report.updated == [] and report.deleted == []
    reasons = " | ".join(reason for _, reason in report.rejected)
    assert "not found" in reasons
    assert engine.count() == 0


def test_extract_update_without_importance_keeps_target_importance(
    engine,
) -> None:
    fid = engine.remember("user's deadline is Friday", importance=0.9)
    consolidator, _ = make_consolidator(engine, [ops_response([
        # judge omits importance (schema violation we tolerate defensively)
        op(op="UPDATE", target_id=fid, content="user's deadline is Monday",
           importance=None, justification="date changed"),
    ])])
    report = asyncio.run(consolidator.extract(
        [Message(role="user", text="my deadline moved to Monday")]))
    assert report.updated == [fid]
    fact = engine.get_fact(fid)
    assert fact is not None
    assert "Monday" in fact.content
    assert fact.importance == 0.9          # not silently downgraded to 0.5


def test_extract_gateway_error_is_data_not_exception(engine) -> None:
    consolidator, _ = make_consolidator(
        engine, [GatewayError("HTTP 500 from gemini")])
    report = asyncio.run(consolidator.extract(
        [Message(role="user", text="remember this")]))
    assert report.errors == ["judge call failed: HTTP 500 from gemini"]
    assert engine.count() == 0                            # engine untouched


def test_extract_empty_transcript_is_a_clean_noop(engine) -> None:
    consolidator, _ = make_consolidator(engine, [])
    report = asyncio.run(consolidator.extract(
        [Message(role="assistant", text="   ")]))
    assert report.errors == ["transcript has no text to extract from"]
    assert gateway_calls_empty(consolidator)


def gateway_calls_empty(consolidator: Consolidator) -> bool:
    gateway = consolidator._gateway
    assert isinstance(gateway, ScriptedGateway)
    return gateway.calls == []


# ---------------------------------------------------------- decay -----


def test_decay_half_life_math(engine) -> None:
    fid = engine.remember("aging fact", importance=0.8)
    age_fact(engine, fid, days=30.0)                      # one half-life
    consolidator, _ = make_consolidator(engine, [])
    report = asyncio.run(consolidator.consolidate(run_reflect=False))
    assert report.decayed == [fid]
    fact = engine.get_fact(fid)
    assert fact is not None
    assert fact.importance == pytest.approx(0.4, abs=1e-6)


def test_decay_does_not_compound_exponentially_on_repeated_runs(engine) -> None:
    """REV-01: multiple consolidation passes without elapsed time must NOT decay repeatedly."""
    fid = engine.remember("stable fact", importance=0.8)
    age_fact(engine, fid, days=30.0)                      # one half-life
    consolidator, _ = make_consolidator(engine, [])
    # Run 1: decays by 1 half-life -> 0.4
    report1 = asyncio.run(consolidator.consolidate(run_reflect=False))
    assert report1.decayed == [fid]
    fact1 = engine.get_fact(fid)
    assert fact1 is not None and fact1.importance == pytest.approx(0.4, abs=1e-6)

    # Runs 2-10 immediately with no elapsed time: MUST NOT decay further!
    for _ in range(9):
        report = asyncio.run(consolidator.consolidate(run_reflect=False))
        assert report.decayed == []
    fact_after = engine.get_fact(fid)
    assert fact_after is not None and fact_after.importance == pytest.approx(0.4, abs=1e-6)


def test_decay_floor_tombstones_and_expiry(engine) -> None:
    old = engine.remember("ancient minor fact", importance=0.5)
    age_fact(engine, old, days=30.0 * 10)                 # 0.5 / 2^10 ≪ floor
    expiring = engine.remember("whereabouts: at the gym", importance=0.9,
                               expires_at=time.time() - 1.0)
    fresh = engine.remember("fresh fact", importance=0.5)
    consolidator, _ = make_consolidator(engine, [])
    report = asyncio.run(consolidator.consolidate(run_reflect=False))
    assert report.decayed_out == [old]
    assert report.expired == [expiring]
    assert report.decayed == []                           # fresh untouched
    assert engine.count() == 1
    assert engine.get_fact(fresh) is not None
    reason = engine._conn.execute(
        "SELECT tombstone_reason FROM semantic_facts WHERE id = ?",
        (expiring,)).fetchone()[0]
    assert reason == "expired"
    # audit + undo: a decayed-out fact can be restored by a human
    assert engine.restore(old) is True


def test_decay_rejects_nonpositive_half_life(engine) -> None:
    engine.remember("any fact")
    consolidator, _ = make_consolidator(engine, [])
    report = consolidator.decay(half_life_days=0)          # sync — no gateway
    assert report.errors == ["half_life_days must be positive"]
    assert engine.count() == 1


# ------------------------------------------------------ reflection ----


def test_reflect_synthesizes_one_higher_level_fact(engine) -> None:
    ids = [engine.remember(f"workout skipped on day {i}", entity="fitness",
                           importance=0.5)
           for i in range(3)]
    consolidator, gateway = make_consolidator(
        engine, [reflect_response("User has been skipping workouts for three "
                                  "days in a row.", 0.7)])
    report = asyncio.run(consolidator.reflect(entity="fitness"))
    assert report.errors == [] and len(report.reflections) == 1
    reflection = engine.get_fact(report.reflections[0])
    assert reflection is not None
    assert reflection.topic == "reflection"
    assert reflection.entity == "fitness"
    assert reflection.importance == pytest.approx(0.7)
    assert reflection.source_ref == f"reflection:{ids}"
    messages, schema = gateway.calls[0]
    assert schema is not None and "reflection" in json.dumps(schema)
    assert "workout skipped on day 0" in messages[1].text


def test_reflect_skips_small_clusters_and_empty_insights(engine) -> None:
    engine.remember("solo fact")
    consolidator, _ = make_consolidator(engine, [])
    report = asyncio.run(consolidator.reflect(min_facts=3))
    assert report.reflections == []
    assert report.skipped and "at least 3" in report.skipped[0]

    for i in range(3):
        engine.remember(f"cluster fact {i}", entity="notes")
    consolidator2, _ = make_consolidator(
        engine, [reflect_response("", 0.5)])
    report2 = asyncio.run(consolidator2.reflect())
    assert report2.reflections == []
    assert report2.skipped == ["judge synthesized no insight"]
    assert engine.count() == 4                            # nothing stored


def test_reflect_gateway_error_is_data_not_exception(engine) -> None:
    for i in range(3):
        engine.remember(f"fact {i}", entity="x")
    consolidator, _ = make_consolidator(
        engine, [GatewayError("cannot reach 127.0.0.1:11434")])
    report = asyncio.run(consolidator.reflect())
    assert report.errors == ["reflection call failed: cannot reach "
                             "127.0.0.1:11434"]
    assert engine.count() == 3


# --------------------------------------------------- orchestration ----


def test_consolidate_full_run_reports_and_publishes_bus_event(engine) -> None:
    engine.remember("old fact to decay out", importance=0.5)
    engine._conn.execute(
        "UPDATE semantic_facts SET known_at = ?",
        (time.time() - 30.0 * 10 * 86400.0,))
    engine._conn.commit()
    engine.remember("user runs on weekends", entity="fitness", importance=0.6)
    engine.remember("user owns running shoes", entity="fitness", importance=0.6)
    bus = EventBus()
    seen: list[Event] = []
    bus.subscribe("memory.*", seen.append)
    consolidator, _ = make_consolidator(engine, [
        ops_response([op(content="user is training for a 10k",
                         entity="fitness", importance=0.8)]),
        reflect_response("User is increasingly focused on running.", 0.6),
    ], bus=bus)
    transcript = [Message(role="user", text="I started training for a 10k!")]
    report = asyncio.run(consolidator.consolidate(transcript))

    assert report.errors == []
    assert len(report.added) == 1 and len(report.reflections) == 1
    assert report.decayed_out == [1]
    assert len(seen) == 1 and seen[0].type == "memory.consolidated"
    payload = seen[0].payload
    assert payload["added"] == report.added
    assert payload["reflections"] == report.reflections
    assert payload["decayed_out"] == [1]
    assert seen[0].source == "memory.consolidation"


def test_consolidate_without_transcript_still_maintains(engine) -> None:
    engine.remember("stable fact", importance=0.9)
    consolidator, _ = make_consolidator(engine, [reflect_response("", 0.5)])
    report = asyncio.run(consolidator.consolidate())   # idle-time, no session
    assert report.added == [] and report.decayed == []
    # one fact is below the reflection cluster size — the judge is not called,
    # so the scripted response is never consumed
    assert report.skipped == ["reflection needs at least 3 facts, found 1"]
    assert engine.count() == 1
