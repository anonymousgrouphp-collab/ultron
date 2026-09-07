"""kernel/tools/base.py — P1-B: the Tool record.

A Tool bundles everything the kernel, gateway and policy engine need:
- `parameters`  : JSON schema (`{"type": "object", ...}`) — gateway renders it into
                  model function-declarations verbatim (Gemini-style; P2-A FastMCP
                  wraps the same shape).
- `risk`        : RiskClass consumed by the P1-E policy engine (consent gating).
- `timeout_s` / `max_retries` : execution budget enforced by ToolRegistry.execute.

Handler contract: `handler(call: ToolCall) -> ToolResult | Any`.
- Return a ToolResult  → passed through (call_id/name re-stamped to the actual call).
- Return anything else → wrapped as ToolResult.success(call, data=result).
- Raise               → retried (up to max_retries), then ToolResult.fail with a
  clean message. Handlers should return ToolResult.fail themselves for *expected*
  failures — those are NOT retried.
Sync handlers are accepted (awaited if awaitable) — same as EventBus.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Union

from kernel.types import RiskClass, ToolCall, ToolResult

Handler = Callable[[ToolCall], Union[Awaitable[Union[ToolResult, Any]], Union[ToolResult, Any]]]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: Mapping[str, Any]
    handler: Handler
    risk: RiskClass = RiskClass.READ
    timeout_s: float = 30.0
    max_retries: int = 0
    _extra: Mapping[str, Any] = field(default_factory=dict)  # future: scopes, cost hints

    def __post_init__(self) -> None:
        if not self.name or not self.name.replace("_", "").isalnum():
            raise ValueError(f"tool name must be non-empty snake_case, got {self.name!r}")
        if not self.description:
            raise ValueError(f"tool {self.name!r} needs a description (the model reads it)")
        if not isinstance(self.parameters, Mapping) or self.parameters.get("type") != "object":
            raise ValueError(
                f"tool {self.name!r} parameters must be a JSON schema of type 'object' "
                '(e.g. {"type": "object", "properties": {...}})'
            )
        if self.timeout_s <= 0:
            raise ValueError(f"tool {self.name!r} timeout_s must be > 0")
        if self.max_retries < 0:
            raise ValueError(f"tool {self.name!r} max_retries must be >= 0")


__all__ = ["Handler", "Tool"]
