"""tests/test_loop_fuzz.py — §P3 planner-fuzz + never-silent evals (K9-derived).

K9's stress suite (research/09 §P3) fuzzes the planner with malformed model
output and asserts the system never crashes, never leaks raw JSON, and is
NEVER SILENT under load. Ported to ULTRON's AgentLoop: a ScriptedGateway
emits degenerate tool calls (unknown tool, empty name, garbage args, duplicate
ids, huge turns) and the assertions pin the fail-clean contract.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import pytest

from kernel.gateway import Message, Response
from kernel.loop import AgentLoop
from kernel.policy import AuditLog, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall, ToolResult


@dataclass
class ScriptedGateway:
    """Fake Completer: pops queued Responses (the test fuzzer's script)."""

    turns: list[Response]
    seen: list[list[Message]] = field(default_factory=list)

    async def complete(self, messages, tools=(), response_schema=None):
        self.seen.append(list(messages))
        if not self.turns:
            raise AssertionError("fuzz script exhausted — loop kept asking")
        return self.turns.pop(0)


def build_registry() -> ToolRegistry:
    reg = ToolRegistry()

    @reg.tool(name="echo", description="Echo back a value.",
              parameters={"type": "object",
                          "properties": {"value": {"type": "string"}},
                          "required": ["value"]},
              risk=RiskClass.READ)
    def echo(call):
        return {"echoed": call.args.get("value", "")}

    return reg


def make_loop(tmp_path, turns) -> tuple[AgentLoop, ScriptedGateway]:
    gateway = ScriptedGateway(turns=list(turns))
    audit = AuditLog(tmp_path / "audit.sqlite3")
    loop = AgentLoop(gateway, PolicyEngine(audit=audit), build_registry(),
                     max_steps=6, source="fuzz")
    return loop, gateway


def call_response(*calls, text: str = "") -> Response:
    return Response(
        text=text,
        tool_calls=tuple(
            ToolCall(id=cid, name=name, args=args, source="fuzz")
            for cid, name, args in calls),
        finish="tool_calls",
    )


# ------------------------------------------------------- malformed calls ----

def test_unknown_tool_yields_fail_result_not_crash(tmp_path):
    loop, _gw = make_loop(tmp_path, [
        call_response(("c1", "does_not_exist", {})),
        Response(text="Recovered, told the user.", finish="stop"),
    ])
    result = asyncio.run(loop.run([Message(role="user", text="hi")]))
    assert result.finish == "stop"
    assert len(result.tool_results) == 1
    failed = result.tool_results[0]
    assert not failed.ok
    assert "unknown tool" in failed.error
    # the failure was OBSERVED by the model (fail-clean replan signal)
    assert result.text == "Recovered, told the user."


def test_empty_tool_name_rejected_at_type_boundary(tmp_path):
    """An empty tool name can never reach the loop: ToolCall's constructor
    rejects it (the gateway parse layer inherits this fail-clean boundary)."""
    with pytest.raises(ValueError):
        ToolCall(id="c1", name="", args={})


def test_garbage_args_never_leak_raw_exception(tmp_path):
    """Args of absurd shapes (None-ish values, wrong types) must come back as
    clean fail/error results — never a raised exception or a stack trace."""
    loop, _gw = make_loop(tmp_path, [
        call_response(("c1", "echo", {"value": 12345})),
        call_response(("c2", "echo", {"unexpected": ["a", "b"]})),
        call_response(("c3", "echo", {})),  # missing required arg
        Response(text="done", finish="stop"),
    ])
    result = asyncio.run(loop.run([Message(role="user", text="hi")]))
    assert len(result.tool_results) == 3
    for tr in result.tool_results:
        assert isinstance(tr, ToolResult)
        if not tr.ok:
            assert "Traceback" not in (tr.error or "")


def test_duplicate_call_ids_do_not_confuse_the_loop(tmp_path):
    loop, _gw = make_loop(tmp_path, [
        call_response(("dup", "echo", {"value": "one"}),
                      ("dup", "echo", {"value": "two"})),
        Response(text="both ran", finish="stop"),
    ])
    result = asyncio.run(loop.run([Message(role="user", text="hi")]))
    assert len(result.tool_results) == 2
    assert result.tool_results[0].ok and result.tool_results[1].ok


# --------------------------------------------------------- never-silent ----

def test_never_silent_under_load(tmp_path):
    """50 degenerate model turns in a row: EVERY turn must produce an
    observable outcome (text or a tool result) — silence is a failure
    (K9's load-test lesson). The step limit ends the run cleanly."""
    turns = [call_response((f"c{i}", "nope_tool", {})) for i in range(50)]
    turns.append(Response(text="stress run finished", finish="stop"))
    loop, _gw = make_loop(tmp_path, turns)
    loop._max_steps = 60
    result = asyncio.run(loop.run([Message(role="user", text="stress")]))
    assert result.finish == "stop"
    assert len(result.tool_results) == 50
    assert all(not tr.ok for tr in result.tool_results)  # all clean fails
    assert result.text  # final outcome observable


def test_model_step_limit_ends_cleanly(tmp_path):
    loop, _gw = make_loop(tmp_path, [
        call_response(("c1", "echo", {"value": "x"})),
    ] * 3)
    loop._max_steps = 2
    result = asyncio.run(loop.run([Message(role="user", text="hi")]))
    assert result.finish == "max_steps"
    assert result.text  # never silent even at the limit
