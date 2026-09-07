"""P2-D tests: consent-gated web research tools + the research→report plan.

Hermetic: fetcher and consent gate injected; the end-to-end test runs the
full research_report_plan through the real P2-C Orchestrator with a fake
search tool and a report tool writing into tmp_path.
"""

import asyncio
from pathlib import Path

import pytest

from kernel.orchestrator import JobQueue, Orchestrator
from kernel.policy import PolicyEngine
from kernel.research import build_research_tools, research_report_plan
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall

HTML = """<html><head><title>Fusion News</title></head>
<body><script>steal()</script><h1>Ignition</h1>
<p>Net energy gain achieved.</p><style>.x{}</style></body></html>"""


def fetch_ok(url: str) -> tuple[int, str]:
    return 200, HTML


def fetch_fail(url: str) -> tuple[int, str]:
    return 404, "gone"


def call(name: str, **args) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=args, source="test")


def run(coro):
    return asyncio.run(coro)


def make_registry(tmp_path: Path, *, consent, fetch=fetch_ok) -> ToolRegistry:
    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch, consent=consent)

    @reg.tool(name="web_search", description="fake search",
              parameters={"type": "object",
                          "properties": {"query": {"type": "string"}}},
              risk=RiskClass.READ)
    def web_search(call):
        return {"first_url": "https://example.com/fusion",
                "query": call.args.get("query", "")}

    @reg.tool(name="fs_write_report", description="writes a report file",
              parameters={"type": "object",
                          "properties": {"name": {"type": "string"},
                                         "text": {"type": "string"}}},
              risk=RiskClass.WRITE)
    def fs_write_report(call):
        path = tmp_path / str(call.args["name"])
        path.write_text(str(call.args["text"]), encoding="utf-8")
        return f"wrote {path}"

    return reg


# ---------------------------------------------------------------- gate

def test_01_no_consent_wired_refuses():
    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch_ok)  # no gate — safe default OFF
    result = run(reg.execute(call("web_read", url="https://x")))
    assert result.ok is False and "disabled" in (result.error or "")


def test_02_consent_false_denies_true_allows():
    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch_ok, enabled=False)
    assert run(reg.execute(call("web_read", url="https://x"))).ok is False

    reg2 = ToolRegistry()
    build_research_tools(reg2, fetch=fetch_ok, enabled=True)
    ok = run(reg2.execute(call("web_read", url="https://x")))
    assert ok.ok is True


def test_03_broken_consent_gate_denies():
    def broken():
        raise RuntimeError("gate down")

    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch_ok, consent=broken)
    result = run(reg.execute(call("web_read", url="https://x")))
    assert result.ok is False


# ---------------------------------------------------------------- read

def test_04_extract_title_text_strips_scripts():
    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch_ok, enabled=True)
    result = run(reg.execute(call("web_read", url="https://x/article")))
    assert result.ok is True
    data = result.data
    assert data["title"] == "Fusion News"
    assert "Net energy gain achieved." in data["text"]
    assert "steal()" not in data["text"] and ".x{}" not in data["text"]
    assert data["truncated"] is False and data["url"] == "https://x/article"


def test_05_http_error_and_bad_url_are_clean_fails():
    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch_fail, enabled=True)
    result = run(reg.execute(call("web_read", url="https://x/gone")))
    assert result.ok is False and "HTTP 404" in (result.error or "")

    reg2 = ToolRegistry()
    build_research_tools(reg2, fetch=fetch_ok, enabled=True)
    result2 = run(reg2.execute(call("web_read", url="ftp://nope")))
    assert result2.ok is False and "http(s)" in (result2.error or "")


def test_06_network_crash_is_a_clean_fail():
    def explode(url: str):
        raise OSError("no route")

    reg = ToolRegistry()
    build_research_tools(reg, fetch=explode, enabled=True)
    result = run(reg.execute(call("web_read", url="https://x")))
    assert result.ok is False and "OSError" in (result.error or "")


def test_07_text_cap_sets_truncated_flag():
    reg = ToolRegistry()
    build_research_tools(reg, fetch=fetch_ok, enabled=True, text_cap=10)
    result = run(reg.execute(call("web_read", url="https://x")))
    assert result.ok is True
    assert result.data["truncated"] is True and len(result.data["text"]) == 10


# ---------------------------------------------------------------- plan

def test_08_plan_builder_shape():
    plan = research_report_plan("fusion energy")
    assert [s["id"] for s in plan] == ["search", "read", "report"]
    assert plan[1]["spec"]["args"] == {"url": "{search.first_url}"}
    assert plan[2]["spec"]["args"]["text"] == "{read.title}\n\n{read.text}"
    with pytest.raises(ValueError, match="topic"):
        research_report_plan("   ")


def test_09_research_report_plan_runs_through_the_orchestrator(tmp_path: Path):
    """J-05 end-to-end at unit scale: search → read → report, via the real
    orchestrator queue — the exact shape of the Phase-2 gate demo."""
    q = JobQueue()
    reg = make_registry(tmp_path, consent=lambda: True)

    async def yes(c, r):
        return True  # report step is WRITE → consented (demo grants it)

    orch = Orchestrator(q, reg, PolicyEngine(), lease_s=30, consent=yes)
    jid = q.enqueue("plan", {"plan": research_report_plan("fusion energy")})
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "done", job.error
    report = tmp_path / "research_report.txt"
    text = report.read_text(encoding="utf-8")
    assert "Fusion News" in text and "Net energy gain achieved." in text
