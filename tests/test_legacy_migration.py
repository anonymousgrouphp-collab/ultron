"""Behavioural regression tests for the P1-F legacy-to-kernel migration seam."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from core.tool_declarations import TOOL_DECLARATIONS
from kernel.legacy import LEGACY_TOOL_RISKS, LegacyToolRuntime
from kernel.types import RiskClass, ToolCall, ToolResult


def _handlers(calls: list[str]):
    def make_handler(name: str):
        async def handler(args):
            calls.append(name)
            return {"tool": name, "args": args}
        return handler

    return {declaration["name"]: make_handler(declaration["name"])
            for declaration in TOOL_DECLARATIONS}


def test_legacy_declarations_have_one_complete_risk_classification():
    declared = {declaration["name"] for declaration in TOOL_DECLARATIONS}

    assert set(LEGACY_TOOL_RISKS) == declared
    assert LEGACY_TOOL_RISKS["web_search"] is RiskClass.READ
    assert LEGACY_TOOL_RISKS["reminder"] is RiskClass.WRITE
    assert LEGACY_TOOL_RISKS["open_app"] is RiskClass.EXECUTE
    assert LEGACY_TOOL_RISKS["file_controller"] is RiskClass.DESTRUCTIVE


def test_legacy_runtime_allows_reads_but_denies_side_effects_without_consent(tmp_path: Path):
    calls: list[str] = []
    runtime = LegacyToolRuntime(
        declarations=TOOL_DECLARATIONS,
        handlers=_handlers(calls),
        audit_path=tmp_path / "audit.sqlite3",
    )

    read = asyncio.run(runtime.execute(ToolCall(
        id="read", name="web_search", args={"query": "ULTRON"}, source="test",
    )))
    write = asyncio.run(runtime.execute(ToolCall(
        id="write", name="reminder", args={"time": "09:00",
                                           "message": "standup"}, source="test",
    )))
    destructive = asyncio.run(runtime.execute(ToolCall(
        id="delete", name="file_controller", args={"action": "delete"}, source="test",
    )))

    assert read.ok and read.data["tool"] == "web_search"
    assert write.ok is False and "requires consent" in (write.error or "")
    assert destructive.ok is False and destructive.error == "denied by policy"
    assert calls == ["web_search"]
    assert [entry["decision"] for entry in runtime.audit.recent()] == [
        "denied", "denied", "allow",
    ]


def test_live_function_call_uses_runtime_instead_of_direct_handler_lookup():
    from main import UltronLive

    class UI:
        muted = True

        def set_state(self, state):
            self.state = state

        def write_log(self, message):
            self.last_log = message

    class Runtime:
        def __init__(self):
            self.call = None

        async def execute(self, call, *, consent=None):
            self.call = call
            self.consent = consent
            return ToolResult.success(call, data="safe result", risk=RiskClass.READ)

    assistant = object.__new__(UltronLive)
    assistant.ui = UI()
    assistant._dashboard = None
    assistant._tool_runtime = Runtime()
    assistant._consent_gate = None
    response = asyncio.run(UltronLive._execute_tool(
        assistant, SimpleNamespace(id="fc-1", name="web_search", args={"query": "x"}),
    ))

    assert assistant._tool_runtime.call == ToolCall(
        id="fc-1", name="web_search", args={"query": "x"}, source="gemini-live",
    )
    assert response.response == {"result": "safe result"}
