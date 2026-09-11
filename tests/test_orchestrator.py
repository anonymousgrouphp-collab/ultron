"""P2-C tests: durable queue, checkpoint/resume, orchestrator, subagents.

Hermetic: tmp SQLite queues, fake tools, a scripted fake gateway for agent
steps, and ONE real subprocess test where a worker is KILLED mid-job and a
second worker resumes the job from its checkpoint (the Phase-2 restart
guarantee at unit scale).
"""

import asyncio
import os
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from kernel.gateway import Response
from kernel.orchestrator import (
    JobQueue,
    Orchestrator,
    resolve_template,
    scoped_registry,
    spawn_subagent,
    steps_from_payload,
    wait_for_job,
)
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall, ToolResult

# The restart-test worker: count_up APPENDS to a file so the assertion can
# prove step 1 executed exactly once across a kill + resume.
WORKER_SRC = textwrap.dedent('''
    import asyncio
    import sys
    import time
    from pathlib import Path
    from kernel.orchestrator import JobQueue, Orchestrator
    from kernel.policy import PolicyEngine
    from kernel.tools import ToolRegistry
    from kernel.types import RiskClass

    db, root = Path(sys.argv[1]), Path(sys.argv[2])
    root.mkdir(parents=True, exist_ok=True)
    reg = ToolRegistry()

    @reg.tool(name="count_up", description="appends to the invocation log",
              parameters={"type": "object", "properties": {}},
              risk=RiskClass.READ)
    def count_up(call):
        log = root / "invocations.txt"
        with open(log, "a", encoding="utf-8") as f:
            f.write("x\\n")
        return {"n": sum(1 for _ in open(log, encoding="utf-8"))}

    @reg.tool(name="wait_for_go", description="waits until <root>/go exists",
              parameters={"type": "object", "properties": {}},
              risk=RiskClass.READ, timeout_s=30.0)
    def wait_for_go(call):
        deadline = time.time() + 25.0
        while time.time() < deadline:
            if (root / "go").exists():
                return {"go": True}
            time.sleep(0.1)
        return {"go": False}

    @reg.tool(name="final_word", description="returns the end marker",
              parameters={"type": "object", "properties": {}},
              risk=RiskClass.READ)
    def final_word(call):
        return {"word": "done"}

    queue = JobQueue(db)
    orch = Orchestrator(queue, reg, PolicyEngine(), lease_s=1.0, poll_s=0.2)
    asyncio.run(orch.run_worker(worker="w-restart", max_jobs=None))
''')


def make_registry(state: dict) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="count_up", description="counts invocations",
              parameters={"type": "object", "properties": {}}, risk=RiskClass.READ)
    def count_up(call):
        state["count"] = state.get("count", 0) + 1
        return {"n": state["count"]}

    @reg.tool(name="fail_tool", description="always fails cleanly",
              parameters={"type": "object", "properties": {}}, risk=RiskClass.READ)
    def fail_tool(call):
        return ToolResult.fail(call, "no can do")

    @reg.tool(name="crash_tool", description="always crashes",
              parameters={"type": "object", "properties": {}}, risk=RiskClass.READ)
    def crash_tool(call):
        raise RuntimeError("boom")

    return reg


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=args, source="test")


def plan_payload(*steps: dict) -> dict:
    return {"plan": list(steps)}


def step(sid: str, tool: str) -> dict:
    return {"id": sid, "kind": "tool", "spec": {"name": tool}}


class FakeGateway:
    """Two-turn agent: call the scoped tool, then finish with text."""

    def __init__(self, first_call: ToolCall) -> None:
        self._first_call = first_call
        self.turns = 0

    async def complete(self, messages, tools=(), response_schema=None):
        self.turns += 1
        if self.turns == 1:
            return Response(text="I will call the tool.",
                             tool_calls=(self._first_call,), finish="tool_calls")
        return Response(text="All done, sir.", finish="stop")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- queue

def test_01_enqueue_claim_complete_cycle():
    q = JobQueue()
    jid = q.enqueue("plan", plan_payload(step("s1", "count_up")), title="t")
    job = q.get(jid)
    assert job is not None and job.status == "queued" and job.kind == "plan"
    claimed = q.claim("w1", lease_s=30)
    assert claimed is not None and claimed.id == jid
    assert claimed.status == "running" and claimed.attempts == 1
    assert q.claim("w2", lease_s=30) is None  # nothing else queued
    assert q.complete(jid, {"outputs": {"s1": {"n": 1}}}, "w1") is True
    done = q.get(jid)
    assert done.status == "done" and done.result == {"outputs": {"s1": {"n": 1}}}


def test_02_lease_expiry_recovers_with_checkpoint():
    q = JobQueue()
    jid = q.enqueue("plan", plan_payload(step("s1", "count_up")))
    claimed = q.claim("w1", lease_s=0.05)
    assert claimed is not None
    assert q.checkpoint(jid, {"done": ["s1"], "outputs": {"s1": {"n": 1}}}, "w1")
    time.sleep(0.1)  # lease expires — worker w1 "crashed" after step 1
    recovered = q.claim("w2", lease_s=30)
    assert recovered is not None and recovered.id == jid
    assert recovered.leased_by == "w2" and recovered.attempts == 2
    assert recovered.done_steps() == {"s1"}
    assert recovered.checkpoint_outputs() == {"s1": {"n": 1}}


def test_03_attempts_exhausted_on_recovery_fails_the_job():
    q = JobQueue()
    jid = q.enqueue("plan", plan_payload(step("s1", "crash_tool")), max_attempts=1)
    q.claim("w1", lease_s=0.05)
    time.sleep(0.1)
    assert q.claim("w2", lease_s=30) is None  # exhausted → failed, not re-claimed
    job = q.get(jid)
    assert job.status == "failed" and "exhausted" in (job.error or "")


def test_04_fail_with_and_without_retry():
    q = JobQueue()
    a = q.enqueue("plan", plan_payload(step("s1", "crash_tool")), max_attempts=2)
    b = q.enqueue("plan", plan_payload(step("s1", "fail_tool")), max_attempts=2)
    qa = q.claim("w", lease_s=30)
    qb = q.claim("w", lease_s=30)
    assert qa and qb
    assert q.fail(a, "boom", worker="w", retry=True) is True
    assert q.get(a).status == "queued"  # attempts 1 < max 2 → requeued
    assert q.fail(b, "no can do", worker="w", retry=False) is True
    assert q.get(b).status == "failed"


def test_05_cancel_terminal_protection():
    q = JobQueue()
    jid = q.enqueue("plan", plan_payload(step("s1", "count_up")))
    assert q.cancel(jid) is True
    assert q.get(jid).status == "canceled"
    assert q.cancel(jid) is False  # already terminal
    assert q.claim("w", lease_s=30) is None  # canceled is not claimable


def test_06_priority_order():
    q = JobQueue()
    low = q.enqueue("plan", plan_payload(step("s1", "x")), priority=0)
    high = q.enqueue("plan", plan_payload(step("s1", "x")), priority=5)
    claimed = q.claim("w", lease_s=30)
    assert claimed.id == high
    claimed2 = q.claim("w", lease_s=30)
    assert claimed2.id == low


# ---------------------------------------------------------------- plans

def test_07_step_parsing_and_templates():
    steps = steps_from_payload(plan_payload(
        {"id": "a", "kind": "tool", "spec": {"name": "x"}},
        {"id": "b", "kind": "agent", "spec": {"instruction": "go"}},
    ))
    assert [s.id for s in steps] == ["a", "b"]
    with pytest.raises(ValueError, match="duplicate"):
        steps_from_payload(plan_payload({"id": "a", "kind": "tool"},
                                        {"id": "a", "kind": "tool"}))
    with pytest.raises(ValueError, match="plan"):
        steps_from_payload({"plan": []})

    outputs = {"a": {"results": [{"url": "https://x"}, {"url": "https://y"}]}}
    assert resolve_template("{a.results.0.url}", outputs) == "https://x"
    assert resolve_template({"u": "{a.results.1.url}"}, outputs) == \
        {"u": "https://y"}
    assert resolve_template(42, outputs) == 42
    with pytest.raises(ValueError, match="not resolvable"):
        resolve_template("{a.results.9.url}", outputs)


# ---------------------------------------------------------------- runner

def test_08_plan_runs_end_to_end_with_checkpoints():
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)
    jid = q.enqueue("plan", plan_payload(step("s1", "count_up"),
                                         step("s2", "count_up")))
    processed = run(orch.run_worker(worker="w", max_jobs=1))
    assert processed == 1
    job = q.get(jid)
    assert job.status == "done"
    assert job.result == {"outputs": {"s1": {"n": 1}, "s2": {"n": 2}}}
    assert set(job.done_steps()) == {"s1", "s2"}


def test_09_expected_failure_fails_without_retry():
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)
    jid = q.enqueue("plan", plan_payload(step("s1", "fail_tool")), max_attempts=3)
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "failed" and job.error == "no can do"
    assert job.attempts == 1  # deterministic failure — no retry


def test_10_crash_retries_then_fails():
    """A handler crash OUTSIDE the registry's clean-result protection (e.g. a
    broken custom kind) is transient: retried up to max_attempts, then failed."""
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)

    async def explode(step, outputs, ctx):
        raise RuntimeError("boom")

    orch.register_kind("explode", explode)
    jid = q.enqueue("plan", plan_payload(
        {"id": "s", "kind": "explode", "spec": {}}), max_attempts=2)
    run(orch.run_worker(worker="w", max_jobs=1))   # attempt 1 → requeued
    run(orch.run_worker(worker="w", max_jobs=1))   # attempt 2 → failed
    job = q.get(jid)
    assert job.status == "failed" and "RuntimeError: boom" in (job.error or "")
    assert job.attempts == 2


def test_11_resume_executes_only_remaining_steps():
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)
    jid = q.enqueue("plan", plan_payload(step("s1", "count_up"),
                                         step("s2", "count_up")))
    claimed = q.claim("w-crash", lease_s=0.05)
    assert claimed is not None
    # worker "crashed" after step 1 with its checkpoint persisted
    q.checkpoint(jid, {"done": ["s1"], "outputs": {"s1": {"n": 1}}}, "w-crash")
    time.sleep(0.1)
    run(orch.run_worker(worker="w2", max_jobs=1))
    job = q.get(jid)
    assert job.status == "done"
    assert job.result["outputs"]["s1"] == {"n": 1}  # restored from checkpoint
    assert state["count"] == 1  # s1 never re-executed (else 2)


def test_12_events_flow_on_the_bus():
    state: dict = {}
    from kernel import EventBus
    bus = EventBus()
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), bus=bus,
                        lease_s=30)
    q.enqueue("plan", plan_payload(step("s1", "count_up")))
    run(orch.run_worker(worker="w", max_jobs=1))
    types = [e.type for e in bus.history]
    assert "job.started" in types and "job.completed" in types
    assert "job.step" in types


def test_13_agent_step_runs_a_scoped_loop():
    state: dict = {}
    q = JobQueue()
    registry = make_registry(state)

    @registry.tool(name="greet", description="greets",
                   parameters={"type": "object",
                               "properties": {"who": {"type": "string"}}},
                   risk=RiskClass.READ)
    def greet(call):
        return {"hi": call.args["who"]}

    gateway = FakeGateway(ToolCall(id="t1", name="greet",
                                   args={"who": "sir"}, source="model"))
    orch = Orchestrator(q, registry, PolicyEngine(), gateway=gateway, lease_s=30)
    jid = spawn_subagent(q, instruction="Greet sir.",
                         tools=["greet", "count_up"], max_steps=4)
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "done"
    out = job.result["outputs"]["agent"]
    assert out["finish"] == "stop" and out["text"] == "All done, sir."
    assert out["tool_calls"][0]["name"] == "greet"
    assert any(t["kind"] == "tool" for t in out["trace"])


def test_14_agent_step_without_gateway_fails_clean():
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)
    jid = q.enqueue("plan", plan_payload(
        {"id": "a", "kind": "agent", "spec": {"instruction": "x"}}))
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "failed" and "gateway" in (job.error or "")


def test_15_unknown_step_kind_fails_clean():
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)
    jid = q.enqueue("plan", plan_payload(
        {"id": "a", "kind": "teleport", "spec": {}}))
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "failed"


def test_16_custom_step_kind():
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)

    async def shout(step, outputs, ctx):
        return {"echo": str(step.spec.get("word", "")) + "!"}

    orch.register_kind("shout", shout)
    jid = q.enqueue("plan", plan_payload(
        {"id": "s", "kind": "shout", "spec": {"word": "hi"}}))
    run(orch.run_worker(worker="w", max_jobs=1))
    assert q.get(jid).result["outputs"]["s"] == {"echo": "hi!"}


def test_17_scoped_registry_rejects_unknown_tools():
    state: dict = {}
    registry = make_registry(state)
    scoped = scoped_registry(registry, ["count_up"])
    assert scoped.names() == ("count_up",)
    with pytest.raises(ValueError, match="unknown tool"):
        scoped_registry(registry, ["nope"])


def test_18_spawn_and_wait_roundtrip():
    state: dict = {}
    q = JobQueue()
    registry = make_registry(state)
    gateway = FakeGateway(ToolCall(id="t1", name="count_up",
                                   args={}, source="model"))
    orch = Orchestrator(q, registry, PolicyEngine(), gateway=gateway, lease_s=30)
    jid = spawn_subagent(q, instruction="count once", tools=["count_up"])

    async def _go():
        await orch.run_worker(worker="w", max_jobs=1)
        return await wait_for_job(q, jid, timeout_s=10, poll_s=0.05)

    job = run(_go())
    assert job.status == "done"
    assert job.result["outputs"]["agent"]["finish"] == "stop"


def test_19_wait_for_job_timeout():
    q = JobQueue()
    jid = q.enqueue("plan", plan_payload(step("s1", "count_up")))  # never run
    with pytest.raises(TimeoutError):
        run(wait_for_job(q, jid, timeout_s=0.2, poll_s=0.05))


# ---------------------------------------------------------------- restart

def test_20_worker_killed_mid_job_job_survives_and_resumes(tmp_path: Path):
    """The Phase-2 restart guarantee at unit scale: kill a REAL worker
    subprocess mid-job, then let a fresh one resume from the checkpoint."""
    db = tmp_path / "queue.db"
    root = tmp_path / "root"
    root.mkdir()
    worker = tmp_path / "worker.py"
    worker.write_text(WORKER_SRC, encoding="utf-8")
    env = {**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parent.parent)}

    q = JobQueue(db)
    jid = q.enqueue("plan", plan_payload(
        step("s1", "count_up"), step("s2", "wait_for_go"),
        step("s3", "final_word")), title="restart demo", max_attempts=3)

    def spawn(tag: str) -> subprocess.Popen:
        err = open(tmp_path / f"{tag}.err", "wb")
        return subprocess.Popen(
            [sys.executable, str(worker), str(db), str(root)],
            env=env, cwd=str(tmp_path), stdout=subprocess.DEVNULL, stderr=err)

    def err_tail(tag: str) -> str:
        p = tmp_path / f"{tag}.err"
        return p.read_text(encoding="utf-8", errors="replace")[-500:] \
            if p.exists() else ""

    # worker 1: run until step 1 is checkpointed, then KILL it mid-step-2
    proc1 = spawn("w1")
    deadline = time.time() + 30
    while time.time() < deadline:
        job = q.get(jid)
        if job and "s1" in job.done_steps():
            break
        time.sleep(0.1)
    else:
        proc1.kill()
        pytest.fail(f"worker never checkpointed step 1; stderr: {err_tail('w1')}")
    proc1.kill()
    proc1.wait(timeout=10)
    mid = q.get(jid)
    assert mid.status == "running"          # died mid-job
    assert "s2" not in mid.done_steps()     # step 2 never checkpointed

    # release step 2 and let a FRESH worker resume from the checkpoint
    (root / "go").write_text("go", encoding="utf-8")
    time.sleep(1.3)                         # lease (1s) must expire first
    proc2 = spawn("w2")
    try:
        deadline = time.time() + 30
        while time.time() < deadline:
            job = q.get(jid)
            if job and job.status == "done":
                break
            time.sleep(0.1)
        else:
            pytest.fail(
                "resumed worker never finished the job; "
                f"status={q.get(jid).status}; stderr: {err_tail('w2')}")
    finally:
        proc2.kill()
        proc2.wait(timeout=10)

    job = q.get(jid)
    assert job.attempts == 2                # claimed once per worker
    outs = job.result["outputs"]
    assert outs["s1"]["n"] == 1             # invocations log: s1 ran EXACTLY once
    assert (root / "invocations.txt").read_text(encoding="utf-8") == "x\n"
    assert outs["s2"] == {"go": True}
    assert outs["s3"] == {"word": "done"}


# ---------------------------------------------------------------- W1 enqueue

def test_21_w1_enqueue_normalizes_both_step_shapes_end_to_end():
    """Phase W1: Orchestrator.enqueue is the app-facing convenience — it must
    emit the payload the frozen worker parses ('plan' key, id/kind/spec).
    Regression: the first cut wrapped steps under a 'steps' key, which
    steps_from_payload rejects — every enqueued job failed at claim time."""
    state: dict = {}
    q = JobQueue()
    orch = Orchestrator(q, make_registry(state), PolicyEngine(), lease_s=30)

    jid = orch.enqueue(
        [
            {"kind": "tool", "path": "count_up", "args": {}},           # shorthand
            {"id": "s2", "kind": "tool",
             "spec": {"name": "count_up", "args": {}}},                 # canonical
        ],
        title="w1 shapes",
    )
    job = q.get(jid)                     # payload round-trips the frozen parser
    assert [s.id for s in steps_from_payload(job.payload)] == ["step-1", "s2"]

    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "done"
    assert job.result == {"outputs": {"step-1": {"n": 1}, "s2": {"n": 2}}}


def test_22_w1_enqueue_rejects_unusable_steps():
    q = JobQueue()
    orch = Orchestrator(q, ToolRegistry(), PolicyEngine(), lease_s=30)
    with pytest.raises(ValueError):
        orch.enqueue([])                                   # no steps at all
    with pytest.raises(ValueError):
        orch.enqueue([{"kind": "tool"}])                   # shorthand needs path
    with pytest.raises(ValueError):
        orch.enqueue([{"spec": {"name": "x"}}])            # canonical needs kind
