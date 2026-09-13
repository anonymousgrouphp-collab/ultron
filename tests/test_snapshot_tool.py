"""research/12 D8 tests: the system_snapshot one-call state tool."""

import asyncio
from types import SimpleNamespace

from kernel.diagnostics.snapshot import build_snapshot_tools
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


class FakeUsers:
    def __init__(self, name="Sir"):
        self._name = name

    def get_current_user(self):
        return SimpleNamespace(display_name=self._name)


def make_call(name, **args):
    return ToolCall(id=f"c-{name}", name=name, args=args, source="test")


def _build(**deps):
    reg = ToolRegistry()
    build_snapshot_tools(reg, **deps)
    return reg


def test_01_snapshot_with_all_stores(tmp_path):
    from kernel.memory import MemoryEngine
    from kernel.orchestrator import JobQueue

    queue = JobQueue(tmp_path / "jobs.sqlite3")
    queue.enqueue("plan", {"plan": [{"id": "s1", "kind": "tool",
                                     "spec": {"name": "web_read",
                                              "args": {}}}]},
                  title="t")
    memory = MemoryEngine(tmp_path / "mem.sqlite3")
    reg = _build(job_queue=queue, memory=memory, users=FakeUsers("Sir"))
    result = asyncio.run(reg.execute(make_call("system_snapshot")))
    assert result.ok
    data = result.data
    assert data["user"] == "Sir"
    assert data["jobs"]["queued"] == 1
    assert data["jobs"]["running"] == 0
    assert isinstance(data["memory_facts"], int)
    assert "time" in data


def test_02_snapshot_without_stores_still_answers(tmp_path):
    reg = _build()
    result = asyncio.run(reg.execute(make_call("system_snapshot")))
    assert result.ok
    assert "user" not in result.data
    assert "jobs" not in result.data


def test_03_broken_store_degrades_not_crashes(tmp_path):
    class Broken:
        def list(self, status=None, limit=50):
            raise RuntimeError("db gone")

    reg = _build(job_queue=Broken(), users=FakeUsers())
    result = asyncio.run(reg.execute(make_call("system_snapshot")))
    assert result.ok
    assert result.data["jobs"]["queued"] == "(unavailable)"


def test_04_snapshot_is_read_risk():
    reg = _build()
    assert reg.risks()["system_snapshot"] == RiskClass.READ
