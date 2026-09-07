"""kernel/mcp_server.py — P2-A: serve the kernel's tools over MCP (FastMCP).

Research/05 §2.2 bet #1, server half: the ToolRegistry's tools become an MCP
server, so the dashboard, the phone, or ANY MCP client reaches ULTRON through
the same policy-gated choke point the agent loop uses — there is no second
execution path (Kill List):

    MCP client → FastMCP → PolicyEngine.run → ToolRegistry.execute

Design points:
- Schema fidelity: a tool's MCP inputSchema IS its registry `parameters` JSON
  schema — the same source the model gateway renders. Nothing is re-shaped,
  re-described, or re-implemented here.
- Trust boundary (research/05 §2.2): a remote MCP caller is untrusted input.
  The default policy is the kernel's fail-safe posture (READ allow / WRITE and
  EXECUTE ask / DESTRUCTIVE never), and with no `consent` handler wired an ASK
  decision denies. Every decision lands in the optional AuditLog; denials
  publish `policy.denied` on the optional bus.
- Risk transparency: the risk class is appended to every tool's MCP
  description so remote clients can render their own consent UX.
- Results: only the structured ToolResult crosses the boundary. Successes are
  JSON payloads; failures raise an MCP ToolError carrying ToolResult.error —
  the registry guarantees raw exception text never travels.

Imported explicitly (`import kernel.mcp_server`) and deliberately NOT
re-exported from kernel/__init__, so the kernel core keeps zero third-party
imports (types.py doctrine).
"""

from __future__ import annotations

import dataclasses
import json
import logging
import uuid
from collections.abc import Mapping
from enum import Enum
from typing import Any

from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from fastmcp.tools import FunctionTool
from fastmcp.tools import ToolResult as McpToolResult

from kernel.bus import EventBus
from kernel.policy import AuditLog, ConsentCallback, Policy, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import ToolCall

log = logging.getLogger(__name__)

SERVER_NAME = "ultron"
SERVER_INSTRUCTIONS = (
    "ULTRON kernel tools. Every call crosses the kernel's policy engine "
    "(risk class + consent + audit) exactly as local callers do. Successful "
    "results are structured payloads: "
    "{ok, name, call_id, data, artifacts, risk, duration_ms}."
)


def jsonable(value: Any) -> Any:
    """Reduce a ToolResult payload to JSON-safe types (HTTP/model safe)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return jsonable(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return repr(value)  # last resort: a string rendering, never a crash


def _bridge_tool(
    registry: ToolRegistry,
    engine: PolicyEngine,
    tool_name: str,
    *,
    consent: ConsentCallback | None,
    bus: EventBus | None,
    source: str,
) -> FunctionTool:
    """One registry tool → one MCP tool that executes through the policy engine."""
    tool = registry.get(tool_name)
    if tool is None:  # names come from registry.names(); defensive only
        raise ValueError(f"cannot bridge unknown tool {tool_name!r}")

    async def call_through_kernel(**arguments: Any) -> McpToolResult:
        call = ToolCall(id=f"mcp-{uuid.uuid4().hex[:12]}", name=tool_name,
                        args=dict(arguments), source=source)
        result = await engine.run(call, registry, bus=bus, consent=consent)
        if not result.ok:
            # ToolResult.error is the clean, user-safe string (registry contract):
            # raw exception text never crosses the MCP boundary.
            raise ToolError(result.error or "tool failed")
        payload = {
            "ok": True,
            "name": result.name,
            "call_id": result.call_id,
            "data": jsonable(result.data),
            "artifacts": list(result.artifacts),
            "risk": result.risk.value,
            "duration_ms": result.duration_ms,
        }
        return McpToolResult(content=json.dumps(payload), structured_content=payload)

    return FunctionTool(
        name=tool_name,
        description=f"{tool.description} [risk: {tool.risk.value}]",
        parameters=dict(tool.parameters),
        fn=call_through_kernel,
        output_schema=None,
    )


def build_mcp_server(
    registry: ToolRegistry,
    *,
    policy: Policy | None = None,
    audit: AuditLog | None = None,
    consent: ConsentCallback | None = None,
    bus: EventBus | None = None,
    name: str = SERVER_NAME,
    source: str = "mcp",
) -> FastMCP:
    """Wrap `registry` as an MCP server (fail-safe default policy).

    `consent` — async (call, risk) -> bool — is the ONLY way an ASK-class tool
    runs for a remote caller; None (default) means ASK denies. `audit`
    receives every decision, `bus` receives tool.*/policy.* events, and
    `source` stamps the origin on every ToolCall (default "mcp").
    """
    engine = PolicyEngine(policy, audit=audit)
    server = FastMCP(name=name, instructions=SERVER_INSTRUCTIONS)
    for tool_name in registry.names():
        server.add_tool(_bridge_tool(registry, engine, tool_name,
                                     consent=consent, bus=bus, source=source))
    return server


def serve_stdio(server: FastMCP) -> None:
    """Serve over stdio (blocking) — for MCP hosts such as Claude Desktop."""
    server.run(transport="stdio")


def serve_http(server: FastMCP, *, host: str = "127.0.0.1", port: int = 8001) -> None:
    """Serve over streamable HTTP (blocking). Loopback by default — binding
    beyond the host is an explicit opt-in (P0-B2 rule), never a default."""
    server.run(transport="http", host=host, port=port)


__all__ = ["SERVER_INSTRUCTIONS", "SERVER_NAME", "jsonable",
           "build_mcp_server", "serve_http", "serve_stdio"]
