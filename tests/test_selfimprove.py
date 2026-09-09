"""tests/test_selfimprove.py — P5-B: the self-improvement loop.

Hermetic (tmp_path SQLite + HashingEmbedder + scripted gateways), but the
runs go through the REAL AgentLoop → PolicyEngine → ToolRegistry stack
(the P3-C procedural tests' doctrine), so every policy/audit guarantee
applies to replays and captured skills.

The flagship test is the roadmap's Phase-5 sentence made executable:
"failures produce skills that make later runs pass" — a naive model fails
a task, the repair pass succeeds, the corrected script is captured, and a
SECOND naive model then passes the same task on its FIRST attempt via
skill replay (the harness that improves itself).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from kernel.bus import EventBus
from kernel.gateway import Message, Response
from kernel.loop import AgentLoop
from kernel.memory import (
    HashingEmbedder,
    MemoryEngine,
    SkillCaptureListener,
    capture_from_run,
    improve_run,
    task_slug,
)
from kernel.orchestrator import JobQueue, Orchestrator
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


# ------------------------------------------------------------- fixtures ----


@dataclass
class ScriptedGateway:
    """Fake Completer: pops queued Responses (the 'model'); a script that
    runs dry pops text-only stop turns (a 'naive' model that just talks)."""

    turns: list[Response]
    seen: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages, tools=(), response_schema=None):
        self.seen.append(list(messages))
        if self.turns:
            return self.turns.pop(0)
        return Response(text="I do not know how to do that.", finish="stop")


def call_turn(*calls: tuple[str, dict]) -> Response:
    return Response(
        text="",
        tool_calls=tuple(
            ToolCall(id=f"t-{i}-{name}", name=name, args=args, source="model")
            for i, (name, args) in enumerate(calls, 1)
        ),
        finish="tool_calls" if calls else "stop",
    )


def text_turn(text: str) -> Response:
    return Response(text=text, finish="stop")


@pytest.fixture()
def engine(tmp_path):
    eng = MemoryEngine(tmp_path / "memory.sqlite3",
                       embedder=HashingEmbedder())
    yield eng
    eng.close()


def counter_registry(tmp_path) -> tuple[ToolRegistry, dict[str, int]]:
    """The task surface: a 'counter' the agent must raise to 3 exactly via
    bump calls — verifiable end-state, no wording dependence."""
    state: dict[str, int] = {"count": 0}
    reg = ToolRegistry()

    @reg.tool(name="bump", description="Add n to the counter.",
              parameters={"type": "object",
                          "properties": {"n": {"type": "integer"}},
                          "required": ["n"]},
              risk=RiskClass.WRITE)
    def bump(call):
        state["count"] += int(call.args.get("n", 0))
        return {"count": state["count"]}

    @reg.tool(name="read_count", description="Read the counter value.",
              parameters={"type": "object", "properties": {}},
              risk=RiskClass.READ)
    def read_count(call):
        return {"count": state["count"]}

    return reg, state


def make_loop(gateway, reg, tmp_path, **kw) -> AgentLoop:
    async def yes(call: ToolCall, risk: RiskClass) -> bool:
        return True
    return AgentLoop(gateway, PolicyEngine(), reg, consent=yes,
                    source="test", **kw)


def verify_count_3(state: dict[str, int]):
    """The task verifier: success iff the counter reached exactly 3."""
    def verify(_result) -> bool:
        return state["count"] == 3
    return verify


# ---------------------------------------------------- flagship: the loop ---


def test_failure_produces_skill_that_makes_later_run_pass(tmp_path, engine):
    """THE Phase-5 sentence, executable: naive model fails → repair pass
    succeeds → script captured → a second naive model passes the SAME task
    on its FIRST attempt via skill replay (the harness that improves
    itself)."""
    reg, state = counter_registry(tmp_path)
    task = "raise the counter to exactly 3 using bump calls"

    # pass 1: the naive turn has no tool calls (fails verification); the
    # repair pass reruns with the corrected script queued in the same model
    naive_then_repair = ScriptedGateway([
        text_turn("I will just tell you: the counter is 3 now."),
        call_turn(("bump", {"n": 2})),
        call_turn(("bump", {"n": 1})),
        text_turn("done"),
    ])
    outcome = asyncio.run(improve_run(
        make_loop(naive_then_repair, reg, tmp_path), engine, task,
        verify=verify_count_3(state),
    ))
    assert outcome.success and outcome.attempt == 2   # naive fail + repair
    assert outcome.captured is not None               # the failure → skill

    skill = engine.get_procedure(outcome.captured)
    assert skill.success_count == 1
    assert [s["tool"] for s in skill.steps] == ["bump", "bump"]
    assert [s["args"]["n"] for s in skill.steps] == [2, 1]

    # pass 2: a FRESH naive-capable model on the SAME task — the skill
    # primes it, and it passes on the FIRST attempt
    state["count"] = 0
    second = ScriptedGateway([
        call_turn(("bump", {"n": 2})),
        call_turn(("bump", {"n": 1})),
        text_turn("done"),
    ])
    second_outcome = asyncio.run(improve_run(
        make_loop(second, reg, tmp_path), engine, task,
        verify=verify_count_3(state),
    ))
    assert second_outcome.success
    assert second_outcome.used_skill == skill.id   # replayed the skill
    assert second_outcome.attempt == 1             # first attempt this time
    # the replay's priming system message carries the known-good script
    primed = second.seen[0][0].text
    assert "bump" in primed and "known-good" in primed.lower()
    # replay tallied: the skill now has 2 successes
    assert engine.get_procedure(skill.id).success_count == 2


def test_repair_pass_success_is_captured(tmp_path, engine):
    """A repaired success stores the CORRECTED script (with its first
    success tallied) — the naive failure is not part of the skill."""
    reg, state = counter_registry(tmp_path)
    task = "set the counter to exactly 3"

    # gateway: naive text-only turn, then the repair turn with correct calls
    gw = ScriptedGateway([
        text_turn("The counter is at 3."),            # naive: fails
        call_turn(("bump", {"n": 3})),                # repair: correct
        text_turn("done"),
    ])
    outcome = asyncio.run(improve_run(
        make_loop(gw, reg, tmp_path), engine, task,
        verify=verify_count_3(state),
    ))
    assert outcome.success and outcome.attempt == 2
    assert outcome.captured is not None
    skill = engine.get_procedure(outcome.captured)
    assert skill.success_count == 1
    assert [s["tool"] for s in skill.steps] == ["bump"]
    assert skill.steps[0]["args"]["n"] == 3


def test_fresh_success_captures_and_replays(tmp_path, engine):
    """A first-try success captures the skill; the next identical task
    replays it instead of running fresh."""
    reg, state = counter_registry(tmp_path)
    task = "bump the counter up to three"

    gw = ScriptedGateway([
        call_turn(("bump", {"n": 1})),
        call_turn(("bump", {"n": 1})),
        call_turn(("bump", {"n": 1})),
        text_turn("done"),
    ])
    first = asyncio.run(improve_run(
        make_loop(gw, reg, tmp_path), engine, task,
        verify=verify_count_3(state),
    ))
    assert first.success and first.attempt == 1
    assert first.captured is not None

    state["count"] = 0
    gw2 = ScriptedGateway([
        call_turn(("bump", {"n": 3})),
        text_turn("done"),
    ])
    second = asyncio.run(improve_run(
        make_loop(gw2, reg, tmp_path), engine, task,
        verify=verify_count_3(state),
    ))
    assert second.success and second.used_skill == first.captured


def test_text_only_success_captures_nothing(tmp_path, engine):
    """A verified success with no tool calls is not a procedure (there is
    no script to replay) — capture_from_run returns None."""
    reg, state = counter_registry(tmp_path)
    # pre-set the state so a text-only answer 'verifies'
    state["count"] = 3
    gw = ScriptedGateway([text_turn("It is already at 3.")])
    outcome = asyncio.run(improve_run(
        make_loop(gw, reg, tmp_path), engine, "report the counter",
        verify=lambda r: state["count"] == 3,
    ))
    assert outcome.success and outcome.captured is None
    assert engine.all_procedures() == []


def test_replay_regression_prunes_and_falls_through_to_fresh(tmp_path, engine):
    """A skill that no longer works: replay fails, tallies, regressed-skill
    pruning kicks in (3 straight fails), and improve_run falls through to a
    fresh attempt instead of dying on the stale skill."""
    from kernel.memory.procedural import record_outcome

    reg, state = counter_registry(tmp_path)
    task = "get the counter to 3"
    # seed a skill with one success (so improve_run will replay it)
    seeded = capture_from_run_engine_helper(engine, task,
                                            [("bump", {"n": 3})])
    # two failed replays already recorded → next replay is the 3rd fail
    record_outcome(engine, seeded, success=False, failure_note="stale")
    record_outcome(engine, seeded, success=False, failure_note="stale")

    state["count"] = 0
    # replay path: a gateway whose script bumps by 1 three times (works
    # fresh but the seeded 'bump 3' script... we make replay fail by
    # priming text that leads nowhere) — simplest: replay's gateway model
    # refuses to act, then the fresh pass works
    gw = ScriptedGateway([
        text_turn("I cannot do that."),              # replayed pass: fail
        call_turn(("bump", {"n": 1})),               # fresh pass: works
        call_turn(("bump", {"n": 1})),
        call_turn(("bump", {"n": 1})),
        text_turn("done"),
    ])
    outcome = asyncio.run(improve_run(
        make_loop(gw, reg, tmp_path), engine, task,
        verify=verify_count_3(state),
    ))
    assert outcome.success
    assert outcome.used_skill is None or outcome.used_skill != seeded
    # the 3rd replay failure pruned the stale skill
    assert engine.get_procedure(seeded) is None


def capture_from_run_engine_helper(engine, task, calls):
    """Seed a skill directly from (name, args) pairs with one success."""
    from kernel.memory import capture_procedure, record_outcome
    steps = [{"tool": name, "args": args} for name, args in calls]
    proc_id = capture_procedure(engine, name=task_slug(task), steps=steps,
                                summary=task)
    record_outcome(engine, proc_id, success=True)
    return proc_id


# ------------------------------------------------------------ capture ------


def test_capture_from_run_upserts_by_name(tmp_path, engine):
    """Same task → same skill (name UPSERT): tallies preserved, script
    refreshed — a refreshed script must re-prove itself or pruning
    reclaims it."""
    from kernel.types import ToolCall as TC

    def fake_result(*calls):
        class R:  # minimal LoopResult-shaped object
            tool_calls = tuple(TC(id=f"c{i}", name=n, args=a, source="model")
                               for i, (n, a) in enumerate(calls, 1))
        return R()

    pid1 = capture_from_run(engine, "send the weekly report",
                             fake_result(("mail_send", {"to": "team"})))
    pid2 = capture_from_run(engine, "send the weekly report",
                            fake_result(("mail_send", {"to": "team"}),
                                        ("log_write", {"where": "sent"})))
    assert pid1 == pid2
    record = engine.get_procedure(pid1)
    assert len(record.steps) == 2          # refreshed script
    assert record.success_count == 2        # both verified runs tallied


def test_task_slug_is_deterministic_and_bounded() -> None:
    assert task_slug("Mail the Weekly Report to the Team!") == \
        "mail-the-weekly-report-to-the"
    assert task_slug("A") == "a"
    assert task_slug("!!!") == "unnamed-task"
    assert task_slug("one two three four five six seven") == \
        "one-two-three-four-five-six"


# ---------------------------------------------------- orchestrator bus -----


def test_skill_listener_captures_completed_agent_jobs(tmp_path, engine):
    """The P2-C wiring: a completed agent job's tool-call script becomes a
    skill keyed by the job title; jobs that didn't finish cleanly (a
    StepFailure-failed job) capture nothing."""
    from kernel.types import ToolResult

    reg, _ = counter_registry(tmp_path)

    @reg.tool(name="tick", description="Marker tool.",
              parameters={"type": "object", "properties": {},
                         "required": []},
              risk=RiskClass.WRITE)
    def tick(call):
        return "ticked"

    @reg.tool(name="boom", description="Always fails.",
              parameters={"type": "object", "properties": {},
                         "required": []},
              risk=RiskClass.READ)
    def boom(call):
        return ToolResult.fail(call, "expected failure")

    queue = JobQueue(tmp_path / "queue.db")
    bus = EventBus()
    listener = SkillCaptureListener(engine)
    listener.attach(bus)

    async def yes(call: ToolCall, risk: RiskClass) -> bool:
        return True

    # the clean job's model: tool call then stop; the failing job is a
    # TOOL step whose tool returns a clean fail (StepFailure → job failed)
    clean_gw = ScriptedGateway([call_turn(("tick", {})),
                                text_turn("done")])
    orch_clean = Orchestrator(queue, reg, PolicyEngine(), bus=bus,
                              gateway=clean_gw, consent=yes)
    ok_id = queue.enqueue("plan", {"plan": [
        {"id": "a", "kind": "agent",
         "spec": {"instruction": "call tick then stop", "max_steps": 4}}]},
        title="run the weekly tick job")
    asyncio.run(orch_clean.run_worker(worker="w1", max_jobs=1))
    orch_tool = Orchestrator(queue, reg, PolicyEngine(), bus=bus,
                            consent=yes)
    fail_id = queue.enqueue("plan", {"plan": [
        {"id": "a", "kind": "tool", "spec": {"name": "boom", "args": {}}}]},
        title="the failing job", max_attempts=1)
    asyncio.run(orch_tool.run_worker(worker="w2", max_jobs=1))

    assert queue.get(ok_id).status == "done"
    assert queue.get(fail_id).status == "failed"

    skills = engine.all_procedures()
    assert len(skills) == 1                 # only the completed job captured
    assert skills[0].summary == "run the weekly tick job"
    assert [s["tool"] for s in skills[0].steps] == ["tick"]
    assert skills[0].success_count == 1
    assert listener.captured == [skills[0].id]
    # recall by the job's task text finds it
    hits = engine.search_procedures("weekly tick")
    assert hits and hits[0].record.id == skills[0].id


def test_skill_listener_skips_non_stop_agent_runs(tmp_path, engine):
    """An agent step that hit max_steps (finish != 'stop') completes its
    JOB but is not a proven procedure — nothing is captured from it."""
    reg, _ = counter_registry(tmp_path)

    @reg.tool(name="tick", description="Marker tool.",
              parameters={"type": "object", "properties": {},
                         "required": []},
              risk=RiskClass.WRITE)
    def tick(call):
        return "ticked"

    queue = JobQueue(tmp_path / "queue.db")
    bus = EventBus()
    listener = SkillCaptureListener(engine)
    listener.attach(bus)

    async def yes(call: ToolCall, risk: RiskClass) -> bool:
        return True

    # an unbounded tool-call model: every completion asks for another tick,
    # so the LOOP's max_steps bound is what ends it (finish=max_steps)
    class LoopingGateway:
        async def complete(self, messages, tools=(), response_schema=None):
            return call_turn(("tick", {}))

    orch = Orchestrator(queue, reg, PolicyEngine(), bus=bus,
                        gateway=LoopingGateway(), consent=yes)
    queue.enqueue("plan", {"plan": [
        {"id": "a", "kind": "agent",
         "spec": {"instruction": "tick forever", "max_steps": 2}}]},
        title="the never-ending job")
    asyncio.run(orch.run_worker(worker="w1", max_jobs=1))
    job = queue.list()[0]
    agent_output = job.result["outputs"]["a"]
    assert agent_output["finish"] == "max_steps"   # the precondition

    skills = engine.all_procedures()
    assert skills == []
    assert listener.captured == []


def test_improve_run_requires_real_arguments(engine) -> None:
    with pytest.raises(ValueError):
        asyncio.run(improve_run(
            loop=None, engine=engine, task="   ",
            verify=lambda r: True))  # type: ignore[arg-type]
