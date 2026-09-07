"""kernel/memory/policy.py — P3-A: the memory write policy (research/04 §6).

The hard part of memory is that not everything deserves to be remembered
(§6.5). The write path is: transcript + existing facts → ONE structured
Gateway call (the "judge", mem0's extract→consolidate pipeline §6.3) →
validated MemoryOps. The judge is the only LLM here and it speaks through
the P1-C gateway (Kill List: no LLM call outside the gateway, no model
strings in this file). Everything the model returns is untrusted input:
ops are normalized, validated against the policy, importance-gated, and
budget-capped. Parse/validation failures become `rejected` entries or a
clean `error` — nothing ever raises to the caller.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from kernel.gateway.base import Gateway, GatewayError, Message

OPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "ops": {
            "type": "array",
            "description": "Memory write operations proposed from the transcript.",
            "items": {
                "type": "object",
                "properties": {
                    "op": {
                        "type": "string",
                        "enum": ["ADD", "UPDATE", "DELETE", "NOOP"],
                        "description": "ADD new memory, UPDATE replace an "
                                       "existing memory's content, DELETE retire "
                                       "a contradicted/false memory, NOOP keep "
                                       "everything as is.",
                    },
                    "target_id": {
                        "type": "integer",
                        "description": "Existing memory id — required for "
                                       "UPDATE and DELETE, omit otherwise.",
                    },
                    "content": {
                        "type": "string",
                        "description": "The memory text (ADD/UPDATE) or empty "
                                       "for DELETE/NOOP.",
                    },
                    "entity": {
                        "type": "string",
                        "description": "Who/what the memory is about (person, "
                                       "project, category). Empty if none.",
                    },
                    "topic": {
                        "type": "string",
                        "description": "Short subject tag. Empty if none.",
                    },
                    "importance": {
                        "type": "number",
                        "description": "0.0-1.0; 1.0 = critical to the user, "
                                       "0.3 = minor detail.",
                    },
                    "justification": {
                        "type": "string",
                        "description": "Why this operation is warranted, one "
                                       "sentence, grounded in the transcript.",
                    },
                },
                "required": [
                    "op", "content", "entity", "topic", "importance",
                    "justification",
                ],
            },
        },
    },
    "required": ["ops"],
}

_JUDGE_SYSTEM_PROMPT = (
    "You are ULTRON's memory write policy. From the session transcript, propose "
    "operations that keep the user's long-term memory accurate and noise-free.\n"
    "Rules:\n"
    "- Extract only durable facts worth remembering across sessions (identity, "
    "preferences, projects, people, plans, stable knowledge about the user's "
    "world) — never small talk or transient tool output.\n"
    "- ADD introduces a new memory. UPDATE replaces an existing memory's content "
    "when the transcript changes or refines it (set target_id). DELETE retires a "
    "memory the transcript proves wrong or retracted (set target_id). NOOP when "
    "nothing qualifies — that is a valid, common answer.\n"
    "- Ground every operation in the transcript; never invent facts. Every "
    "operation carries a one-sentence justification and an importance in 0.0-1.0."
)


@dataclass(frozen=True)
class MemoryOp:
    """One validated memory write operation (already normalized).

    `importance` is None when the judge did not supply one: ADD then stores
    the neutral default, UPDATE then keeps the target's existing importance
    (a missing field must never silently downgrade a memory)."""

    op: str                                   # ADD | UPDATE | DELETE | NOOP
    content: str = ""
    entity: str = ""
    topic: str = ""
    importance: float | None = None
    justification: str = ""
    target_id: int | None = None


@dataclass(frozen=True)
class WritePolicy:
    """The gate between the judge and the store (§6.5: candidate → judge →
    commit). Tunable; defaults favor precision over recall of memories."""

    min_importance: float = 0.3
    max_ops: int = 32
    allowed_ops: tuple[str, ...] = ("ADD", "UPDATE", "DELETE", "NOOP")

    def validate(self, op: MemoryOp) -> str | None:
        """None if the op may be applied, else the rejection reason."""
        if op.op not in self.allowed_ops:
            return f"unknown op {op.op!r}"
        if op.op in ("ADD", "UPDATE"):
            if not op.content:
                return f"{op.op} without content"
            effective = 0.5 if op.importance is None else op.importance
            if effective < self.min_importance:
                return (f"importance {effective:.2f} below gate "
                        f"{self.min_importance:.2f}")
        if op.op in ("UPDATE", "DELETE") and (
            op.target_id is None or op.target_id < 1
        ):
            return f"{op.op} without a valid target_id"
        return None


@dataclass(frozen=True)
class ProposedOps:
    """Outcome of one judge call: accepted ops plus the full audit trail."""

    ops: tuple[MemoryOp, ...] = ()
    rejected: tuple[tuple[str, str], ...] = ()   # (rendered raw op, reason)
    error: str | None = None                     # judge call itself failed


def _norm_op(raw: Any) -> tuple[MemoryOp | None, str | None]:
    """Normalize one raw judge op; returns (op, None) or (None, reason)."""
    if not isinstance(raw, dict):
        return None, "op is not an object"
    op_name = str(raw.get("op", "")).strip().upper()
    content = str(raw.get("content") or "").strip()
    entity = str(raw.get("entity") or "").strip()
    topic = str(raw.get("topic") or "").strip()
    justification = str(raw.get("justification") or "").strip()
    importance: float | None
    try:
        importance = float(raw["importance"])  # required by the schema
    except (KeyError, TypeError, ValueError):
        importance = None                      # absent/unusable → keep-old
    if importance is not None:
        importance = min(1.0, max(0.0, importance))
    target_raw = raw.get("target_id")
    try:
        target_id = int(target_raw) if target_raw is not None else None
    except (TypeError, ValueError):
        target_id = -1  # present but invalid → fails validate() below
    return MemoryOp(
        op=op_name, content=content, entity=entity, topic=topic,
        importance=importance, justification=justification,
        target_id=target_id,
    ), None


def strip_json_fences(text: str) -> str:
    """Defensive unwrap of a ```json fenced block (structured output should
    already be raw JSON from both adapters; this only tolerates decoration)."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        first_newline = cleaned.find("\n")
        if first_newline != -1:
            cleaned = cleaned[first_newline + 1:]
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    return cleaned.strip()


def parse_ops_json(text: str, policy: WritePolicy) -> ProposedOps:
    """Parse + validate the judge's structured output (pure — no gateway)."""
    cleaned = strip_json_fences(text)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return ProposedOps(error="judge returned non-JSON output")
    if not isinstance(data, dict) or not isinstance(data.get("ops"), list):
        return ProposedOps(error="judge output missing an 'ops' list")

    ops: list[MemoryOp] = []
    rejected: list[tuple[str, str]] = []

    def reject(raw: Any, reason: str) -> None:
        rendered = json.dumps(raw, sort_keys=True, default=str)
        if len(rendered) > 200:
            rendered = rendered[:197] + "..."
        rejected.append((rendered, reason))

    for raw in data["ops"]:
        if len(ops) >= policy.max_ops:
            reject(raw, "over the operation budget")
            continue
        op, norm_err = _norm_op(raw)
        if norm_err is not None or op is None:
            reject(raw, norm_err or "malformed op")
            continue
        reason = policy.validate(op)
        if reason is not None:
            reject(raw, reason)
            continue
        ops.append(op)
    return ProposedOps(ops=tuple(ops), rejected=tuple(rejected))


def render_transcript(messages: list[Message]) -> str:
    """Gateway-neutral transcript → judge input text (role: text lines)."""
    lines = [f"{msg.role}: {msg.text.strip()}"
             for msg in messages if msg.text and msg.text.strip()]
    return "\n".join(lines)


def render_facts(facts: list[dict[str, Any]]) -> str:
    """Existing memories as the judge's dedupe context (§6.5 dedupe check)."""
    if not facts:
        return "(memory is currently empty)"
    lines = [
        f"- [id={f.get('id')}] entity={f.get('entity', '')!r}"
        f" topic={f.get('topic', '')!r}: {f.get('content', '')}"
        for f in facts
    ]
    return "\n".join(lines)


def extraction_prompt(transcript_text: str, facts_text: str) -> str:
    return (
        "EXISTING LONG-TERM MEMORIES (dedupe against these; use their ids "
        "as target_id for UPDATE/DELETE):\n"
        f"{facts_text}\n\n"
        "SESSION TRANSCRIPT:\n"
        f"{transcript_text}\n\n"
        "Propose the memory operations for this transcript as JSON ops."
    )


async def propose_ops(
    gateway: Gateway,
    transcript: list[Message],
    existing_facts: list[dict[str, Any]],
    policy: WritePolicy,
) -> ProposedOps:
    """One structured judge call over the gateway (§6.3 session-end job)."""
    transcript_text = render_transcript(transcript)
    if not transcript_text:
        return ProposedOps(error="transcript has no text to extract from")
    prompt = extraction_prompt(transcript_text, render_facts(existing_facts))
    try:
        response = await gateway.complete(
            [Message(role="system", text=_JUDGE_SYSTEM_PROMPT),
             Message(role="user", text=prompt)],
            response_schema=OPS_SCHEMA,
        )
    except GatewayError as exc:
        return ProposedOps(error=f"judge call failed: {exc}")
    return parse_ops_json(response.text, policy)


__all__ = [
    "OPS_SCHEMA",
    "MemoryOp",
    "ProposedOps",
    "WritePolicy",
    "extraction_prompt",
    "parse_ops_json",
    "propose_ops",
    "render_facts",
    "render_transcript",
    "strip_json_fences",
]
