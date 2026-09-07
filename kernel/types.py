"""kernel/types.py — P1-A: the typed vocabulary every kernel subsystem speaks.

Design notes:
- Frozen dataclasses: events/results are immutable records (audit-friendly, safe to share).
- ToolResult REPLACES the old string-result protocol (Kill List #2): what the MODEL sees,
  what the LOG gets, and what the USER hears are three renderings built FROM this
  structure — never one shared human string.
- Stdlib only; no heavy imports at kernel level (voice/UI stay outside).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping


class RiskClass(str, Enum):
    """Policy classes for tools (consumed by the P1-E policy engine).

    READ        — observes; cannot change state (list files, read config, screenshot)
    WRITE       — mutates user-visible state (create/edit files, send a message)
    EXECUTE     — runs code/commands (sandboxed shell, script replay)
    DESTRUCTIVE — irreversible or dangerous (delete files, kill processes, purge history)
    """

    READ = "read"
    WRITE = "write"
    EXECUTE = "execute"
    DESTRUCTIVE = "destructive"


@dataclass(frozen=True)
class ToolCall:
    """A request to run a tool. `source` records who asked (voice, dashboard,
    subagent, scheduler, test) — the policy engine will use it for consent scoping."""

    id: str
    name: str
    args: Mapping[str, Any] = field(default_factory=dict)
    source: str = "unknown"

    def __post_init__(self) -> None:
        if not self.id:
            raise ValueError("ToolCall.id is required")
        if not self.name:
            raise ValueError("ToolCall.name is required")


@dataclass(frozen=True)
class ToolResult:
    """Structured outcome of a ToolCall. `data` is what the model observes;
    `error` is a clean, user-safe message — raw exception text never travels here."""

    call_id: str
    name: str
    ok: bool
    data: Any = None
    error: str | None = None
    artifacts: tuple[str, ...] = ()          # paths/URLs produced by the tool
    risk: RiskClass = RiskClass.READ
    duration_ms: float = 0.0

    @classmethod
    def ok(
        cls,
        call: ToolCall,
        data: Any = None,
        risk: RiskClass = RiskClass.READ,
        duration_ms: float = 0.0,
        artifacts: tuple[str, ...] = (),
    ) -> "ToolResult":
        return cls(call_id=call.id, name=call.name, ok=True, data=data,
                   risk=risk, duration_ms=duration_ms, artifacts=artifacts)

    @classmethod
    def fail(cls, call: ToolCall, error: str, risk: RiskClass = RiskClass.READ,
             duration_ms: float = 0.0) -> "ToolResult":
        if not error:
            raise ValueError("ToolResult.fail requires a clean error message")
        return cls(call_id=call.id, name=call.name, ok=False, error=error,
                   risk=risk, duration_ms=duration_ms)


@dataclass(frozen=True)
class Event:
    """A kernel event. `type` uses dotted, lowercase, segment names by convention:
    "voice.wake", "tool.completed", "job.progress", "system.alert", "bus.subscriber_error".
    Wildcard subscribers match on whole segments ("tool.*", "*") — see bus._matches."""

    type: str
    payload: Mapping[str, Any] = field(default_factory=dict)
    source: str = "unknown"
    ts: float = field(default_factory=time.time)
    seq: int = 0                             # assigned by EventBus on publish

    def __post_init__(self) -> None:
        if not self.type:
            raise ValueError("Event.type is required")


__all__ = ["Event", "RiskClass", "ToolCall", "ToolResult"]
