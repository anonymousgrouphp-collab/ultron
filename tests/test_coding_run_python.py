"""research/12 D3 tests: the run_python self-healing code-exec tool."""

import asyncio

import pytest

from kernel.coding.tools import build_coding_tools
from kernel.types import ToolCall


def make_call(name, **args):
    return ToolCall(id=f"c-{name}", name=name, args=args, source="test")


@pytest.fixture()
def workspace(tmp_path):
    return tmp_path / "ws"


def _run(tool_fn, call):
    return asyncio.run(tool_fn(call))


def _build(workspace):
    from kernel.tools import ToolRegistry
    reg = ToolRegistry()
    build_coding_tools(reg, workspace)
    return reg


def test_01_run_python_prints_and_keeps_artifact(workspace):
    reg = _build(workspace)
    result = asyncio.run(reg.execute(make_call(
        "run_python", code="print('hello ada')", filename="hello.py")))
    assert result.ok
    data = result.data
    assert data["exit_code"] == 0
    assert "hello ada" in data["stdout"]
    assert data["script"].startswith("scripts") and data["script"].endswith("hello.py")
    assert (workspace / data["script"]).is_file()
    assert result.artifacts == (data["script"],)


def test_02_failing_run_surfaces_stderr_for_self_heal(workspace):
    reg = _build(workspace)
    result = asyncio.run(reg.execute(make_call(
        "run_python", code="x = 1 / 0")))
    assert result.ok  # the RUN succeeded; the SCRIPT failed — model observes stderr
    assert result.data["exit_code"] != 0
    assert "ZeroDivisionError" in result.data["stderr"]
    assert result.data["script"]  # artifact path present for the fix-and-rerun


def test_03_pip_install_blocked(workspace):
    reg = _build(workspace)
    result = asyncio.run(reg.execute(make_call(
        "run_python", code="import subprocess\nsubprocess.run(['pip', 'install', 'requests'])")))
    assert not result.ok
    assert "pip-install" in (result.error or "")


def test_04_empty_code_fails_cleanly(workspace):
    reg = _build(workspace)
    result = asyncio.run(reg.execute(make_call("run_python", code="   ")))
    assert not result.ok


def test_05_hostile_filename_stays_in_scripts(workspace):
    reg = _build(workspace)
    result = asyncio.run(reg.execute(make_call(
        "run_python", code="print('ok')", filename="../../evil.py")))
    assert result.ok
    assert ".." not in result.data["script"]
    assert result.data["script"].replace("\\", "/").startswith("scripts/")


def test_06_scripts_accumulate_unique_artifacts(workspace):
    reg = _build(workspace)
    first = asyncio.run(reg.execute(make_call(
        "run_python", code="print(1)", filename="job.py")))
    second = asyncio.run(reg.execute(make_call(
        "run_python", code="print(2)", filename="job.py")))
    assert first.ok and second.ok
    assert first.data["script"] != second.data["script"]


def test_07_run_python_is_risky_and_in_coding_allowlist():
    from kernel.coding.tools import CODING_TOOLS
    assert "run_python" in CODING_TOOLS
