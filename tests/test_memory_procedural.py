"""tests/test_memory_procedural.py — P3-C: procedural memory (failures→skills).

Hermetic: tmp_path SQLite + HashingEmbedder + scripted loop fakes; one test
replays through the REAL AgentLoop (ScriptedGateway model + real
ToolRegistry/PolicyEngine) to pin that a replay is an ordinary kernel run —
same policy choke point, fresh trace, outcome tallied, regressed skills pruned.
"""

from __future__ import annotations

import asyncio
import json
import sqlite3
from dataclasses import dataclass, field

import pytest

from kernel.gateway import Message, Response
from kernel.loop import AgentLoop, LoopResult
from kernel.memory.engine import MemoryEngine, ProcedureRecord
from kernel.memory.procedural import (
    PRUNE_AFTER_FAILURES,
    OutcomeApplied,
    capture_procedure,
    prime_messages,
    recall_similar,
    record_outcome,
    register_procedural_tools,
    replay,
    should_prune,
)
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


@pytest.fixture()
def engine(tmp_path):
    eng = MemoryEngine(tmp_path / "memory.sqlite3")
    yield eng
    eng.close()


def make_proc(engine: MemoryEngine, name: str = "morning-report",
              summary: str | None = None) -> int:
    return capture_procedure(
        engine, name=name,
        steps=[{"tool": "file_read", "args": {"path": "report.md"}},
               {"tool": "email_send", "args": {"to": "team"}}],
        summary=summary or "read the report and email it to the team")


# ------------------------------------------------------ engine layer ----


def test_save_and_get_procedure_roundtrip(engine) -> None:
    pid = make_proc(engine)
    record = engine.get_procedure(pid)
    assert record is not None
    assert record.name == "morning-report"
    assert record.summary == "read the report and email it to the team"
    assert [s["tool"] for s in record.steps] == ["file_read", "email_send"]
    assert record.success_count == 0 and record.fail_count == 0
    assert record.updated_at > 0


def test_save_same_name_updates_in_place(engine) -> None:
    pid = make_proc(engine)
    again = engine.save_procedure(
        "morning-report",
        [{"tool": "file_read", "args": {"path": "v2.md"}}],
        "read versioned report and email it")
    assert again == pid
    record = engine.get_procedure(pid)
    assert record is not None
    assert record.summary.startswith("read versioned")
    assert len(record.steps) == 1
    # retrieval now keys on the NEW summary
    assert engine.search_procedures("versioned report")[0].record.id == pid


def test_save_procedure_validation(engine) -> None:
    with pytest.raises(ValueError):
        engine.save_procedure("  ", [{"tool": "x"}], "summary")
    with pytest.raises(ValueError):
        engine.save_procedure("name", [], "summary")
    with pytest.raises(ValueError):
        engine.save_procedure("name", [{"tool": "x"}], "   ")
    with pytest.raises(ValueError):
        engine.save_procedure("name", ["not-a-dict"], "summary")


def test_outcome_tally_records_success_and_failure(engine) -> None:
    pid = make_proc(engine)
    assert engine.record_procedure_outcome(pid, True) is True
    assert engine.record_procedure_outcome(
        pid, False, failure_note="file missing") is True
    record = engine.get_procedure(pid)
    assert record is not None
    assert record.success_count == 1 and record.fail_count == 1
    assert record.last_failure == "file missing"
    assert engine.record_procedure_outcome(pid, False) is True
    record = engine.get_procedure(pid)
    assert record is not None
    assert record.last_failure == "unspecified failure"
    assert engine.record_procedure_outcome(999, True) is False


def test_search_procedures_ranks_by_summary_similarity(engine) -> None:
    make_proc(engine, name="email-report",
              summary="read the report and email it to the team")
    make_proc(engine, name="plant-watering",
              summary="water the Monstera and pothos plants on the shelf")
    hits = engine.search_procedures("email the team report")
    assert hits and hits[0].record.name == "email-report"
    assert hits[0].score > 0.0
    assert engine.search_procedures("   ") == []


def test_search_procedures_skips_foreign_dimensions(engine) -> None:
    make_proc(engine)
    from kernel.memory.embedders import HashingEmbedder
    engine._embedder = HashingEmbedder(dim=64)
    assert engine.search_procedures("email the report to the team") == []


def test_prune_procedure_hard_deletes(engine) -> None:
    pid = make_proc(engine)
    assert engine.prune_procedure(pid) is True
    assert engine.prune_procedure(pid) is False
    assert engine.get_procedure(pid) is None
    assert engine.search_procedures("email the report to the team") == []
    rows = engine._conn.execute(
        "SELECT COUNT(*) FROM procedure_vectors").fetchone()[0]
    assert rows == 0                                   # cascade cleaned up


def test_pre_p3c_database_migrates_in_place(tmp_path) -> None:
    db = tmp_path / "legacy.sqlite3"
    conn = sqlite3.connect(str(db))
    conn.executescript("""
    CREATE TABLE procedures (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        steps_json TEXT NOT NULL DEFAULT '[]',
        success_count INTEGER NOT NULL DEFAULT 0,
        fail_count INTEGER NOT NULL DEFAULT 0
    );
    """)
    conn.execute(
        "INSERT INTO procedures (name, steps_json, success_count)"
        " VALUES ('old-skill', ?, 2)",
        (json.dumps([{"tool": "legacy_tool", "args": {}}]),),
    )
    conn.commit()
    conn.close()

    eng = MemoryEngine(db)
    try:
        records = eng.all_procedures()
        assert len(records) == 1
        assert records[0].name == "old-skill"
        assert records[0].steps[0]["tool"] == "legacy_tool"
        assert records[0].success_count == 2
        # re-saving the same skill upgrades it (summary + vector appear)
        pid = eng.save_procedure("old-skill",
                                 [{"tool": "legacy_tool", "args": {}}],
                                 "the legacy skill, now searchable")
        assert pid == records[0].id
        assert eng.search_procedures("legacy skill")[0].record.id == pid
    finally:
        eng.close()


# -------------------------------------------------- curation policy ----


def test_capture_procedure_validation(engine) -> None:
    with pytest.raises(ValueError):
        capture_procedure(engine, name="x", steps=[], summary="s")
    with pytest.raises(ValueError):
        capture_procedure(engine, name="x", steps=[{"args": {}}],
                          summary="s")
    with pytest.raises(ValueError):
        capture_procedure(engine, name="x",
                          steps=[{"tool": "y", "args": {}}], summary="")
    pid = capture_procedure(engine, name="x",
                            steps=[{"tool": "y", "args": {}}],
                            summary="step summary text")
    assert pid >= 1


def test_should_prune_never_worked_and_regressed_rules() -> None:
    def rec(successes: int, fails: int) -> ProcedureRecord:
        return ProcedureRecord(id=1, name="x", steps=(), summary="s",
                               success_count=successes, fail_count=fails)

    assert should_prune(rec(0, PRUNE_AFTER_FAILURES)) is True
    assert should_prune(rec(0, PRUNE_AFTER_FAILURES - 1)) is False
    assert should_prune(rec(1, 1)) is False
    # regressed: 1/4 = 0.25 <= 0.25 → pruned
    assert should_prune(rec(1, 3)) is True
    assert should_prune(rec(2, 2)) is False            # 0.5 > 0.25
    assert should_prune(rec(3, 1)) is False


def test_record_outcome_auto_prunes_never_worked(engine) -> None:
    pid = make_proc(engine)
    for i in range(PRUNE_AFTER_FAILURES - 1):
        applied = record_outcome(engine, pid, success=False,
                                 failure_note=f"attempt {i}")
        assert applied.pruned is False
    final = record_outcome(engine, pid, success=False,
                           failure_note="last straw")
    assert isinstance(final, OutcomeApplied)
    assert final.recorded is True and final.pruned is True
    assert engine.get_procedure(pid) is None


def test_record_outcome_keeps_healthy_skill(engine) -> None:
    pid = make_proc(engine)
    record_outcome(engine, pid, success=True)
    applied = record_outcome(engine, pid, success=False,
                             failure_note="transient")
    assert applied.pruned is False
    assert (applied.success_count, applied.fail_count) == (1, 1)
    record = engine.get_procedure(pid)
    assert record is not None                          # survived


def test_record_outcome_prunes_regressed_skill(engine) -> None:
    pid = make_proc(engine)
    record_outcome(engine, pid, success=True)          # 1/0
    for i in range(3):                                 # → 1/3 = 0.25 ≤ 0.25
        applied = record_outcome(engine, pid, success=False,
                                 failure_note=f"env changed {i}")
    assert applied.pruned is True
    assert engine.get_procedure(pid) is None


def test_record_outcome_unknown_id(engine) -> None:
    applied = record_outcome(engine, 4242, success=True)
    assert applied == OutcomeApplied(recorded=False, pruned=False)


# ------------------------------------------------- loop integration ----


def test_recall_and_prime_build_loop_transcript(engine) -> None:
    pid = make_proc(engine)
    hits = recall_similar(engine, "send the team the weekly report")
    assert hits and hits[0].record.id == pid
    messages = prime_messages(hits[0].record, "send the team the weekly report")
    assert [m.role for m in messages] == ["system", "user"]
    assert "morning-report" in messages[0].text
    assert "file_read" in messages[0].text and "email_send" in messages[0].text
    assert messages[1].text == "send the team the weekly report"


@dataclass
class FakeLoop:
    """LoopRunner fake: returns queued LoopResults, records prompts."""

    results: list[LoopResult]
    seen: list[list[Message]] = field(default_factory=list)

    async def run(self, messages):
        self.seen.append(list(messages))
        return self.results.pop(0)


def _loop_result(finish: str = "stop") -> LoopResult:
    return LoopResult(text="ok", steps=1, finish=finish)


def test_replay_success_tallies_outcome(engine) -> None:
    pid = make_proc(engine)
    loop = FakeLoop(results=[_loop_result("stop")])
    outcome = asyncio.run(replay(loop, engine, pid, "do the morning report"))
    assert outcome.ran and outcome.success and not outcome.pruned
    assert outcome.finish == "stop"
    assert outcome.detail["success_count"] == 1
    record = engine.get_procedure(pid)
    assert record is not None and record.success_count == 1
    # the loop saw the priming transcript
    assert "morning-report" in loop.seen[0][0].text


def test_replay_failure_records_note(engine) -> None:
    pid = make_proc(engine)
    loop = FakeLoop(results=[_loop_result("max_steps")])
    outcome = asyncio.run(replay(loop, engine, pid, "do the thing"))
    assert outcome.success is False and outcome.finish == "max_steps"
    record = engine.get_procedure(pid)
    assert record is not None
    assert record.fail_count == 1
    assert record.last_failure == "replay finished with max_steps"


def test_replay_verifier_overrides_finish(engine) -> None:
    pid = make_proc(engine)
    loop = FakeLoop(results=[_loop_result("stop")])
    outcome = asyncio.run(replay(loop, engine, pid, "do the thing",
                                 verify=lambda _r: False))
    assert outcome.success is False                    # stop ≠ verified
    loop2 = FakeLoop(results=[_loop_result("max_steps")])
    outcome2 = asyncio.run(replay(loop2, engine, pid, "do the thing",
                                  verify=lambda _r: True))
    assert outcome2.success is True                    # verified despite finish


def test_replay_prunes_regressed_skill_end_to_end(engine) -> None:
    pid = make_proc(engine)
    for i in range(PRUNE_AFTER_FAILURES - 1):
        loop = FakeLoop(results=[_loop_result("error")])
        outcome = asyncio.run(replay(loop, engine, pid, "do it"))
        assert outcome.pruned is False
    final_loop = FakeLoop(results=[_loop_result("error")])
    final = asyncio.run(replay(final_loop, engine, pid, "do it"))
    assert final.success is False and final.pruned is True
    assert engine.get_procedure(pid) is None


def test_replay_unknown_procedure_raises(engine) -> None:
    loop = FakeLoop(results=[])
    with pytest.raises(ValueError, match="does not exist"):
        asyncio.run(replay(loop, engine, 999, "anything"))


# ------------------------------------------------ real AgentLoop ----


@dataclass
class ScriptedGateway:
    turns: list[Response]
    seen: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages, tools=(), response_schema=None):
        self.seen.append(list(messages))
        return self.turns.pop(0)


def note_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="append_note", description="Append a note.",
              parameters={"type": "object",
                          "properties": {"text": {"type": "string"}},
                          "required": ["text"]},
              risk=RiskClass.READ)
    def append_note(call):
        return {"appended": call.args.get("text")}

    return reg


def test_replay_through_real_agent_loop(engine) -> None:
    """A replay is an ordinary kernel run: real loop, real policy/registry —
    the procedure script only primes the model, it never bypasses anything."""
    pid = capture_procedure(
        engine, name="note-greeting",
        steps=[{"tool": "append_note", "args": {"text": "hello"}}],
        summary="append a greeting note")
    gateway = ScriptedGateway(turns=[
        Response(text="Following the procedure.",
                 tool_calls=(ToolCall("c1", "append_note",
                                      {"text": "hello"}),)),
        Response(text="Greeting note appended as the procedure prescribes."),
    ])
    loop = AgentLoop(gateway, PolicyEngine(), note_registry(), max_steps=4)
    outcome = asyncio.run(replay(loop, engine, pid,
                                 "append the greeting note again"))
    assert outcome.success is True and outcome.pruned is False
    assert outcome.detail["tool_calls"] == 1
    assert outcome.detail["success_count"] == 1
    record = engine.get_procedure(pid)
    assert record is not None and record.success_count == 1


def test_procedure_recall_tool_is_read_risk(engine) -> None:
    pid = make_proc(engine, summary="water the Monstera and pothos plants")
    reg = ToolRegistry()
    register_procedural_tools(reg, engine)
    assert reg.risks()["procedure_recall"] is RiskClass.READ
    declarations = {d["name"]: d for d in reg.declarations()}
    assert "procedure_recall" in declarations
    result = asyncio.run(reg.execute(
        ToolCall("c1", "procedure_recall",
                 {"query": "water the plants on the shelf"})))
    assert result.ok and result.data
    top = result.data[0]
    assert top["id"] == pid
    assert top["name"] == "morning-report"
    assert top["steps"][0]["tool"] == "file_read"
    assert top["score"] > 0.0


def test_procedure_recall_tool_empty_query_is_clean(engine) -> None:
    reg = ToolRegistry()
    register_procedural_tools(reg, engine)
    result = asyncio.run(reg.execute(
        ToolCall("c2", "procedure_recall", {"query": "   "})))
    assert result.ok and result.data == []
