"""kernel/memory/consolidation.py — P3-A: the consolidation job (research/04 §6).

Idle-time maintenance for the memory engine (§6.4: scheduled when the machine
is idle — a Task Scheduler hook calls `consolidate`; the kernel never taxes a
live thread with it). Three passes, mem0/Generative-Agents style:

1. **Extraction** (§6.3): a session-end transcript goes to the write-policy
   judge (policy.propose_ops, one structured gateway call) and the returned
   ADD/UPDATE/DELETE/NOOP ops are applied to the engine.
2. **Decay** (§6.6): importance decays exponentially (half-life); facts below
   the floor or past `expires_at` are tombstoned — audit + undo, never a hard
   delete from a job.
3. **Reflection** (§6.2): a cluster of recent facts is offered to a second
   structured gateway call that may synthesize ONE higher-level insight,
   stored with a `reflection:[ids]` source_ref back to its inputs.

Like the agent loop, a judge failure is data, not an exception: errors land in
the ConsolidationReport and the engine is left untouched. One
`memory.consolidated` bus event carries the summary when a bus is provided.
"""

from __future__ import annotations

import json
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from kernel.bus import EventBus
from kernel.gateway.base import Gateway, GatewayError, Message
from kernel.memory.engine import MemoryEngine
from kernel.memory.policy import (
    WritePolicy,
    propose_ops,
    render_facts,
    strip_json_fences,
)
from kernel.types import Event

REFLECT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "reflection": {
            "type": "string",
            "description": "One higher-level insight that is true of the "
                           "memories together and not already stated by any "
                           "single one. Empty string if none is warranted.",
        },
        "importance": {
            "type": "number",
            "description": "0.0-1.0 importance of the insight.",
        },
    },
    "required": ["reflection", "importance"],
}

_REFLECT_SYSTEM_PROMPT = (
    "You are ULTRON's reflection job. Given a set of related long-term "
    "memories, either synthesize ONE higher-level insight they support "
    "together (a pattern, habit, or conclusion — never a restatement of a "
    "single memory) or return an empty reflection. Ground the insight in the "
    "given memories; never invent facts."
)


@dataclass
class ConsolidationReport:
    """What one consolidation pass did — the audit trail and bus payload."""

    added: list[int] = field(default_factory=list)
    updated: list[int] = field(default_factory=list)
    deleted: list[int] = field(default_factory=list)      # DELETE op → tombstone
    reflections: list[int] = field(default_factory=list)
    decayed: list[int] = field(default_factory=list)      # importance lowered
    expired: list[int] = field(default_factory=list)      # past expires_at
    decayed_out: list[int] = field(default_factory=list)  # below the floor
    noops: int = 0
    rejected: list[tuple[str, str]] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        """JSON-able report for the bus event and the eval trace."""
        return {
            "added": list(self.added),
            "updated": list(self.updated),
            "deleted": list(self.deleted),
            "reflections": list(self.reflections),
            "decayed": list(self.decayed),
            "expired": list(self.expired),
            "decayed_out": list(self.decayed_out),
            "noops": self.noops,
            "rejected": [list(pair) for pair in self.rejected],
            "skipped": list(self.skipped),
            "errors": list(self.errors),
        }


def _render_op(op: Any) -> str:
    """Compact rendering of a rejected op for the report (bounded)."""
    text = json.dumps(op, sort_keys=True, default=str)
    return text if len(text) <= 200 else text[:197] + "..."


class Consolidator:
    """Runs the write policy + decay + reflection against a MemoryEngine."""

    def __init__(
        self,
        engine: MemoryEngine,
        gateway: Gateway,
        policy: WritePolicy | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._engine = engine
        self._gateway = gateway
        self._policy = policy if policy is not None else WritePolicy()
        self._bus = bus

    # -------------------------------------------------- extraction ----

    async def extract(
        self,
        transcript: Sequence[Message],
        *,
        source_ref: str | None = None,
        dedupe_limit: int = 20,
    ) -> ConsolidationReport:
        """Session-end extraction: transcript → judge ops → engine (§6.3)."""
        report = ConsolidationReport()
        await self._extract_into(report, transcript, source_ref=source_ref,
                                 dedupe_limit=dedupe_limit)
        return report

    async def _extract_into(
        self,
        report: ConsolidationReport,
        transcript: Sequence[Message],
        *,
        source_ref: str | None,
        dedupe_limit: int,
    ) -> None:
        existing = self._engine.active_facts()[:dedupe_limit]
        proposed = await propose_ops(
            self._gateway, list(transcript), existing, self._policy)
        if proposed.error is not None:
            report.errors.append(proposed.error)
            return
        report.rejected.extend(proposed.rejected)
        for op in proposed.ops:
            self._apply_op(report, op, source_ref)

    def _apply_op(
        self,
        report: ConsolidationReport,
        op: Any,
        source_ref: str | None,
    ) -> None:
        if op.op == "NOOP":
            report.noops += 1
            return
        if op.op == "ADD":
            report.added.append(self._engine.remember(
                op.content, entity=op.entity, topic=op.topic,
                importance=0.5 if op.importance is None else op.importance,
                source_ref=source_ref or "consolidation",
            ))
            return
        if op.op == "UPDATE":
            target = op.target_id
            assert target is not None  # WritePolicy.validate guarantees this
            if self._engine.update_fact(
                target, content=op.content or None, entity=op.entity or None,
                topic=op.topic or None, importance=op.importance,
            ):
                report.updated.append(target)
            else:
                report.rejected.append((_render_op(op), "update target not found"))
            return
        if op.op == "DELETE":
            target = op.target_id
            assert target is not None  # WritePolicy.validate guarantees this
            if self._engine.tombstone(
                target, op.justification or "consolidation DELETE"):
                report.deleted.append(target)
            else:
                report.rejected.append(
                    (_render_op(op), "delete target not found or already retired"))

    # ------------------------------------------------------- decay ----

    def decay(
        self,
        *,
        now: float | None = None,
        half_life_days: float = 30.0,
        floor: float = 0.05,
    ) -> ConsolidationReport:
        """Exponential importance decay + hard expiry (§6.6). Tombstones are
        recorded with reasons so `restore` can undo a job's decision."""
        report = ConsolidationReport()
        self._decay_into(report, now=now, half_life_days=half_life_days,
                         floor=floor)
        return report

    def _decay_into(
        self,
        report: ConsolidationReport,
        *,
        now: float | None,
        half_life_days: float,
        floor: float,
    ) -> None:
        if half_life_days <= 0:
            report.errors.append("half_life_days must be positive")
            return
        current = time.time() if now is None else now
        for fact in self._engine.active_facts():
            fact_id = int(fact["id"])
            expires_at = fact["expires_at"]
            if expires_at is not None and float(expires_at) <= current:
                if self._engine.tombstone(fact_id, "expired"):
                    report.expired.append(fact_id)
                continue
            last_run = fact.get("last_decayed_at") or fact["known_at"]
            elapsed_days = max(0.0, (current - float(last_run)) / 86400.0)
            if elapsed_days <= 0.0:
                continue
            decayed = float(fact["importance"]) * (
                0.5 ** (elapsed_days / half_life_days))
            if decayed < floor:
                if self._engine.tombstone(fact_id, "decayed below floor"):
                    report.decayed_out.append(fact_id)
            elif float(fact["importance"]) - decayed > 1e-6:
                # skip sub-epsilon noise so a fresh fact is never "decayed"
                if self._engine.set_importance(fact_id, decayed, last_decayed_at=current):
                    report.decayed.append(fact_id)

    # -------------------------------------------------- reflection ----

    async def reflect(
        self,
        *,
        entity: str | None = None,
        window: int = 10,
        min_facts: int = 3,
        default_importance: float = 0.6,
    ) -> ConsolidationReport:
        """Generative-Agents reflection (§6.2): the most recent `window` facts
        (optionally one entity's) may yield one synthesized insight."""
        report = ConsolidationReport()
        await self._reflect_into(report, entity=entity, window=window,
                                 min_facts=min_facts,
                                 default_importance=default_importance)
        return report

    async def _reflect_into(
        self,
        report: ConsolidationReport,
        *,
        entity: str | None,
        window: int,
        min_facts: int,
        default_importance: float,
    ) -> None:
        facts = self._engine.active_facts()
        if entity is not None:
            facts = [f for f in facts if f.get("entity") == entity]
        facts = facts[:window]
        if len(facts) < min_facts:
            report.skipped.append(
                f"reflection needs at least {min_facts} facts, found {len(facts)}")
            return
        facts_text = render_facts([
            {k: f.get(k) for k in ("id", "entity", "topic", "content")}
            for f in facts
        ])
        prompt = (
            f"MEMORIES{' about ' + entity if entity else ''}:\n"
            f"{facts_text}\n\n"
            "Synthesize one higher-level insight, or return an empty "
            "reflection if these memories do not support one."
        )
        try:
            response = await self._gateway.complete(
                [Message(role="system", text=_REFLECT_SYSTEM_PROMPT),
                 Message(role="user", text=prompt)],
                response_schema=REFLECT_SCHEMA,
            )
        except GatewayError as exc:
            report.errors.append(f"reflection call failed: {exc}")
            return
        try:
            data = json.loads(strip_json_fences(response.text))
        except json.JSONDecodeError:
            report.errors.append("reflection judge returned non-JSON output")
            return
        if not isinstance(data, dict):
            report.errors.append("reflection judge output was not an object")
            return
        text = str(data.get("reflection") or "").strip()
        if not text:
            report.skipped.append("judge synthesized no insight")
            return
        try:
            importance = float(data.get("importance", default_importance))
        except (TypeError, ValueError):
            importance = default_importance
        importance = min(1.0, max(0.0, importance))
        source_ids = sorted(int(f["id"]) for f in facts)
        report.reflections.append(self._engine.remember(
            text, entity=entity or "", topic="reflection",
            importance=importance, source_ref=f"reflection:{source_ids}",
        ))

    # ------------------------------------------------- orchestration --

    async def consolidate(
        self,
        transcript: Sequence[Message] | None = None,
        *,
        source_ref: str | None = None,
        run_decay: bool = True,
        run_reflect: bool = True,
        half_life_days: float = 30.0,
        floor: float = 0.05,
        now: float | None = None,
    ) -> ConsolidationReport:
        """The idle-time job (§6.4): extraction → decay → reflection, one
        report, one `memory.consolidated` bus event when a bus is wired."""
        report = ConsolidationReport()
        if transcript is not None:
            await self._extract_into(report, transcript, source_ref=source_ref,
                                     dedupe_limit=20)
        if run_decay:
            self._decay_into(report, now=now, half_life_days=half_life_days,
                             floor=floor)
        if run_reflect:
            await self._reflect_into(report, entity=None, window=10,
                                     min_facts=3, default_importance=0.6)
        if self._bus is not None:
            await self._bus.publish(Event(
                type="memory.consolidated",
                payload=report.summary(),
                source="memory.consolidation",
            ))
        return report


__all__ = ["REFLECT_SCHEMA", "ConsolidationReport", "Consolidator"]
