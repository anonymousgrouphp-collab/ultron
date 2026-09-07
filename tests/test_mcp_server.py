"""P2-A tests: FastMCP server bridging the kernel tool registry (hermetic —
in-memory transport, no sockets, no processes, no network)."""

import asyncio
import json

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError

from kernel import Decision, EventBus, Policy, RiskClass
from kernel.mcp_server import build_mcp_server
from kernel.policy import AuditLog
from kernel.tools import ToolRegistry


def make_registry():
    """Small registry covering every risk class + crash/oddity handlers."""
    reg = ToolRegistry()
    fired = {"purge": False}

    @reg.tool(name="read_note", description="reads a note",
              parameters={"type": "object", "properties": {"key": {"type": "string"}}},
              risk=RiskClass.READ)
    def read_note(call):
        return {"note": f"content-of-{call.args['key']}"}

    @reg.tool(name="write_note", description="writes a note",
              parameters={"type": "object",
                          "properties": {"key": {"type": "string"},
                                         "text": {"type": "string"}}},
              risk=RiskClass.WRITE)
    def write_note(call):
        return {"written": call.args["key"]}

    @reg.tool(name="purge_all", description="purges everything",
              parameters={"type": "object", "properties": {}},
              risk=RiskClass.DESTRUCTIVE)
    def purge_all(call):
        fired["purge"] = True
        return {"purged": True}

    @reg.tool(name="boom", description="always crashes",
              parameters={"type": "object", "properties": {}}, risk=RiskClass.READ)
    def boom(call):
        raise RuntimeError("SECRET internals: C:\\Users\\secrets.txt")

    @reg.tool(name="echo_args", description="returns its arguments verbatim",
              parameters={"type": "object", "properties": {}}, risk=RiskClass.READ)
    def echo_args(call):
        return {"got": dict(call.args)}

    @reg.tool(name="weird", description="returns an unserializable object",
              parameters={"type": "object", "properties": {}}, risk=RiskClass.READ)
    def weird(call):
        return object()

    return reg, fired


def build(reg=None, **kw):
    if reg is None:
        reg, _ = make_registry()
    return build_mcp_server(reg, **kw)


def run(coro):
    return asyncio.run(coro)


async def _list(server):
    async with Client(server) as client:
        return await client.list_tools()


async def _call(server, name, args):
    async with Client(server) as client:
        return await client.call_tool(name, args)


# ---------------------------------------------------------------- listing

def test_01_lists_registry_tools_with_schema_fidelity():
    reg, _ = make_registry()
    tools = run(_list(build(reg)))
    assert [t.name for t in tools] == sorted(reg.names())
    by = {t.name: t for t in tools}
    assert by["read_note"].description == "reads a note [risk: read]"
    assert by["read_note"].input_schema == {
        "type": "object", "properties": {"key": {"type": "string"}}}


def test_02_empty_registry_builds_a_valid_server():
    assert run(_list(build_mcp_server(ToolRegistry()))) == []


# ---------------------------------------------------------------- execution

def test_03_read_tool_runs_and_returns_structured_payload():
    async def _go():
        async with Client(build()) as client:
            return (await client.call_tool("read_note", {"key": "abc"}),
                    await client.call_tool("read_note", {"key": "abc"}))
    r1, r2 = run(_go())
    p = r1.data
    assert p["ok"] is True
    assert p["name"] == "read_note"
    assert p["data"] == {"note": "content-of-abc"}
    assert p["risk"] == "read"
    assert p["artifacts"] == []
    assert isinstance(p["duration_ms"], float) and p["duration_ms"] >= 0.0
    assert p["call_id"].startswith("mcp-")
    assert p["call_id"] != r2.data["call_id"]  # unique per call
    assert json.loads(r1.content[0].text) == p  # text content mirrors structured


def test_04_arguments_reach_the_handler_unchanged():
    payload = run(_call(build(), "echo_args",
                        {"a": 1, "b": [2, 3], "c": {"d": True}})).data
    assert payload["ok"] is True
    assert payload["data"] == {"got": {"a": 1, "b": [2, 3], "c": {"d": True}}}


def test_05_unserializable_data_becomes_a_string_never_a_crash():
    payload = run(_call(build(), "weird", {})).data
    assert payload["ok"] is True
    assert isinstance(payload["data"], str) and payload["data"]


# ---------------------------------------------------------------- policy

def test_06_write_without_consent_is_denied_failsafe():
    reg, _ = make_registry()
    bus = EventBus()

    async def _go():
        async with Client(build_mcp_server(reg, bus=bus)) as client:
            with pytest.raises(ToolError, match="requires consent"):
                await client.call_tool("write_note", {"key": "k", "text": "t"})

    run(_go())
    denied = [e for e in bus.history if e.type == "policy.denied"]
    assert len(denied) == 1
    assert denied[0].source == "mcp"
    assert denied[0].payload["name"] == "write_note"
    assert denied[0].payload["risk"] == "write"


def test_07_write_with_consent_runs_and_audits_ask_yes():
    reg, _ = make_registry()
    audit = AuditLog()
    seen = []

    async def consent(call, risk):
        seen.append((call.name, risk, call.source))
        return True

    payload = run(_call(build_mcp_server(reg, audit=audit, consent=consent),
                        "write_note", {"key": "k", "text": "t"})).data
    assert payload["ok"] is True and payload["data"] == {"written": "k"}
    assert seen == [("write_note", RiskClass.WRITE, "mcp")]
    row = audit.recent()[0]
    assert (row["name"], row["decision"], row["source"]) == \
        ("write_note", "ask-yes", "mcp")


def test_08_consent_declined_denies():
    reg, _ = make_registry()
    audit = AuditLog()

    async def consent(call, risk):
        return False

    async def _go():
        async with Client(build_mcp_server(reg, audit=audit, consent=consent)) as client:
            with pytest.raises(ToolError, match="user declined"):
                await client.call_tool("write_note", {"key": "k", "text": "t"})

    run(_go())
    assert audit.recent()[0]["decision"] == "denied"


def test_09_destructive_denied_even_when_consent_says_yes():
    reg, fired = make_registry()

    async def consent(call, risk):
        return True

    async def _go():
        async with Client(build_mcp_server(reg, consent=consent)) as client:
            with pytest.raises(ToolError, match="denied by policy"):
                await client.call_tool("purge_all", {})

    run(_go())
    assert fired["purge"] is False  # handler never executed


def test_10_custom_policy_can_allow_write_without_consent():
    reg, _ = make_registry()
    permissive = Policy(rules={
        RiskClass.READ: Decision.ALLOW,
        RiskClass.WRITE: Decision.ALLOW,
        RiskClass.EXECUTE: Decision.ALLOW,
        RiskClass.DESTRUCTIVE: Decision.DENY,
    })

    async def _go():
        async with Client(build_mcp_server(reg, policy=permissive)) as client:
            ok = await client.call_tool("write_note", {"key": "k", "text": "t"})
            with pytest.raises(ToolError, match="denied by policy"):
                await client.call_tool("purge_all", {})
            return ok

    assert run(_go()).data["data"] == {"written": "k"}


def test_11_audit_trail_records_allow_ask_yes_and_denied():
    reg, _ = make_registry()
    audit = AuditLog()

    async def consent(call, risk):
        return True

    async def _go():
        async with Client(build_mcp_server(reg, audit=audit, consent=consent)) as client:
            await client.call_tool("read_note", {"key": "a"})               # allow
            await client.call_tool("write_note", {"key": "a", "text": "b"})  # ask-yes
            with pytest.raises(ToolError):
                await client.call_tool("purge_all", {})                     # denied

    run(_go())
    decisions = [(r["name"], r["decision"], r["source"]) for r in audit.recent()]
    assert ("read_note", "allow", "mcp") in decisions
    assert ("write_note", "ask-yes", "mcp") in decisions
    assert ("purge_all", "denied", "mcp") in decisions


# ---------------------------------------------------------------- failures

def test_12_unknown_tool_is_a_clean_protocol_error():
    async def _go():
        async with Client(build()) as client:
            with pytest.raises(ToolError, match="nope"):
                await client.call_tool("nope", {})

    run(_go())


def test_13_handler_crash_becomes_clean_error_without_internals():
    async def _go():
        async with Client(build()) as client:
            try:
                await client.call_tool("boom", {})
            except ToolError as err:
                return str(err)
        raise AssertionError("boom must raise ToolError")

    message = run(_go())
    assert message == "tool failed unexpectedly"
    assert "SECRET" not in message


# ---------------------------------------------------------------- events

def test_14_bus_receives_tool_lifecycle_events_on_success():
    reg, _ = make_registry()
    bus = EventBus()
    run(_call(build_mcp_server(reg, bus=bus), "read_note", {"key": "a"}))
    types = [e.type for e in bus.history]
    assert "tool.started" in types and "tool.completed" in types
