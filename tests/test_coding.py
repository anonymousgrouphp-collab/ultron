"""P2-E tests: coding subagent toolkit — workspace jail, sandboxed run, J-11
agent step through the orchestrator. Hermetic (tmp workspaces, real python
subprocesses only for run_command)."""

import asyncio
import sys
from pathlib import Path


from kernel.coding import CODING_TOOLS, build_coding_tools, spawn_coding_job
from kernel.gateway import Response
from kernel.orchestrator import JobQueue, Orchestrator
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall

PYTHON = sys.executable


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=args, source="test")


def run(coro):
    return asyncio.run(coro)


def make_tools(tmp_path: Path) -> ToolRegistry:
    reg = ToolRegistry()
    build_coding_tools(reg, tmp_path / "ws", run_timeout_s=15.0)
    return reg


# ---------------------------------------------------------------- jail

def test_01_write_read_roundtrip(tmp_path: Path):
    reg = make_tools(tmp_path)
    wrote = run(reg.execute(call("write_file", path="src/app.py",
                                 text="print('hi')\n")))
    assert wrote.ok is True and wrote.data["bytes"] == len("print('hi')\n")
    assert (tmp_path / "ws" / "src" / "app.py").read_text(encoding="utf-8") == \
        "print('hi')\n"
    read = run(reg.execute(call("read_file", path="src/app.py")))
    assert read.ok is True and read.data["text"] == "print('hi')\n"


def test_02_jail_escape_refused(tmp_path: Path):
    reg = make_tools(tmp_path)
    for bad in ("../outside.txt", "..\\outside.txt", "a/../../b.txt",
                str(tmp_path.parent / "evil.txt")):
        result = run(reg.execute(call("write_file", path=bad, text="x")))
        assert result.ok is False and "escapes" in (result.error or "")
        result = run(reg.execute(call("read_file", path=bad)))
        assert result.ok is False and "escapes" in (result.error or "")
    assert not (tmp_path / "outside.txt").exists()


def test_03_list_files_skips_noise(tmp_path: Path):
    reg = make_tools(tmp_path)
    run(reg.execute(call("write_file", path="a.py", text="x")))
    run(reg.execute(call("write_file", path="pkg/inner.py", text="x")))
    run(reg.execute(call("write_file", path=".git/config", text="x")))
    run(reg.execute(call("write_file", path="pkg/__pycache__/c.pyc", text="x")))
    listing = run(reg.execute(call("list_files")))
    files = listing.data["files"]
    assert "a.py" in files
    assert any(f.endswith("inner.py") for f in files)
    assert listing.data["total"] == 2
    assert ".git" not in str(files)
    assert "__pycache__" not in str(files)


# ---------------------------------------------------------------- run

def test_04_run_command_executes_and_captures(tmp_path: Path):
    reg = make_tools(tmp_path)
    result = run(reg.execute(call(
        "run_command", argv=[PYTHON, "-c", "print(2+3)"])))
    assert result.ok is True
    assert result.data["exit_code"] == 0
    assert result.data["stdout"].strip() == "5"
    assert result.data["sandbox"] in ("job-object", "plain")


def test_05_nonzero_exit_is_a_structured_result(tmp_path: Path):
    reg = make_tools(tmp_path)
    result = run(reg.execute(call(
        "run_command", argv=[PYTHON, "-c", "import sys; sys.exit(3)"])))
    assert result.ok is True
    assert result.data["exit_code"] == 3


def test_06_timeout_kills_and_reports_clean(tmp_path: Path):
    reg = ToolRegistry()
    build_coding_tools(reg, tmp_path / "ws", run_timeout_s=1.0)
    result = run(reg.execute(call(
        "run_command", argv=[PYTHON, "-c", "import time; time.sleep(10)"])))
    assert result.ok is False and "timed out" in (result.error or "")


def test_07_pip_install_blocked(tmp_path: Path):
    reg = make_tools(tmp_path)
    for argv in ([PYTHON, "-m", "pip", "install", "requests"],
                 ["pip", "install", "requests"]):
        result = run(reg.execute(call("run_command", argv=argv)))
        assert result.ok is False and "disabled" in (result.error or "")


def test_08_env_scrubbed_of_secrets(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "super-secret-value")
    monkeypatch.setenv("MY_MASTER_TOKEN", "tok")
    reg = make_tools(tmp_path)
    result = run(reg.execute(call(
        "run_command",
        argv=[PYTHON, "-c",
              "import os; print(repr(os.environ.get('GEMINI_API_KEY')), "
              "repr(os.environ.get('MY_MASTER_TOKEN')))"])))
    assert result.ok is True
    assert "super-secret-value" not in result.data["stdout"]
    assert "None None" in result.data["stdout"].replace("'", "")


def test_09_output_capped(tmp_path: Path):
    reg = ToolRegistry()
    build_coding_tools(reg, tmp_path / "ws", run_timeout_s=20.0, output_cap=1000)
    result = run(reg.execute(call(
        "run_command", argv=[PYTHON, "-c", "print('x' * 100000)"])))
    assert result.ok is True
    assert len(result.data["stdout"]) < 2000
    assert "truncated" in result.data["stdout"]


def test_10_bad_argv_is_a_clean_fail(tmp_path: Path):
    reg = make_tools(tmp_path)
    result = run(reg.execute(call("run_command", argv="not-a-list")))
    assert result.ok is False and "argv" in (result.error or "")


def test_11_allow_run_false_registers_read_write_only(tmp_path: Path):
    reg = ToolRegistry()
    build_coding_tools(reg, tmp_path / "ws", allow_run=False)
    assert "run_command" not in reg
    assert set(reg.names()) == {"list_files", "read_file", "write_file"}


def test_12_risk_map_for_policy():
    reg = ToolRegistry()
    build_coding_tools(reg, Path("."), allow_run=True)
    risks = reg.risks()
    assert risks["list_files"] is RiskClass.READ
    assert risks["read_file"] is RiskClass.READ
    assert risks["write_file"] is RiskClass.WRITE
    assert risks["run_command"] is RiskClass.EXECUTE


# ---------------------------------------------------------------- J-11 agent

class FakeCoderGateway:
    """Turn 1: write a file. Turn 2: run it. Turn 3: report success."""

    def __init__(self) -> None:
        self.turns = 0

    async def complete(self, messages, tools=(), response_schema=None):
        self.turns += 1
        if self.turns == 1:
            return Response(text="writing", finish="tool_calls", tool_calls=(
                ToolCall(id="t1", name="write_file",
                         args={"path": "main.py",
                               "text": "print('hello from J-11')"},
                         source="model"),))
        if self.turns == 2:
            return Response(text="running", finish="tool_calls", tool_calls=(
                ToolCall(id="t2", name="run_command",
                         args={"argv": [PYTHON, "main.py"]}, source="model"),))
        return Response(text="Task complete: the script prints the greeting.",
                        finish="stop")


def test_13_coding_job_runs_the_write_run_fix_loop(tmp_path: Path):
    q = JobQueue()
    reg = make_tools(tmp_path)

    async def yes(c, r):
        return True  # the demo consent: WRITE + EXECUTE tools allowed

    orch = Orchestrator(q, reg, PolicyEngine(), gateway=FakeCoderGateway(),
                        consent=yes, lease_s=30)
    jid = spawn_coding_job(q, task="print a greeting")
    assert set(reg.names()) == set(CODING_TOOLS)

    async def _go():
        await orch.run_worker(worker="w", max_jobs=1)
        return q.get(jid)

    job = run(_go())
    assert job.status == "done", job.error
    out = job.result["outputs"]["agent"]
    assert out["finish"] == "stop"
    assert "Task complete" in out["text"]
    assert (tmp_path / "ws" / "main.py").read_text(encoding="utf-8") == \
        "print('hello from J-11')"
    run_steps = [t for t in out["trace"] if t["kind"] == "tool"]
    assert any("run_command" in str(t) for t in run_steps)
