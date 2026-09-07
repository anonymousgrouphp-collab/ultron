"""kernel/policy/engine.py — P1-E v0: risk-class gating + consent decisions.

Semantics (fail-safe by design):
- Every tool has a RiskClass (from the P1-B registry's risks()).
- A Policy maps RiskClass → Decision:
    ALLOW  — run immediately
    ASK    — run only if a consent callback says yes; NO callback configured → DENY
    DENY   — never run
- The default policy is the roadmap's safety posture: READ flows, WRITE/EXECUTE
  ask, DESTRUCTIVE denied.
- Every decision (allow, ask-yes, ask-no, deny, unknown) is written to the
  AuditLog — policy without an audit trail is theater.
- Denials publish a "policy.denied" event when a bus is provided.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import Enum

from kernel.bus import EventBus
from kernel.types import Event, RiskClass, ToolCall, ToolResult

ConsentCallback = Callable[[ToolCall, RiskClass], Awaitable[bool]]


class Decision(str, Enum):
    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


@dataclass(frozen=True)
class Policy:
    """RiskClass → Decision map. Build via Policy.default() or explicit rules."""

    rules: dict[RiskClass, Decision]

    def __post_init__(self) -> None:
        missing = set(RiskClass) - set(self.rules)
        if missing:
            raise ValueError(
                f"policy must cover every RiskClass, missing: {sorted(m.value for m in missing)}")

    @staticmethod
    def default() -> "Policy":
        return Policy(rules={
            RiskClass.READ: Decision.ALLOW,
            RiskClass.WRITE: Decision.ASK,
            RiskClass.EXECUTE: Decision.ASK,
            RiskClass.DESTRUCTIVE: Decision.DENY,
        })

    def decide(self, risk: RiskClass) -> Decision:
        return self.rules[risk]


class PolicyEngine:
    """Gates tool execution. Usage (agent loop, P1-G):

        result = await policy.run(call, registry, bus=bus, consent=maybe_ask_user)
    """

    def __init__(self, policy: Policy | None = None, audit=None) -> None:
        self.policy = policy or Policy.default()
        self.audit = audit          # AuditLog or None (None = decide, don't record)

    def decide(self, risk: RiskClass) -> Decision:
        return self.policy.decide(risk)

    async def run(self, call: ToolCall, registry, bus: EventBus | None = None,
                  consent: ConsentCallback | None = None) -> ToolResult:
        """Check policy, then execute via the registry. Never raises."""
        risk = registry.risks().get(call.name)
        if risk is None:
            # unknown tool: registry produces the clean "unknown tool" fail —
            # still audited, as an anomaly.
            result = await registry.execute(call, bus=bus)
            self._record(call, RiskClass.READ, "unknown", ok=result.ok,
                         note=result.error or "")
            return result

        decision = self.decide(risk)

        if decision is Decision.DENY:
            return await self._deny(call, risk, "denied by policy", bus=bus)

        if decision is Decision.ASK:
            if consent is None:
                # fail-safe: an ASK with no way to ask is a NO.
                return await self._deny(
                    call, risk, "requires consent but no consent handler is available",
                    bus=bus)
            try:
                allowed = await consent(call, risk)
            except Exception as e:  # noqa: BLE001 — a broken consent UI must not execute the tool
                return await self._deny(call, risk,
                                        f"consent handler failed: {type(e).__name__}",
                                        bus=bus)
            if not allowed:
                return await self._deny(call, risk, "user declined", bus=bus)

        result = await registry.execute(call, bus=bus)
        self._record(call, risk,
                     decision.value if decision is Decision.ALLOW else "ask-yes",
                     ok=result.ok)
        return result

    async def _deny(self, call: ToolCall, risk: RiskClass, reason: str,
                    bus: EventBus | None = None) -> ToolResult:
        self._record(call, risk, "denied", note=reason)
        if bus is not None:
            await bus.publish(Event(
                type="policy.denied",
                payload={"call_id": call.id, "name": call.name,
                         "risk": risk.value, "reason": reason},
                source=call.source,
            ))
        return ToolResult.fail(call, reason, risk=risk)

    def _record(self, call: ToolCall, risk: RiskClass, decision: str,
                ok: bool | None = None, note: str = "") -> None:
        if self.audit is not None:
            self.audit.record(call=call, risk=risk, decision=decision, ok=ok, note=note)


__all__ = ["ConsentCallback", "Decision", "Policy", "PolicyEngine"]
