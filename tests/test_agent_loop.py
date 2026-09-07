"""tests/test_agent_loop.py — P1-G: plan→act→observe loop semantics.

Hermetic: the model is a ScriptedGateway (kernel Response queue); the tools
run through the REAL ToolRegistry + PolicyEngine + AuditLog stack, so every
policy/audit guarantee the live loop gets is exercised here.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from kernel.bus import EventBus
from kernel.gateway import GatewayError, Message, Response
from kernel.loop import AgentLoop
from kernel.policy import AuditLog, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


@dataclass
class ScriptedGateway:
    """Fake Completer: pops queued Responses, records the transcript it saw."""

    turns: list[Response]
    seen: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages, tools=(), response_schema=None):
        self.seen.append(list(messages))
        return self.turns.pop(0)


def note_registry(tmp_path) -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="read_note", description="Read a note.",
              parameters={"type": "object",
                          "properties": {"name": {"type": "string"}},
                          "required": ["name"]},
              risk=RiskClass.READ)
    def read_note(call):
        return {"name": call.args.get("name", ""), "body": "hello"}

    @reg.tool(name="write_note", description="Write a note.",
              parameters={"type": "object",
                          "properties": {"name": {"type": "string"}},
                          "required": ["name"]},
              risk=RiskClass.WRITE)
    def write_note(call):
        return "written"

    return reg


def make_loop(gateway, reg, tmp_path, **kw) -> AgentLoop:
    audit = AuditLog(tmp_path / "audit.sqlite3")
    return AgentLoop(gateway, PolicyEngine(audit=audit), reg,
                     source="test", **kw)


def text_response(text: str) -> Response:
    return Response(text=text, finish="stop")


def call_response(*calls: tuple[str, dict], text: str = "") -> Response:
    return Response(
        text=text,
        tool_calls=tuple(
            ToolCall(id=f"t-{i}-{name}", name=name, args=args, source="model")
            for i, (name, args) in enumerate(calls, 1)
        ),
        finish="tool_calls" if calls else "stop",
    )


# ------------------------------------------------------------------ happy --


def test_text_only_response_stops_immediately(tmp_path) -> None:
    gw = ScriptedGateway([text_response("Done.")])
    result = asyncio.run(make_loop(gw, note_registry(tmp_path), tmp_path)
                         .run([Message(role="user", text="hi")]))
    assert result.finish == "stop"
    assert result.text == "Done."
    assert result.steps == 1
    assert result.tool_calls == ()
    assert result.tool_results == ()


def test_tool_call_is_executed_and_observed(tmp_path) -> None:
    gw = ScriptedGateway([
        call_response(("read_note", {"name": "a"})),
        text_response("The note says hello."),
    ])
    result = asyncio.run(make_loop(gw, note_registry(tmp_path), tmp_path)
                         .run([Message(role="user", text="read note a")]))
    assert result.finish == "stop"
    assert result.steps == 2
    assert [c.name for c in result.tool_calls] == ["read_note"]
    assert result.tool_results[0].ok is True
    assert result.tool_results[0].data == {"name": "a", "body": "hello"}
    # the model observed the tool result in the next turn's transcript
    second = gw.seen[1]
    tool_msg = [m for m in second if m.role == "tool"]
    assert len(tool_msg) == 1
    assert tool_msg[0].tool_results[0].data == {"name": "a", "body": "hello"}
    assert second[-2].role == "assistant"  # assistant echo precedes the result


def test_multi_step_chain_accumulates_calls(tmp_path) -> None:
    gw = ScriptedGateway([
        call_response(("read_note", {"name": "a"})),
        call_response(("read_note", {"name": "b"})),
        text_response("Both read."),
    ])
    result = asyncio.run(make_loop(gw, note_registry(tmp_path), tmp_path)
                         .run([Message(role="user", text="read both")]))
    assert result.steps == 3
    assert [c.name for c in result.tool_calls] == ["read_note", "read_note"]
    assert result.text == "Both read."


# ------------------------------------------------------------------ bounds --


def test_max_steps_bound_aborts_cleanly(tmp_path) -> None:
    endless = [call_response(("read_note", {"name": "x"}))] * 10
    gw = ScriptedGateway(list(endless))
    result = asyncio.run(make_loop(gw, note_registry(tmp_path), tmp_path,
                                   max_steps=2)
                         .run([Message(role="user", text="loop forever")]))
    assert result.finish == "max_steps"
    assert result.steps == 2
    assert "step limit" in result.text
    assert len(gw.seen) == 2


def test_abort_check_fires_before_any_model_turn(tmp_path) -> None:
    gw = ScriptedGateway([text_response("never reached")])
    loop = make_loop(gw, note_registry(tmp_path), tmp_path,
                     abort_check=lambda: True)
    result = asyncio.run(loop.run([Message(role="user", text="hi")]))
    assert result.finish == "aborted"
    assert result.steps == 0
    assert gw.seen == []


# ------------------------------------------------------------- policy tie --


def test_write_tool_is_denied_without_consent_and_observed(tmp_path) -> None:
    gw = ScriptedGateway([
        call_response(("write_note", {"name": "x"})),
        text_response("I could not write; permission was denied."),
    ])
    result = asyncio.run(make_loop(gw, note_registry(tmp_path), tmp_path)
                         .run([Message(role="user", text="write x")]))
    assert result.finish == "stop"
    assert result.tool_results[0].ok is False  # fail-closed by P1-E semantics
    tool_msg = [m for m in gw.seen[1] if m.role == "tool"][0]
    assert tool_msg.tool_results[0].ok is False
    assert tool_msg.tool_results[0].error  # the model sees a clean error


def test_write_tool_runs_with_consent(tmp_path) -> None:
    gw = ScriptedGateway([
        call_response(("write_note", {"name": "x"})),
        text_response("Wrote it."),
    ])
    audit = AuditLog(tmp_path / "audit.sqlite3")
    loop = AgentLoop(gw, PolicyEngine(audit=audit), note_registry(tmp_path),
                     source="test",
                     consent=lambda call, risk: _async_true())
    result = asyncio.run(loop.run([Message(role="user", text="write x")]))
    assert result.finish == "stop"
    assert result.tool_results[0].ok is True


async def _async_true() -> bool:
    return True


def test_gateway_error_is_captured_never_raised(tmp_path) -> None:
    class FailingGateway:
        async def complete(self, messages, tools=(), response_schema=None):
            raise GatewayError("HTTP 503 from provider")

    result = asyncio.run(
        make_loop(FailingGateway(), note_registry(tmp_path), tmp_path)
        .run([Message(role="user", text="hi")])
    )
    assert result.finish == "error"
    assert "503" in result.text
    assert result.steps == 0


# ------------------------------------------------------------- contracts --


def test_trace_records_every_step(tmp_path) -> None:
    gw = ScriptedGateway([
        call_response(("read_note", {"name": "a"})),
        text_response("ok"),
    ])
    result = asyncio.run(make_loop(gw, note_registry(tmp_path), tmp_path)
                         .run([Message(role="user", text="read")]))
    kinds = [(s.kind, s.detail.get("finish", s.detail.get("name")))
             for s in result.trace]
    assert ("model", "tool_calls") in kinds
    assert ("tool", "read_note") in kinds
    assert kinds[-1] == ("stop", "stop") or kinds[-1][0] == "model"
    model_steps = [s for s in result.trace if s.kind == "model"]
    assert model_steps[0].detail["tool_calls"] == ["read_note"]


def test_run_does_not_mutate_input_messages(tmp_path) -> None:
    gw = ScriptedGateway([
        call_response(("read_note", {"name": "a"})),
        text_response("done"),
    ])
    loop = make_loop(gw, note_registry(tmp_path), tmp_path)
    original = [Message(role="user", text="hi")]
    asyncio.run(loop.run(original))
    assert original == [Message(role="user", text="hi")]
    assert len(gw.seen[-1]) > 1  # working transcript grew, input did not


def test_constructor_and_run_validation(tmp_path) -> None:
    with pytest.raises(ValueError, match="max_steps"):
        make_loop(ScriptedGateway([]), note_registry(tmp_path), tmp_path,
                  max_steps=0)
    loop = make_loop(ScriptedGateway([text_response("x")]),
                     note_registry(tmp_path), tmp_path)
    with pytest.raises(ValueError, match="at least one"):
        asyncio.run(loop.run([]))


def test_loop_works_with_shared_bus(tmp_path) -> None:
    seen: list[str] = []
    bus = EventBus()
    bus.subscribe("tool.*", lambda e: seen.append(e.type))
    gw = ScriptedGateway([
        call_response(("read_note", {"name": "a"})),
        text_response("ok"),
    ])
    audit = AuditLog(tmp_path / "audit.sqlite3")
    loop = AgentLoop(gw, PolicyEngine(audit=audit), note_registry(tmp_path),
                     bus=bus, source="test")
    result = asyncio.run(loop.run([Message(role="user", text="read")]))
    assert result.finish == "stop"
    assert "tool.started" in seen and "tool.completed" in seen
