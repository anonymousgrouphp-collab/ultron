"""kernel/tools/registry.py — P1-B: the tool registry + single execution choke point.

ToolRegistry is where tools live and the ONLY path through which they run:
- declarations() → gateway-ready function declarations (model-facing shape)
- execute(call)  → timeout + retry enforced, results always structured ToolResult,
  never raw exceptions. The P1-E policy engine and P2-A MCP server will wrap/serve
  this same entry point — nothing else may execute tools directly.
- Optional bus: when provided, execute publishes "tool.started" / "tool.completed"
  / "tool.failed" events (segment convention from P1-A).

Auto-registration: `@registry.tool(...)` decorator registers the decorated function
and returns it unchanged, so it stays directly callable/testable. A module-level
`default_registry` exists for simple use; subsystems may build their own scoped
registries (subagents get restricted copies — P2-C).

Execution semantics:
- async handlers are awaited; sync handlers run in the default executor thread so
  the event loop never blocks and timeouts are actually enforceable (a timed-out
  thread cannot be killed — its result is simply discarded).
- A handler RAISE is a crash: retried up to max_retries, then a clean fail result.
- A handler RETURNING ToolResult.fail is an answer: delivered as-is, never retried.
"""

from __future__ import annotations

import asyncio
import dataclasses
import inspect
import logging
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from kernel.bus import EventBus
from kernel.types import Event, RiskClass, ToolCall, ToolResult

from .base import Tool

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExecMeta:
    """Execution bookkeeping attached to log lines (attempts made, wall time)."""

    attempts: int
    duration_ms: float


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    # -- registration ------------------------------------------------------

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} already registered")
        self._tools[tool.name] = tool

    def tool(self, *, name: str | None = None, description: str,
             parameters: Mapping[str, Any], risk: RiskClass = RiskClass.READ,
             timeout_s: float = 30.0, max_retries: int = 0) -> Callable[[Any], Any]:
        """Auto-registering decorator. The function stays directly callable."""
        def decorator(fn: Any) -> Any:
            self.register(Tool(name=name or fn.__name__, description=description,
                               parameters=parameters, handler=fn, risk=risk,
                               timeout_s=timeout_s, max_retries=max_retries))
            return fn
        return decorator

    # -- lookup ------------------------------------------------------------

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._tools))

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def declarations(self) -> tuple[dict[str, Any], ...]:
        """Gateway-ready function declarations (model-facing, Gemini-style shape)."""
        return tuple(
            {"name": t.name, "description": t.description,
             "parameters": dict(t.parameters)}
            for t in sorted(self._tools.values(), key=lambda t: t.name)
        )

    def risks(self) -> dict[str, RiskClass]:
        """Risk class per tool — the P1-E policy engine's lookup table."""
        return {name: t.risk for name, t in self._tools.items()}

    # -- execution ---------------------------------------------------------

    async def execute(self, call: ToolCall, bus: EventBus | None = None) -> ToolResult:
        """Run a tool with timeout + retry, always returning a structured ToolResult.
        Never raises: unknown tool, crashes and timeouts all become fail results."""
        tool = self._tools.get(call.name)
        if tool is None:
            return ToolResult.fail(call, f"unknown tool: {call.name}")
        if bus is not None:
            await bus.publish(Event(
                type="tool.started",
                payload={"call_id": call.id, "name": call.name,
                         "source": call.source, "risk": tool.risk.value},
                source=call.source,
            ))

        start = time.monotonic()
        last_error = "tool failed"
        attempts = 0
        result: ToolResult | None = None
        while attempts <= tool.max_retries:
            attempts += 1
            try:
                raw = await asyncio.wait_for(self._invoke(tool, call),
                                             timeout=tool.timeout_s)
                result = self._wrap(call, raw)
            except asyncio.TimeoutError:
                last_error = f"timed out after {tool.timeout_s:g}s"
                continue
            except Exception:  # noqa: BLE001 — the choke point converts ALL crashes
                # Exception detail belongs in local logs, never in a tool result
                # that may be sent to a model, dashboard, or spoken response.
                log.exception("tool %s crashed on attempt %s", tool.name, attempts)
                last_error = "tool failed unexpectedly"
                continue
            break
        duration = (time.monotonic() - start) * 1000.0

        if result is None:  # retries exhausted
            result = ToolResult.fail(call, last_error, risk=tool.risk,
                                     duration_ms=duration)
            if bus is not None:
                await bus.publish(Event(
                    type="tool.failed",
                    payload={"call_id": call.id, "name": call.name, "ok": False,
                             "duration_ms": round(duration, 3)},
                    source=call.source,
                ))
            return result

        final = dataclasses.replace(result, duration_ms=duration)
        if bus is not None:
            await bus.publish(Event(
                type="tool.completed" if final.ok else "tool.failed",
                payload={"call_id": call.id, "name": call.name,
                         "ok": final.ok, "duration_ms": round(duration, 3)},
                source=call.source,
            ))
        return final

    @staticmethod
    async def _invoke(tool: Tool, call: ToolCall) -> Any:
        """Normalize sync/async handlers; sync runs in a thread (timeout-enforceable)."""
        if inspect.iscoroutinefunction(tool.handler):
            return await tool.handler(call)
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: tool.handler(call))

    @staticmethod
    def _wrap(call: ToolCall, raw: Any) -> ToolResult:
        """Handler contract: ToolResult passthrough (re-stamped), or ok-wrap."""
        if isinstance(raw, ToolResult):
            if raw.call_id == call.id and raw.name == call.name:
                return raw
            return dataclasses.replace(raw, call_id=call.id, name=call.name)
        return ToolResult.success(call, data=raw)


default_registry = ToolRegistry()

__all__ = ["ExecMeta", "Tool", "ToolRegistry", "default_registry"]
