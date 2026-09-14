"""research/12 D11+D12 tests: project workspaces + transcript export."""

import asyncio

import pytest

from kernel.coding.projects import build_project_tools
from kernel.diagnostics.snapshot import build_transcript_tools
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


def make_call(tool_name, **args):
    return ToolCall(id=f"c-{tool_name}", name=tool_name, args=args,
                    source="test")


# ------------------------------------------------------------- D11 projects

@pytest.fixture()
def proj_reg(tmp_path):
    reg = ToolRegistry()
    build_project_tools(reg, tmp_path / "ws")
    return reg, tmp_path / "ws"


def test_11_create_switch_list_round_trip(proj_reg):
    reg, ws = proj_reg
    made = asyncio.run(reg.execute(make_call("project_create", name="Demo App")))
    assert made.ok and made.data["created"] is True
    assert (ws / "projects" / "Demo App").is_dir()
    # creating an existing name switches to it instead of failing
    again = asyncio.run(reg.execute(make_call("project_create", name="Demo App")))
    assert again.ok and again.data["created"] is False
    listing = asyncio.run(reg.execute(make_call("project_list")))
    assert listing.data["projects"] == ["Demo App"]
    assert listing.data["current"] == "Demo App"


def test_12_project_names_are_sanitized(proj_reg):
    reg, ws = proj_reg
    result = asyncio.run(reg.execute(make_call("project_create", name="../../evil")))
    assert result.ok
    assert ".." not in result.data["path"]
    assert (ws / "projects" / result.data["project"]).is_dir()


def test_13_switch_unknown_project_lists_existing(proj_reg):
    reg, _ = proj_reg
    result = asyncio.run(reg.execute(make_call("project_switch", name="ghost")))
    assert not result.ok
    assert "none" in result.error


def test_14_project_context_walks_and_reads(proj_reg):
    reg, ws = proj_reg
    asyncio.run(reg.execute(make_call("project_create", name="demo")))
    project = ws / "projects" / "demo"
    (project / "notes.txt").write_text("the plan is simple", encoding="utf-8")
    (project / "data.bin").write_bytes(b"\x00\x01\x02")
    result = asyncio.run(reg.execute(make_call("project_context")))
    assert result.ok
    assert result.data["project"] == "demo"
    assert "notes.txt" in result.data["files"]
    assert "data.bin" in result.data["files"]
    assert "the plan is simple" in result.data["context"]
    assert "(binary or oversized" in result.data["context"]


def test_15_project_context_without_current_fails_cleanly(proj_reg):
    reg, _ = proj_reg
    result = asyncio.run(reg.execute(make_call("project_context")))
    assert not result.ok


# ---------------------------------------------------------- D12 transcript

def test_16_transcript_export_writes_timestamped_file(tmp_path):
    reg = ToolRegistry()
    build_transcript_tools(reg,
                           transcript_provider=lambda: ["You: hello",
                                                        "ULTRON: hi"],
                           out_dir=tmp_path / "t")
    result = asyncio.run(reg.execute(make_call("export_transcript")))
    assert result.ok
    from pathlib import Path
    path = Path(result.data["path"])
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "You: hello" in text and "ULTRON: hi" in text
    assert result.data["lines"] == 2


def test_17_transcript_export_empty_fails_cleanly(tmp_path):
    reg = ToolRegistry()
    build_transcript_tools(reg, transcript_provider=list,
                           out_dir=tmp_path / "t")
    result = asyncio.run(reg.execute(make_call("export_transcript")))
    assert not result.ok


def test_18_transcript_export_is_write_risk(tmp_path):
    reg = ToolRegistry()
    build_transcript_tools(reg, transcript_provider=list,
                           out_dir=tmp_path / "t")
    assert reg.risks()["export_transcript"] == RiskClass.WRITE
