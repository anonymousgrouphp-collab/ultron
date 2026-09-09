"""kernel/memory/procedural.py — P3-C: procedural memory (research/04 §7).

Failures become skills (the Voyager pattern, adapted): when a multi-step job
succeeds, its tool-call script + summary are captured as a `procedure`; the
agent retrieves "similar past run" before planning (`recall_similar` /
`procedure_recall` tool); a replay primes the P1-G loop with the known-good
script and runs it like any other loop pass — the same consent/audit choke
point applies, and every replay is a fresh eval-trace (research/05 §7).

Skills that regress get pruned (§7): `record_outcome` applies the deterministic
pruning policy — a skill that never worked (≥ `prune_after_failures` failures,
zero successes) or one whose success rate collapsed (≤ 25% over ≥ 4 attempts)
is hard-deleted. Unlike facts, a stale script has no audit value worth
keeping, so pruning is a delete, not a tombstone.

This module is the kernel API; wiring capture into the orchestrator's job
lifecycle is P2-C. Nothing here is model-callable except the READ-risk
`procedure_recall` tool — skills are captured from verified outcomes, never
written from a model request (same rule as the fact stores).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from kernel.gateway.base import Message
from kernel.memory.engine import MemoryEngine, ProcedureHit, ProcedureRecord
from kernel.tools import ToolRegistry
from kernel.types import RiskClass

# pruning policy (deterministic, testable — no clocks involved)
PRUNE_AFTER_FAILURES = 3       # never worked: this many fails, zero successes
PRUNE_MIN_ATTEMPTS = 4         # regression: evaluate rate from this many tries
PRUNE_MAX_SUCCESS_RATE = 0.25  # regression: at or below this → prune


@dataclass(frozen=True)
class OutcomeApplied:
    """Result of one recorded attempt, including the prune decision."""

    recorded: bool
    pruned: bool
    success_count: int = 0
    fail_count: int = 0


@dataclass(frozen=True)
class ReplayOutcome:
    """What one replay via the agent loop did (JSON-able via the result)."""

    procedure_id: int
    ran: bool
    success: bool
    pruned: bool
    finish: str
    detail: dict[str, Any]


class LoopRunner(Protocol):
    """Structural type satisfied by the P1-G AgentLoop (and test fakes)."""

    async def run(self, messages: Sequence[Message]) -> Any: ...


Verifier = Callable[[Any], bool]


def capture_procedure(
    engine: MemoryEngine,
    *,
    name: str,
    steps: Sequence[dict[str, Any]],
    summary: str,
) -> int:
    """Capture a completed job's tool-call script as a skill. `steps` are
    {"tool": name, "args": {...}} dicts in execution order; `summary` is what
    retrieval embeds ("mail the weekly report to the team", not raw JSON)."""
    if not steps:
        raise ValueError("a procedure needs at least one step")
    for step in steps:
        if not isinstance(step, dict) or not str(step.get("tool", "")).strip():
            raise ValueError("each step needs a non-empty 'tool' key")
    return engine.save_procedure(name, list(steps), summary)


def recall_similar(
    engine: MemoryEngine,
    task_text: str,
    *,
    k: int = 3,
) -> list[ProcedureHit]:
    """Similar past runs for the task at hand (the loop consults this before
    planning; the `procedure_recall` tool exposes it to the model)."""
    return engine.search_procedures(task_text, k=max(1, min(k, 10)))


def should_prune(record: ProcedureRecord) -> bool:
    """The regression policy, as a pure predicate over the tally:
    never-worked — at least PRUNE_AFTER_FAILURES failures, zero successes;
    regressed — at least PRUNE_MIN_ATTEMPTS attempts with a success rate at
    or below PRUNE_MAX_SUCCESS_RATE."""
    attempts = record.success_count + record.fail_count
    if record.success_count == 0 and record.fail_count >= PRUNE_AFTER_FAILURES:
        return True
    return (attempts >= PRUNE_MIN_ATTEMPTS
            and record.success_count / attempts <= PRUNE_MAX_SUCCESS_RATE)


def record_outcome(
    engine: MemoryEngine,
    proc_id: int,
    *,
    success: bool,
    failure_note: str | None = None,
) -> OutcomeApplied:
    """Tally one attempt and prune the skill if the policy says so."""
    if not engine.record_procedure_outcome(proc_id, success,
                                           failure_note=failure_note):
        return OutcomeApplied(recorded=False, pruned=False)
    record = engine.get_procedure(proc_id)
    if record is not None and should_prune(record):
        engine.prune_procedure(proc_id)
        return OutcomeApplied(recorded=True, pruned=True,
                              success_count=record.success_count,
                              fail_count=record.fail_count)
    fresh = engine.get_procedure(proc_id)
    return OutcomeApplied(
        recorded=True, pruned=False,
        success_count=fresh.success_count if fresh else 0,
        fail_count=fresh.fail_count if fresh else 0,
    )


def prime_messages(record: ProcedureRecord, task_text: str) -> list[Message]:
    """Build the loop-priming transcript: the known-good script as context,
    the current task as the ask. Replay re-derives parameters from the live
    environment — the script is guidance, not a blind macro."""
    step_lines = "\n".join(
        f"  {i + 1}. {step.get('tool', '?')}({step.get('args', {})})"
        for i, step in enumerate(record.steps)
    )
    system = (
        "You have completed this kind of task before. Known-good procedure "
        f"'{record.name}' ({record.success_count} past successes):\n"
        f"{step_lines}\n"
        "Adapt it to the current task: re-check every parameter against the "
        "live environment instead of replaying blind. If the script no "
        "longer fits, deviate and say so."
    )
    return [Message(role="system", text=system),
            Message(role="user", text=task_text)]


async def replay(
    loop: LoopRunner,
    engine: MemoryEngine,
    proc_id: int,
    task_text: str,
    *,
    verify: Verifier | None = None,
) -> ReplayOutcome:
    """Replay a skill through the agent loop: prime with the stored script,
    run the normal plan→act→observe cycle (same policy/consent/audit as any
    run), then tally the outcome. Success = `verify(result)` when given,
    else `result.finish == "stop"`."""
    record = engine.get_procedure(proc_id)
    if record is None:
        raise ValueError(f"procedure {proc_id} does not exist")
    result = await loop.run(prime_messages(record, task_text))
    if verify is not None:
        success = bool(verify(result))
    else:
        success = getattr(result, "finish", "error") == "stop"
    note = None if success else (
        f"replay finished with {getattr(result, 'finish', 'error')}")
    applied = record_outcome(engine, proc_id, success=success,
                             failure_note=note)
    return ReplayOutcome(
        procedure_id=proc_id,
        ran=True,
        success=success,
        pruned=applied.pruned,
        finish=getattr(result, "finish", "error"),
        detail={
            "steps": len(record.steps),
            "success_count": applied.success_count,
            "fail_count": applied.fail_count,
            "tool_calls": len(getattr(result, "tool_calls", ()) or ()),
        },
    )


def register_procedural_tools(registry: ToolRegistry,
                              engine: MemoryEngine) -> None:
    """Expose skill recall as a READ-risk kernel tool. Saving/pruning stays
    programmatic — a model request never writes procedural memory."""

    @registry.tool(
        name="procedure_recall",
        description="Search ULTRON's procedural memory for similar past "
                    "tasks. Returns known-good tool-call scripts (with their "
                    "success/failure tallies) worth adapting or replaying.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "the task at hand, in natural "
                                         "language"},
                "k": {"type": "integer",
                      "description": "maximum procedures to return"},
            },
            "required": ["query"],
        },
        risk=RiskClass.READ,
    )
    def procedure_recall(call):
        hits = recall_similar(
            engine, str(call.args.get("query", "")),
            k=max(1, min(int(call.args.get("k", 3)), 10)),
        )
        return [
            {
                "id": hit.record.id,
                "name": hit.record.name,
                "summary": hit.record.summary,
                "steps": list(hit.record.steps),
                "success_count": hit.record.success_count,
                "fail_count": hit.record.fail_count,
                "score": round(hit.score, 4),
            }
            for hit in hits
        ]


__all__ = [
    "PRUNE_AFTER_FAILURES",
    "PRUNE_MAX_SUCCESS_RATE",
    "PRUNE_MIN_ATTEMPTS",
    "LoopRunner",
    "OutcomeApplied",
    "ReplayOutcome",
    "Verifier",
    "capture_procedure",
    "prime_messages",
    "recall_similar",
    "record_outcome",
    "register_procedural_tools",
    "replay",
    "should_prune",
]
