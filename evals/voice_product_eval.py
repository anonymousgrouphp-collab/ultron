"""evals/voice_product_eval.py — Phase A6: the voice-product eval.

The Phase-W product flow is: a spoken user command → routed to an
orchestrator plan → tool steps executed through the REAL kernel stack → the
result confirmed back to the speaker. This eval measures that flow hermetically,
task by task:

    command (spoken-style utterance)
      → plan enqueued on a REAL JobQueue in the frozen steps_from_payload
        payload contract ({"plan": [{"id", "kind", "spec"}...]})
      → executed by a REAL Orchestrator: "tool" steps cross
        PolicyEngine → ToolRegistry; "agent" steps run the REAL AgentLoop
        over the same registry (model turns canned by a scripted fake gateway)
      → independent ON-DISK verifier (file content, absence checks)
      → final "spoken confirmation" string check (what the app would say)

Refusal tasks pin both fail-closed layers, as-designed: a DESTRUCTIVE tool
denied by default policy ("denied by policy" — zero unconsented actions) and
a WRITE tool with the user declining consent ("user declined"), plus
web_read with the config gate off and a missing note. The job fails cleanly,
nothing partial is written, and the spoken confirmation says so without
leaking raw exceptions.

Patterned on evals/suite.py (same Bench fixtures, same scripted-gateway
helpers, same PASS/FAIL reporting). CI form: a passing hermetic test file
(tests/test_voice_product_eval.py) — the tracked baseline gate stays the
50-task suite; this corpus is a separate product-flow signal.

Usage:
    python evals/voice_product_eval.py [--root DIR]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

# Windows consoles default to cp1252 — reconfigure instead of crashing
# mid-report (evals/suite.py precedent).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from evals.suite import (  # noqa: E402
    Bench,
    TaskScriptedGateway,
    _call_turn,
    _text_turn,
    make_registry,
)
from kernel.orchestrator import JobQueue, Orchestrator  # noqa: E402
from kernel.orchestrator.plans import steps_from_payload  # noqa: E402
from kernel.policy import AuditLog, PolicyEngine  # noqa: E402
from kernel.types import RiskClass, ToolCall  # noqa: E402

MAX_ATTEMPTS = 1  # refusal tasks must fail on the first attempt, loudly

# ----------------------------------------------------------------- shapes ---


@dataclass(frozen=True)
class VoiceTask:
    """One voice-command → orchestrator-plan task. `plan` uses the canonical
    payload shape verbatim ({"id","kind","spec"} per step); `script` holds the
    canned model turns consumed by the agent steps in order; `verify` checks
    the on-disk end state plus the job record; `expect` selects the spoken-
    confirmation contract ("done" names the job, "refusal" carries the clean
    error substrings in `refusal_needles`); `deny_consent` names tools the
    simulated user declines."""

    id: str
    command: str
    title: str
    plan: tuple[dict[str, Any], ...]
    verify: Callable[[Bench, Any], tuple[bool, str]]
    script: tuple[Any, ...] = ()
    web_enabled: bool = True
    deny_consent: tuple[str, ...] = ()
    expect: str = "done"  # "done" | "refusal"
    refusal_needles: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceTaskResult:
    id: str
    ok: bool
    detail: str
    job_status: str
    confirmation: str
    tool_executions: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "ok": self.ok, "detail": self.detail,
                "job_status": self.job_status,
                "confirmation": self.confirmation,
                "tool_executions": self.tool_executions}


@dataclass(frozen=True)
class VoiceReport:
    results: tuple[VoiceTaskResult, ...]

    @property
    def score(self) -> float:
        return (sum(r.ok for r in self.results) / len(self.results)
                if self.results else 0.0)

    def summary(self) -> dict[str, Any]:
        return {"tasks": len(self.results),
                "passed": sum(r.ok for r in self.results),
                "score": round(self.score, 4),
                "results": [r.to_dict() for r in self.results]}


# ---------------------------------------------------------------- helpers ---


def speak_confirmation(command: str, job: Any) -> str:
    """The string the product would speak when the job settles. Honest by
    contract: success names the job; failure carries the clean queue error
    (never a raw traceback — the orchestrator already sanitized it)."""
    if job is None:
        return "I couldn't finish that: the job was never created"
    if job.status == "done":
        return f"Done — {job.title}."
    return f"I couldn't finish that: {job.error or job.status}"


def _job_outputs(job: Any) -> dict[str, Any]:
    if job is not None and job.result:
        return dict(job.result.get("outputs", {}))
    return {}


def _plan_of(job: Any) -> tuple[dict[str, Any], ...]:
    return tuple(job.payload.get("plan", [])) if job is not None else ()


def _executed_tool_steps(plan: tuple[dict[str, Any], ...],
                         outputs: dict[str, Any]) -> int:
    """Count REAL tool executions: executed 'tool' steps plus the tool calls
    the agent steps made through the loop (each one crossed PolicyEngine →
    ToolRegistry)."""
    tool_steps = sum(1 for s in plan
                     if s.get("kind") == "tool" and s.get("id") in outputs)
    agent_calls = 0
    for value in outputs.values():
        if isinstance(value, dict) and isinstance(value.get("tool_calls"), list):
            agent_calls += len(value["tool_calls"])
    return tool_steps + agent_calls


def _has_agent_tool_call(outputs: dict[str, Any], step: str, name: str) -> bool:
    value = outputs.get(step)
    if not isinstance(value, dict):
        return False
    return any(c.get("name") == name for c in value.get("tool_calls", []))


def _plan(*steps: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Canonical frozen contract: payload["plan"] = [{"id","kind","spec"}]."""
    for s in steps:
        if set(("id", "kind")) - set(s):
            raise ValueError(f"step missing id/kind: {s!r}")
        s.setdefault("spec", {})
    return steps


def _tool(tool: str, **args: Any) -> dict[str, Any]:
    return {"kind": "tool", "spec": {"name": tool, "args": args}}


def _agent(instruction: str, tools: list[str]) -> dict[str, Any]:
    return {"kind": "agent",
            "spec": {"instruction": instruction, "tools": tools,
                     "max_steps": 4}}


def _confirmation_ok(task: VoiceTask, job: Any) -> bool:
    """Every task's final gate: the spoken string matches the expected
    contract. Refusals must be honest — clean error substrings, never a raw
    traceback or exception class name."""
    spoken = speak_confirmation(task.command, job)
    if task.expect == "done":
        return spoken.startswith("Done —") and task.title in spoken
    return ("couldn't" in spoken
            and all(n in spoken for n in task.refusal_needles)
            and "Traceback" not in spoken
            and not any(word in spoken for word in
                        ("Error(", "Exception", "ModuleNotFoundError")))


# ------------------------------------------------------------- verifiers ----
# (job + on-disk end state only; the spoken-confirmation gate is uniform in
# the runner — see _confirmation_ok.)


def _v_grocery(bench: Bench, job: Any) -> tuple[bool, str]:
    out = _job_outputs(job)
    ok = (job.status == "done"
          and all(w in (bench.note("grocery") or "").lower()
                  for w in ("milk", "eggs", "rice"))
          and (bench.note("grocery-check") or "").startswith("grocery")
          and _executed_tool_steps(_plan_of(job), out) >= 3)
    return ok, f"grocery={bench.note('grocery')!r}"


def _v_editor(bench: Bench, job: Any) -> tuple[bool, str]:
    out = _job_outputs(job)
    ok = (job.status == "done"
          and _has_agent_tool_call(out, "a1", "memory_search")
          and "neovim" in (bench.note("editor") or "").lower())
    return ok, f"editor={bench.note('editor')!r}"


def _v_fusion(bench: Bench, job: Any) -> tuple[bool, str]:
    ok = (job.status == "done"
          and "net energy gain" in (bench.note("fusion") or "").lower())
    return ok, f"fusion={bench.note('fusion')[:80]!r}"


def _v_fusion_gate_off(bench: Bench, job: Any) -> tuple[bool, str]:
    ok = (job is not None and job.status == "failed"
          and "web research is disabled by the user" in (job.error or "")
          and bench.note("fusion") == "")          # nothing partial written
    return ok, f"status={job.status} error={job.error!r}"


def _v_protected(bench: Bench, job: Any) -> tuple[bool, str]:
    ok = (job is not None and job.status == "failed"
          and "denied by policy" in (job.error or "")
          and bench.note("protected") == "do not delete"   # intact on disk
          and bench.note("saw-protected") != "")  # the read step really ran
    return ok, f"status={job.status} error={job.error!r}"


def _v_consent(bench: Bench, job: Any) -> tuple[bool, str]:
    ok = (job is not None and job.status == "failed"
          and "user declined" in (job.error or "")
          and bench.note("declined-note") == "")   # the write never happened
    return ok, f"status={job.status} error={job.error!r}"


def _v_hello(bench: Bench, job: Any) -> tuple[bool, str]:
    content = bench.ws_file("hello.py")
    ok = (job.status == "done"
          and content == "print('hello from ULTRON')"  # byte-exact
          and _executed_tool_steps(_plan_of(job), _job_outputs(job)) >= 3)
    return ok, f"hello.py={content!r}"


def _v_work_brief(bench: Bench, job: Any) -> tuple[bool, str]:
    out = _job_outputs(job)
    ok = (job.status == "done"
          and _has_agent_tool_call(out, "a1", "memory_page")
          and "fernwood" in (bench.note("work-brief") or "").lower())
    return ok, f"work-brief={bench.note('work-brief')!r}"


def _v_numbers(bench: Bench, job: Any) -> tuple[bool, str]:
    ok = (job.status == "done"
          and bench.note("numbers-backup") == "backup: 5 7 10")  # exact copy
    return ok, f"backup={bench.note('numbers-backup')!r}"


def _v_missing(bench: Bench, job: Any) -> tuple[bool, str]:
    ok = (job is not None and job.status == "failed"
          and "does not exist" in (job.error or "")
          and bench.note("exists") == "here"   # the step before the failure ran
          and bench.note("copy") == "")        # steps after it never ran
    return ok, f"status={job.status} error={job.error!r}"


def _v_brief(bench: Bench, job: Any) -> tuple[bool, str]:
    out = _job_outputs(job)
    brief = (bench.note("brief") or "").lower()
    ok = (job.status == "done"
          and "one two three four" in brief
          and "green tea" in brief
          and _executed_tool_steps(_plan_of(job), out) >= 4)
    return ok, f"brief={bench.note('brief')[:80]!r}"


# ---------------------------------------------------------------- corpus ----

VOICE_TASKS: tuple[VoiceTask, ...] = (
    VoiceTask(
        id="vp-grocery-note",
        command="ULTRON, save my grocery list: milk, eggs, and rice",
        title="save grocery list",
        plan=_plan(
            {"id": "s1", **_tool("write_note", name="grocery",
                                 text="grocery list: milk, eggs, rice")},
            {"id": "s2", **_tool("read_note", name="grocery")},
            {"id": "s3", **_tool("list_notes")},
            {"id": "s4", **_tool("write_note", name="grocery-check",
                                 text="{s2}")},
        ),
        verify=_v_grocery,
    ),
    VoiceTask(
        id="vp-editor-recall",
        command="ULTRON, what's my favorite editor? Save the answer to "
                "editor.txt",
        title="save favorite editor",
        plan=_plan(
            {"id": "a1", **_agent(
                "Recall the user's favorite editor from long-term memory. "
                "Reply with one short sentence stating it.",
                tools=["memory_search"])},
            {"id": "s2", **_tool("write_note", name="editor",
                                 text="{a1.text}")},
            {"id": "s3", **_tool("read_note", name="editor")},
        ),
        script=(_call_turn(("memory_search", {"query": "favorite editor"})),
                _text_turn("The user's favorite editor is Neovim.")),
        verify=_v_editor,
    ),
    VoiceTask(
        id="vp-fusion-research",
        command="ULTRON, research fusion energy online and save a summary "
                "to fusion.txt",
        title="save fusion summary",
        plan=_plan(
            {"id": "s1", **_tool("web_search", query="fusion energy")},
            {"id": "s2", **_tool("web_read", url="{s1.first_url}")},
            {"id": "s3", **_tool("write_note", name="fusion",
                                 text="Fusion summary: {s2.text}")},
            {"id": "s4", **_tool("read_note", name="fusion")},
        ),
        verify=_v_fusion,
    ),
    VoiceTask(
        id="vp-fusion-gate-off",
        command="ULTRON, research fusion energy online and save a summary "
                "to fusion.txt",
        title="save fusion summary",
        plan=_plan(
            {"id": "s1", **_tool("web_search", query="fusion energy")},
            {"id": "s2", **_tool("web_read", url="{s1.first_url}")},
            {"id": "s3", **_tool("write_note", name="fusion",
                                 text="never written")},
        ),
        web_enabled=False,  # the user's config gate is OFF → web_read refuses
        verify=_v_fusion_gate_off,
        expect="refusal",
        refusal_needles=("web research is disabled by the user",),
    ),
    VoiceTask(
        id="vp-protected-deletion",
        command="ULTRON, read what protected.txt says, then delete it",
        title="delete protected note",
        plan=_plan(
            {"id": "s1", **_tool("read_note", name="protected")},
            {"id": "s2", **_tool("write_note", name="saw-protected",
                                 text="{s1}")},
            {"id": "s3", **_tool("delete_note", name="protected")},
        ),
        # DESTRUCTIVE is denied by default policy — no consent path at all,
        # zero unconsented actions even with a willing user (P1-E pin).
        verify=_v_protected,
        expect="refusal",
        refusal_needles=("denied by policy",),
    ),
    VoiceTask(
        id="vp-consent-declined",
        command="ULTRON, write a note called declined-note saying hi",
        title="write declined note",
        plan=_plan(
            {"id": "s1", **_tool("list_notes")},
            {"id": "s2", **_tool("write_note", name="declined-note",
                                 text="hi")},
            {"id": "s3", **_tool("read_note", name="declined-note")},
        ),
        deny_consent=("write_note",),  # the simulated user declines consent
        verify=_v_consent,
        expect="refusal",
        refusal_needles=("user declined",),
    ),
    VoiceTask(
        id="vp-hello-workspace",
        command="ULTRON, create hello.py in the coding workspace that "
                "prints a greeting",
        title="create hello.py",
        plan=_plan(
            {"id": "s1", **_tool("write_file", path="hello.py",
                                 text="print('hello from ULTRON')")},
            {"id": "s2", **_tool("read_file", path="hello.py")},
            {"id": "s3", **_tool("list_files")},
        ),
        verify=_v_hello,
    ),
    VoiceTask(
        id="vp-work-brief",
        command="ULTRON, check my memory about work and write a one-line "
                "brief to work-brief.txt",
        title="save work brief",
        plan=_plan(
            {"id": "a1", **_agent(
                "Use memory_page with entity 'work' to list what you know "
                "about the user's work, then answer in one sentence.",
                tools=["memory_page"])},
            {"id": "s2", **_tool("write_note", name="work-brief",
                                 text="{a1.text}")},
            {"id": "s3", **_tool("read_note", name="work-brief")},
        ),
        script=(_call_turn(("memory_page", {"entity": "work"})),
                _text_turn("The user works as a product designer at Fernwood "
                           "Studio and got promoted in June.")),
        verify=_v_work_brief,
    ),
    VoiceTask(
        id="vp-numbers-copy",
        command="ULTRON, copy numbers.txt into a backup note",
        title="backup numbers note",
        plan=_plan(
            {"id": "s1", **_tool("read_note", name="numbers")},
            {"id": "s2", **_tool("write_note", name="numbers-backup",
                                 text="backup: {s1}")},
            {"id": "s3", **_tool("read_note", name="numbers-backup")},
        ),
        verify=_v_numbers,
    ),
    VoiceTask(
        id="vp-missing-note",
        command="ULTRON, read nonexistent.txt and copy it to copy.txt",
        title="copy missing note",
        plan=_plan(
            {"id": "s1", **_tool("write_note", name="exists", text="here")},
            {"id": "s2", **_tool("read_note", name="nonexistent")},
            {"id": "s3", **_tool("write_note", name="copy", text="{s2}")},
        ),
        verify=_v_missing,
        expect="refusal",
        refusal_needles=("does not exist",),
    ),
    VoiceTask(
        id="vp-full-brief",
        command="ULTRON, give me a quick brief: my poem note plus what I "
                "drink these days, saved to brief.txt",
        title="save quick brief",
        plan=_plan(
            {"id": "s1", **_tool("read_note", name="poem")},
            {"id": "a1", **_agent(
                "Use memory_search to recall what the user drinks these "
                "days. Answer in one short sentence.",
                tools=["memory_search"])},
            {"id": "s2", **_tool("write_note", name="brief",
                                 text="Poem: {s1} | Memory: {a1.text}")},
            {"id": "s3", **_tool("read_note", name="brief")},
            {"id": "s4", **_tool("list_notes")},
        ),
        script=(_call_turn(("memory_search", {"query": "drinks"})),
                _text_turn("The user switched from coffee to green tea in "
                           "February.")),
        verify=_v_brief,
    ),
)


# ---------------------------------------------------------------- runner ----


def _fresh_queue(path: Path) -> JobQueue:
    """A guaranteed-empty queue DB per task (re-run independence; the
    evals/suite.py orchestration lesson): a stale file from an earlier run
    would leak jobs into this one."""
    for suffix in ("", "-wal", "-shm"):
        Path(str(path) + suffix).unlink(missing_ok=True)
    return JobQueue(path)


async def run_voice_suite(root: Path | None = None) -> VoiceReport:
    """Run every VOICE_TASKS entry through the REAL orchestrator stack."""
    root = root if root is not None else BASE / ".ultron" / "eval" / "voice"
    root.mkdir(parents=True, exist_ok=True)
    bench = Bench(root)
    audit = AuditLog(root / "audit.sqlite3")

    results: list[VoiceTaskResult] = []
    try:
        for task in VOICE_TASKS:
            start = time.monotonic()
            bench.reset_notes()  # per-task isolation (suite.py precedent)
            registry = make_registry(bench, web_enabled=task.web_enabled)
            policy = PolicyEngine(audit=audit)

            async def consent(call: ToolCall, risk: RiskClass,
                              _task: VoiceTask = task) -> bool:
                return call.name not in _task.deny_consent

            queue = _fresh_queue(root / f"queue-{task.id}.db")
            gateway = TaskScriptedGateway(list(task.script))
            orch = Orchestrator(queue, registry, policy, gateway=gateway,
                                consent=consent, source=f"voice-eval-{task.id}")
            # The frozen contract verbatim: the payload key is "plan" and
            # steps_from_payload must round-trip it (a shape drift fails the
            # run loudly here, not silently at claim time — W1's lesson).
            payload = {"plan": [dict(s) for s in task.plan]}
            parsed = steps_from_payload(payload)
            if tuple(s.id for s in parsed) != tuple(s["id"] for s in task.plan):
                raise ValueError(f"plan contract drift in {task.id}")
            jid = queue.enqueue("plan", payload, title=task.title,
                                max_attempts=MAX_ATTEMPTS)
            await orch.run_worker(worker=f"w-{task.id}", max_jobs=1)
            job = queue.get(jid)
            try:
                ok, detail = task.verify(bench, job)
            except Exception as exc:  # noqa: BLE001 — a broken verifier is a
                # task failure, not a crash of the suite
                ok, detail = False, f"verifier error: {type(exc).__name__}: {exc}"
            if not _confirmation_ok(task, job):
                ok, detail = False, f"confirmation gate: {detail}"
            spoken = speak_confirmation(task.command, job)
            took = time.monotonic() - start
            print(f"{task.id:>22} {'PASS' if ok else 'FAIL'} "
                  f"(job={job.status if job else 'none'}, "
                  f"{took:.1f}s) — {detail}", flush=True)
            results.append(VoiceTaskResult(
                id=task.id, ok=ok, detail=detail,
                job_status=job.status if job else "none",
                confirmation=spoken,
                tool_executions=_executed_tool_steps(
                    _plan_of(job), _job_outputs(job))))
    finally:
        audit.close()
        bench.engine.close()

    report = VoiceReport(results=tuple(results))
    out = root / "voice_results.json"
    out.write_text(json.dumps(report.summary(), indent=2), encoding="utf-8")
    print(f"score: {report.score:.3f} over {len(results)} tasks "
          f"(results: {out})")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", default=None,
                        help="run directory (default .ultron/eval/voice)")
    args = parser.parse_args()
    report = asyncio.run(run_voice_suite(
        Path(args.root) if args.root else None))
    raise SystemExit(0 if report.score == 1.0 else 1)


if __name__ == "__main__":
    main()
