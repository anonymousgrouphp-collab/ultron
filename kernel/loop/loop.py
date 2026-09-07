"""kernel/loop/loop.py — P1-G: the plan→act→observe agent loop.

Design notes:
- The loop programs ONLY the kernel surfaces: gateway.complete (P1-C) for the
  model, PolicyEngine.run over the ToolRegistry (P1-E/P1-B) for every action.
  It never imports legacy code, and tools run behind the same consent/audit
  choke point as every other caller (fail-closed).
- max_steps bounds the act cycle; a failed tool result is OBSERVED by the model
  (that is v0's replan: the error text is the signal to try something else).
- abort_check is consulted before every model turn — an external stop request
  ends the run cleanly, never mid-tool (tools are bounded by their own timeout).
- Every step lands in an append-only trace of JSON-able records (research/05
  §7: a failure must be replayable from the trace).
- finish semantics: "stop" (model done) | "max_steps" | "aborted" | "error"
  (clean GatewayError capture — the loop never raises past run()).
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

from kernel.bus import EventBus
from kernel.gateway import GatewayError, Message, Response, ToolResultLike
from kernel.policy import ConsentCallback, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import ToolCall, ToolResult

__all__ = ["AgentLoop", "LoopResult", "TraceStep"]


class Completer(Protocol):
    """Structural type satisfied by kernel Gateway (and test fakes)."""

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = ...,
        response_schema: Mapping[str, Any] | None = ...,
    ) -> Response: ...


@dataclass(frozen=True)
class TraceStep:
    """One replayable record of what the loop did (JSON-able detail)."""

    step: int
    kind: str  # "model" | "tool" | "stop"
    detail: Mapping[str, Any] = field(default_factory=dict)
    ts: float = field(default_factory=time.time)


@dataclass(frozen=True)
class LoopResult:
    """Outcome of one run(): the final text plus everything replayable."""

    text: str
    steps: int
    finish: str  # stop | max_steps | aborted | error
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResult, ...] = ()
    trace: tuple[TraceStep, ...] = ()


class AgentLoop:
    """Drives model↔tool cycles to completion. One instance may run many
    conversations; run() itself is single-conversation (async re-entrancy is
    the caller's concern, as with the gateway)."""

    def __init__(
        self,
        gateway: Completer,
        policy: PolicyEngine,
        registry: ToolRegistry,
        *,
        bus: EventBus | None = None,
        max_steps: int = 8,
        consent: ConsentCallback | None = None,
        source: str = "loop",
        abort_check: Callable[[], bool] | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("max_steps must be >= 1")
        self._gateway = gateway
        self._policy = policy
        self._registry = registry
        self._bus = bus
        self._max_steps = max_steps
        self._consent = consent
        self._source = source
        self._abort_check = abort_check

    async def run(self, messages: Sequence[Message]) -> LoopResult:
        """Run the plan→act→observe cycle until the model stops or a bound
        fires. The input sequence is never mutated; the working transcript is
        private to this run."""
        if not messages:
            raise ValueError("AgentLoop.run needs at least one message")

        conversation: list[Message] = list(messages)
        declarations = self._registry.declarations()
        trace: list[TraceStep] = []
        calls: list[ToolCall] = []
        results: list[ToolResult] = []
        step = 0

        while True:
            if self._abort_check is not None and self._abort_check():
                trace.append(TraceStep(step=step, kind="stop",
                                       detail={"finish": "aborted"}))
                return self._result("aborted", conversation, trace, calls, results)
            if step >= self._max_steps:
                trace.append(TraceStep(step=step, kind="stop",
                                       detail={"finish": "max_steps",
                                               "max_steps": self._max_steps}))
                return self._result("max_steps", conversation, trace, calls, results,
                                    text="I stopped at my step limit before finishing.")
            step += 1

            try:
                response = await self._gateway.complete(
                    conversation, tools=declarations
                )
            except GatewayError as exc:
                trace.append(TraceStep(step=step, kind="stop",
                                       detail={"finish": "error", "error": str(exc)}))
                return self._result("error", conversation, trace, calls, results,
                                    text=f"The model gateway failed: {exc}")

            trace.append(TraceStep(
                step=step, kind="model",
                detail={"text": response.text,
                        "tool_calls": [c.name for c in response.tool_calls],
                        "finish": response.finish},
            ))

            if not response.tool_calls:
                return self._result("stop", conversation, trace, calls, results,
                                    text=response.text)

            conversation.append(Message(
                role="assistant", text=response.text,
                tool_calls=response.tool_calls,
                tool_signatures=response.tool_signatures,
            ))
            for call in response.tool_calls:
                result = await self._policy.run(
                    call, self._registry, bus=self._bus, consent=self._consent,
                )
                calls.append(call)
                results.append(result)
                trace.append(TraceStep(
                    step=step, kind="tool",
                    detail={"name": call.name, "ok": result.ok,
                            "risk": result.risk.value,
                            "error": result.error, "duration_ms": result.duration_ms},
                ))
                conversation.append(Message(
                    role="tool",
                    tool_results=(ToolResultLike(
                        name=result.name, ok=result.ok,
                        data=result.data, error=result.error,
                    ),),
                ))

    @staticmethod
    def _result(
        finish: str,
        conversation: Sequence[Message],
        trace: list[TraceStep],
        calls: list[ToolCall],
        results: list[ToolResult],
        text: str | None = None,
    ) -> LoopResult:
        if text is None:
            final = [m for m in conversation if m.role == "assistant" and m.text]
            text = final[-1].text if final else ""
        return LoopResult(
            text=text,
            steps=len([s for s in trace if s.kind == "model"]),
            finish=finish,
            tool_calls=tuple(calls),
            tool_results=tuple(results),
            trace=tuple(trace),
        )
