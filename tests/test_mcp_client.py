"""P2-B tests: MCP client mounting external servers as kernel tools.

Hermetic: in-process FastMCP instances for unit tests + one real stdio
subprocess test proving the external-server path end to end (spawn → list →
call → file on disk → stop).
"""

import asyncio
import sys
import textwrap
from pathlib import Path

import pytest
from fastmcp import FastMCP

from kernel import RiskClass, ToolCall
from kernel.mcp_client import _kernel_name, mount_inproc, mount_stdio
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry

SERVER_SRC = textwrap.dedent('''
    import sys
    from pathlib import Path
    from fastmcp import FastMCP
    root = Path(sys.argv[1]); root.mkdir(parents=True, exist_ok=True)
    mcp = FastMCP("fixture-fs")

    @mcp.tool
    def write_report(name: str, text: str) -> str:
        """writes a report file into the root dir"""
        (root / name).write_text(text, encoding="utf-8")
        return f"wrote {name}"

    @mcp.tool
    def read_report(name: str) -> str:
        """reads a report file from the root dir"""
        return (root / name).read_text(encoding="utf-8")

    mcp.run()
''')


def make_server() -> FastMCP:
    mcp = FastMCP("fixture")

    @mcp.tool
    def echo(text: str) -> dict:
        """echoes its input"""
        return {"echo": text, "len": len(text)}

    @mcp.tool
    def flaky() -> str:
        """always raises remotely"""
        raise RuntimeError("REMOTE internals: secret stuff")

    @mcp.tool
    def add(a: int, b: int) -> int:
        """adds two integers"""
        return a + b

    return mcp


def call(tool: str, **args) -> ToolCall:
    return ToolCall(id=f"c-{tool}", name=tool, args=args, source="test")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- mounting

def test_01_mount_exposes_kernel_tools_with_fidelity():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx")
        try:
            return mount.tools
        finally:
            await mount.stop()

    tools = {t.name: t for t in run(_go())}
    assert set(tools) == {"fx_add", "fx_echo", "fx_flaky"}
    assert tools["fx_echo"].description.startswith("[mcp:fx] echoes its input")
    assert tools["fx_echo"].parameters["properties"]["text"] == {"type": "string"}
    assert tools["fx_echo"].risk is RiskClass.EXECUTE  # untrusted default
    assert tools["fx_add"].risk is RiskClass.EXECUTE


def test_02_kernel_name_mapping():
    assert _kernel_name("fs", "read.file") == "fs_read_file"
    assert _kernel_name("fs", "write-file") == "fs_write_file"
    with pytest.raises(ValueError, match="snake_case"):
        _kernel_name("Bad-Prefix", "x")


def test_03_calls_flow_through_the_registry():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx")
        try:
            reg = ToolRegistry()
            mount.into(reg)
            return await reg.execute(call("fx_echo", text="hi"))
        finally:
            await mount.stop()

    result = run(_go())
    assert result.ok is True
    assert result.data == {"echo": "hi", "len": 2}
    assert result.risk is RiskClass.EXECUTE


def test_04_remote_error_becomes_a_clean_fail_result():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx")
        try:
            reg = ToolRegistry()
            mount.into(reg)
            return await reg.execute(call("fx_flaky"))
        finally:
            await mount.stop()

    result = run(_go())
    assert result.ok is False
    assert "REMOTE internals" in (result.error or "")  # remote-controlled text, capped
    assert len(result.error or "") <= 300


def test_05_risk_overrides_relax_trusted_tools_only():
    async def _go():
        mount = await mount_inproc(
            make_server(), prefix="fx",
            risk_overrides={"echo": RiskClass.READ})
        try:
            reg = ToolRegistry()
            mount.into(reg)
            return reg.risks()
        finally:
            await mount.stop()

    risks = run(_go())
    assert risks == {"fx_echo": RiskClass.READ, "fx_add": RiskClass.EXECUTE,
                     "fx_flaky": RiskClass.EXECUTE}


def test_06_include_filter_mounts_a_subset():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx", include=["add"])
        try:
            return mount.tools
        finally:
            await mount.stop()

    assert [t.name for t in run(_go())] == ["fx_add"]


def test_07_policy_gates_mounted_tools_fail_safe():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx")
        try:
            reg = ToolRegistry()
            mount.into(reg)
            policy = PolicyEngine()
            denied = await policy.run(call("fx_echo", text="x"), reg, consent=None)

            async def yes(c, r):
                return True

            allowed = await policy.run(call("fx_echo", text="x"), reg, consent=yes)
            return denied, allowed
        finally:
            await mount.stop()

    denied, allowed = run(_go())
    assert denied.ok is False and "consent" in (denied.error or "")
    assert allowed.ok is True and allowed.data == {"echo": "x", "len": 1}


def test_08_real_stdio_subprocess_roundtrip(tmp_path: Path):
    script = tmp_path / "fixture_fs_server.py"
    script.write_text(SERVER_SRC, encoding="utf-8")
    root = tmp_path / "root"

    async def _go():
        mount = await mount_stdio(
            sys.executable, [str(script), str(root)],
            prefix="fs", label="fixture-fs", risk_overrides={
                "write_report": RiskClass.WRITE, "read_report": RiskClass.READ})
        try:
            reg = ToolRegistry()
            mount.into(reg)
            wrote = await reg.execute(
                call("fs_write_report", name="gate.txt", text="report body"))
            read = await reg.execute(call("fs_read_report", name="gate.txt"))
            risks = reg.risks()
            return wrote, read, risks
        finally:
            await mount.stop()
        # stop() must terminate the child process

    wrote, read, risks = run(_go())
    assert wrote.ok is True and wrote.data == "wrote gate.txt"
    assert read.ok is True and read.data == "report body"
    assert risks["fs_write_report"] is RiskClass.WRITE  # per-tool override
    assert risks["fs_read_report"] is RiskClass.READ
    assert (root / "gate.txt").read_text(encoding="utf-8") == "report body"


def test_09_stop_is_idempotent():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx")
        await mount.stop()
        await mount.stop()  # second close must not raise
        return True

    assert run(_go()) is True


def test_10_call_after_stop_is_a_clean_registry_fail():
    async def _go():
        mount = await mount_inproc(make_server(), prefix="fx")
        reg = ToolRegistry()
        mount.into(reg)
        await mount.stop()
        return await reg.execute(call("fx_echo", text="x"))

    result = run(_go())
    assert result.ok is False  # closed transport → registry's clean crash path
