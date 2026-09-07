"""kernel/mcp_client.py — P2-B: mount external MCP servers as kernel tools.

Research/05 §2.2 bet #1, client half: FastMCP's client mounts remote servers
(filesystem, fetch, GitHub, anything in the registry ecosystem) into a
ToolRegistry. One mount = one server; every tool it exposes becomes a kernel
`Tool` whose handler forwards over MCP. The execution path is the same choke
point as every other caller:

    model/subagent → ToolRegistry.execute → MCP client → remote server

Trust boundary (research/05 §2.2): a remote server's tools are UNTRUSTED —
- every mounted tool defaults to RiskClass.EXECUTE, so the default policy
  (P1-E) requires consent before any mounted tool runs; trusted self-hosted
  servers can be relaxed per tool via `risk_overrides`;
- remote output is treated as data (`jsonable`-normalized), never instructions;
- remote error text is capped and wrapped, never executed or spoken raw.

Transports: stdio subprocess (the standard MCP host integration — e.g. the
official reference servers via npx/python), streamable HTTP, or an in-process
FastMCP instance (tests; embedding another kernel's server directly).

Typical use:

    mount = await mount_stdio("npx", ["-y", "@modelcontextprotocol/server-filesystem", root],
                              prefix="fs", risk_overrides={"read_file": RiskClass.READ})
    try:
        mount.into(registry)          # tools named fs_read_file, fs_write_file, ...
        ...  # registry.execute / PolicyEngine as with any tool
    finally:
        await mount.stop()
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping, Sequence
from typing import Any

from fastmcp import Client, FastMCP
from fastmcp.client.transports import StdioTransport, StreamableHttpTransport
from fastmcp.exceptions import ToolError

from kernel.mcp_server import jsonable
from kernel.tools import ToolRegistry
from kernel.tools.base import Tool
from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_S = 60.0
_ERROR_CAP = 300

__all__ = [
    "DEFAULT_TIMEOUT_S",
    "McpMount",
    "mount_http",
    "mount_inproc",
    "mount_stdio",
]


def _kernel_name(prefix: str, mcp_name: str) -> str:
    """Map an MCP tool name to a kernel snake_case name under a prefix."""
    if not re.fullmatch(r"[a-z][a-z0-9_]*", prefix):
        raise ValueError(
            f"mount prefix must be lowercase snake_case, got {prefix!r}")
    safe = re.sub(r"[^A-Za-z0-9_]", "_", mcp_name)
    if not safe:
        raise ValueError(f"mcp tool name {mcp_name!r} maps to nothing usable")
    return f"{prefix}_{safe}"


def _clean_error(text: str) -> str:
    collapsed = " ".join(str(text).split())
    return collapsed[:_ERROR_CAP] or "remote tool failed"


class McpMount:
    """A connected external MCP server exposing its tools as kernel `Tool`s.

    Construct via :func:`mount_stdio` / :func:`mount_http` / :func:`mount_inproc`
    (each returns a STARTED mount). Always :meth:`stop` when done — a stdio
    mount owns a child process.
    """

    def __init__(
        self,
        client: Client,
        *,
        prefix: str,
        label: str,
        default_risk: RiskClass,
        risk_overrides: Mapping[str, RiskClass],
        include: Sequence[str] | None,
        timeout_s: float,
    ) -> None:
        self._client = client
        self._prefix = prefix
        self._label = label
        self._default_risk = default_risk
        self._risk_overrides = dict(risk_overrides)
        self._include = None if include is None else set(include)
        self._timeout_s = timeout_s
        self._tools: list[Tool] = []

    # -- lifecycle ---------------------------------------------------------

    async def start(self) -> "McpMount":
        """Connect, discover tools, build kernel Tool wrappers."""
        await self._client.__aenter__()
        try:
            remote = await self._client.list_tools()
        except Exception:
            await self._client.__aexit__(None, None, None)
            raise
        seen: dict[str, str] = {}
        for mcp_tool in remote:
            if self._include is not None and mcp_tool.name not in self._include:
                continue
            kernel_name = _kernel_name(self._prefix, mcp_tool.name)
            if kernel_name in seen:
                raise ValueError(
                    f"mcp tools {seen[kernel_name]!r} and {mcp_tool.name!r} both "
                    f"map to kernel name {kernel_name!r}")
            seen[kernel_name] = mcp_tool.name
            self._tools.append(self._wrap(mcp_tool, kernel_name))
        log.info("mcp_client: mounted %d tool(s) from %r under %r",
                 len(self._tools), self._label, self._prefix)
        return self

    async def stop(self) -> None:
        """Disconnect (terminates a stdio child process). Idempotent."""
        try:
            await self._client.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001 — closing a dead transport must not raise
            log.exception("mcp_client: error while stopping mount %r", self._prefix)

    # -- registration ------------------------------------------------------

    @property
    def tools(self) -> tuple[Tool, ...]:
        return tuple(self._tools)

    def into(self, registry: ToolRegistry) -> None:
        """Register every mounted tool (raises on a name collision)."""
        for tool in self._tools:
            registry.register(tool)

    # -- internals ---------------------------------------------------------

    def _wrap(self, mcp_tool: Any, kernel_name: str) -> Tool:
        risk = self._risk_overrides.get(mcp_tool.name, self._default_risk)
        schema = mcp_tool.input_schema
        if not isinstance(schema, Mapping) or schema.get("type") != "object":
            schema = {"type": "object", "properties": {}}
        description = str(getattr(mcp_tool, "description", "") or "").strip()
        return Tool(
            name=kernel_name,
            description=f"[mcp:{self._label}] {description or 'external MCP tool'}",
            parameters=dict(schema),
            handler=self._handler(str(mcp_tool.name), risk),
            risk=risk,
            timeout_s=self._timeout_s,
            max_retries=0,
        )

    def _handler(self, mcp_name: str, risk: RiskClass):
        async def invoke(call: ToolCall) -> ToolResult:
            try:
                result = await self._client.call_tool(
                    mcp_name, dict(call.args), timeout=self._timeout_s)
            except ToolError as exc:
                # the remote tool answered with a protocol-level error — an
                # expected failure (returned fail results are never retried).
                return ToolResult.fail(call, _clean_error(str(exc)), risk=risk)
            if getattr(result, "is_error", False):
                return ToolResult.fail(
                    call, _clean_error(_text_of(result)), risk=risk)
            data = getattr(result, "data", None)
            return ToolResult.success(
                call, data=jsonable(data if data is not None else _text_of(result)),
                risk=risk)
        return invoke


def _text_of(result: Any) -> str:
    parts = []
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            parts.append(str(text))
    return "\n".join(parts)


# -- mount constructors (all return a STARTED mount) ------------------------


async def mount_stdio(
    command: str,
    args: Sequence[str],
    *,
    prefix: str,
    label: str | None = None,
    env: Mapping[str, str] | None = None,
    cwd: str | None = None,
    default_risk: RiskClass = RiskClass.EXECUTE,
    risk_overrides: Mapping[str, RiskClass] | None = None,
    include: Sequence[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> McpMount:
    """Mount an MCP server launched as a stdio subprocess (the standard host
    integration — e.g. the official reference servers)."""
    client = Client(StdioTransport(
        command, list(args), env=dict(env) if env else None, cwd=cwd),
        timeout=timeout_s)
    return await McpMount(
        client, prefix=prefix, label=label or prefix,
        default_risk=default_risk, risk_overrides=risk_overrides or {},
        include=include, timeout_s=timeout_s,
    ).start()


async def mount_http(
    url: str,
    *,
    prefix: str,
    label: str | None = None,
    headers: Mapping[str, str] | None = None,
    default_risk: RiskClass = RiskClass.EXECUTE,
    risk_overrides: Mapping[str, RiskClass] | None = None,
    include: Sequence[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> McpMount:
    """Mount a remote MCP server over streamable HTTP."""
    client = Client(StreamableHttpTransport(
        url, headers=dict(headers) if headers else None), timeout=timeout_s)
    return await McpMount(
        client, prefix=prefix, label=label or prefix,
        default_risk=default_risk, risk_overrides=risk_overrides or {},
        include=include, timeout_s=timeout_s,
    ).start()


async def mount_inproc(
    server: FastMCP,
    *,
    prefix: str,
    label: str | None = None,
    default_risk: RiskClass = RiskClass.EXECUTE,
    risk_overrides: Mapping[str, RiskClass] | None = None,
    include: Sequence[str] | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
) -> McpMount:
    """Mount an in-process FastMCP instance (tests; embedding a known server
    without spawning a process)."""
    client = Client(server, timeout=timeout_s)
    return await McpMount(
        client, prefix=prefix, label=label or prefix,
        default_risk=default_risk, risk_overrides=risk_overrides or {},
        include=include, timeout_s=timeout_s,
    ).start()
