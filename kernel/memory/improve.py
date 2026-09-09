"""kernel/memory/improve.py — P5-B: the self-improvement loop (roadmap §4
Phase 5: "failures produce skills that make later runs pass").

Composes the P3-C primitives (capture / recall / replay / tally / prune)
into one task-driven loop, plus the orchestrator wiring that turns
completed agent jobs into skills automatically:

- `improve_run()` — one task, three escalating strategies:
    1. REPLAY: a proven skill for a similar task exists → prime the loop
       with its known-good script (policy, consent and audit apply exactly
       as in any kernel run); `procedural.replay` tallies the outcome and
       auto-prunes on regression.
    2. FRESH: no viable skill (none recalled, or the replay failed) → run
       the task plainly.
    3. REPAIR: a failed fresh attempt is fed back to the model as an
       observation and the task rerun (the loop's replan-on-error
       doctrine). A repaired success is the Voyager pattern realized: the
       FAILURE produced the corrected script, and `capture_from_run` saves
       that script as the skill.
- `capture_from_run()` — store a verified run's tool-call script as a
  skill (name-UPSERT: same task → same skill, tallies kept; a refreshed
  script must re-prove itself or pruning reclaims it). The capturing run
  IS a verified execution, so it is tallied as the skill's first success.
- `SkillCaptureListener` — bus wiring for the P2-C job lifecycle:
  completed agent jobs' scripts become skills keyed by their job title.
  Failed jobs capture nothing — pruning counts real replay attempts only
  (procedural.py), and a failed job is not a failed replay.

Nothing here is model-callable: skills are captured from verified
outcomes (the P3-C rule). Kernel code — stdlib + kernel imports only.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from kernel.bus import EventBus
from kernel.gateway.base import Message
from kernel.memory.engine import MemoryEngine
from kernel.memory.procedural import (
    LoopRunner,
    Verifier,
    capture_procedure,
    recall_similar,
    replay,
)

__all__ = [
    "ImproveOutcome",
    "RepairRunner",
    "SkillCaptureListener",
    "capture_from_run",
    "improve_run",
    "task_slug",
]

RepairRunner = Callable[[LoopRunner, str, Any], Awaitable[Any]]


@dataclass(frozen=True)
class ImproveOutcome:
    """One self-improving task run (JSON-able via `detail`)."""

    task: str
    success: bool
    finish: str
    attempt: int            # 1 = first pass (replay or fresh), 2+ = repair
    used_skill: int | None   # procedure id the run was primed with
    captured: int | None     # procedure id captured/refreshed by this run
    pruned: bool             # a replayed skill was pruned (regression)
    detail: dict[str, Any]


def task_slug(task: str) -> str:
    """Deterministic procedure name from the task text (first six words,
    kebab-case) — the dedupe key: the same task always updates the same
    skill instead of accumulating near-duplicates."""
    words = re.findall(r"[a-z0-9]+", task.lower())[:6]
    return "-".join(words) if words else "unnamed-task"


def capture_from_run(
    engine: MemoryEngine,
    task: str,
    result: Any,
    *,
    name: str | None = None,
) -> int | None:
    """Store a VERIFIED run's tool-call script as a skill. Returns the
    procedure id, or None when the run made no tool calls (a text-only
    answer is not a procedure). The capturing run passed verification, so
    it is tallied as the skill's first success — a fresh skill is
    immediately replayable by `improve_run`."""
    calls = list(getattr(result, "tool_calls", ()) or ())
    if not calls:
        return None
    steps = [{"tool": call.name, "args": dict(call.args)} for call in calls]
    proc_id = capture_procedure(
        engine, name=name or task_slug(task), steps=steps, summary=task)
    engine.record_procedure_outcome(proc_id, success=True)
    return proc_id


async def _default_repair(loop: LoopRunner, task: str,
                          failed: Any) -> Any:
    """Rerun the task with the failure observed (v0 repair: the error text
    is the model's signal to try differently — same doctrine as the loop's
    replan-on-error)."""
    finish = getattr(failed, "finish", "error")
    observation = (
        f"Your previous attempt at this task failed (finish: {finish}). "
        "It did not accomplish the goal. Try a different approach, verify "
        "each step, and make sure the end state is correct."
    )
    return await loop.run([
        Message(role="user", text=task),
        Message(role="user", text=observation),
    ])


async def improve_run(
    loop: LoopRunner,
    engine: MemoryEngine,
    task: str,
    *,
    verify: Verifier,
    name: str | None = None,
    repair_attempts: int = 1,
    repair: RepairRunner | None = None,
) -> ImproveOutcome:
    """Run one task through the self-improvement loop:

    1. recall a proven skill (≥1 past success) for this task → replay it;
       success tallies, regression auto-prunes;
    2. otherwise (or after a failed replay) run fresh;
    3. on fresh failure, repair (default: rerun with the failure observed);
       a repaired success is captured — the failure produced the skill.

    Every success (fresh or repaired) refreshes the skill via
    `capture_from_run` — name-UPSERT, tallies preserved.
    """
    if not task.strip():
        raise ValueError("task text is required")
    if repair_attempts < 0:
        raise ValueError("repair_attempts must be >= 0")
    do_repair: RepairRunner = repair if repair is not None else _default_repair

    hits = recall_similar(engine, task)
    skill = next((h for h in hits if h.record.success_count > 0), None)
    if skill is not None:
        outcome = await replay(loop, engine, skill.record.id, task,
                               verify=verify)
        if outcome.success:
            return ImproveOutcome(
                task=task, success=True, finish=outcome.finish, attempt=1,
                used_skill=skill.record.id, captured=None,
                pruned=outcome.pruned, detail=dict(outcome.detail))
        # replay failed (and tallied; a regressed skill was pruned) — fall
        # through to a fresh attempt before giving up

    result = await loop.run([Message(role="user", text=task)])
    attempt = 1
    while True:
        if verify(result):
            captured = capture_from_run(engine, task, result, name=name)
            return ImproveOutcome(
                task=task, success=True,
                finish=str(getattr(result, "finish", "stop")), attempt=attempt,
                used_skill=None, captured=captured, pruned=False,
                detail={"tool_calls": len(getattr(result, "tool_calls",
                                                  ()) or ())})
        if attempt > repair_attempts:
            return ImproveOutcome(
                task=task, success=False,
                finish=str(getattr(result, "finish", "error")),
                attempt=attempt, used_skill=None, captured=None, pruned=False,
                detail={"tool_calls": len(getattr(result, "tool_calls",
                                                  ()) or ())})
        attempt += 1
        result = await do_repair(loop, task, result)


class SkillCaptureListener:
    """Bus wiring for the P2-C orchestrator job lifecycle (P5-B): every
    COMPLETED agent job's tool-call script becomes a skill, keyed by the
    job title (the natural-language task — the retrieval key).

    Subscribes to `job.started` (remembers each job's title) and
    `job.completed` (captures every agent-step output that finished
    cleanly). Failed jobs capture nothing: pruning counts real replay
    attempts only, and a failed job is not a failed replay — recording one
    would prune unrelated skills on title similarity.
    """

    def __init__(self, engine: MemoryEngine) -> None:
        self._engine = engine
        self._titles: dict[str, str] = {}
        self.captured: list[int] = []

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("job.started", self._on_started)
        bus.subscribe("job.completed", self._on_completed)

    async def _on_started(self, event: Any) -> None:
        payload = getattr(event, "payload", None) or {}
        job_id = str(payload.get("job", ""))
        title = str(payload.get("title", "")).strip()
        if job_id and title:
            self._titles[job_id] = title

    async def _on_completed(self, event: Any) -> None:
        payload = getattr(event, "payload", None) or {}
        job_id = str(payload.get("job", ""))
        title = self._titles.pop(job_id, "")
        if not title:
            return
        outputs = payload.get("outputs")
        if not isinstance(outputs, dict):
            return
        for step_output in outputs.values():
            if not isinstance(step_output, dict):
                continue
            if step_output.get("finish") != "stop":
                continue  # a max-steps/errored agent run is not a skill
            calls = step_output.get("tool_calls")
            if not isinstance(calls, list) or not calls:
                continue
            steps = [
                {"tool": str(call.get("name", "")),
                 "args": dict(call.get("args") or {})}
                for call in calls if isinstance(call, dict)
            ]
            if not steps:
                continue
            proc_id = capture_procedure(
                self._engine, name=task_slug(title), steps=steps,
                summary=title)
            self._engine.record_procedure_outcome(proc_id, success=True)
            self.captured.append(proc_id)
