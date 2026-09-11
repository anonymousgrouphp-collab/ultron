"""tests/test_phase_oipq_modules.py — Tests for all Phase O/I/P/Q kernel modules.

Covers:
- kernel/loop/runner.py (AgentRunner)
- kernel/computer/planner.py (GUIPlanner)
- kernel/loop/research_runner.py (ResearchRunner)
- kernel/loop/provider_test.py (ProviderTestRunner)
- kernel/persona/prompt_assembler.py (PromptAssembler)
- kernel/persona/traits.py (PersonaTraits)
- kernel/memory/session_summary.py (session_summary)
- kernel/proactive/dashboard_bridge.py (BusDashboardBridge)
- kernel/diagnostics/health.py (HealthMonitor)
- kernel/diagnostics/cost_tracker.py (CostTracker)
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock


# ═══════════════════════════════════════════════════════════════════════
# AgentRunner tests
# ═══════════════════════════════════════════════════════════════════════

class TestAgentRunner:
    """Tests for kernel.loop.runner.AgentRunner."""

    def test_import(self):
        from kernel.loop.runner import AgentRunner, TaskResult
        assert AgentRunner is not None
        assert TaskResult is not None

    def test_task_result_fields(self):
        from kernel.loop.runner import TaskResult
        r = TaskResult(text="hello", steps=2, finish="stop")
        assert r.text == "hello"
        assert r.steps == 2
        assert r.finish == "stop"
        assert r.tool_names == ()
        assert r.error is None

    def test_task_result_with_tools(self):
        from kernel.loop.runner import TaskResult
        r = TaskResult(text="done", steps=3, finish="stop", tool_names=("web_search", "write_file"))
        assert r.tool_names == ("web_search", "write_file")

    def test_run_task_simple_stop(self):
        from kernel.loop.runner import AgentRunner
        from kernel.gateway import Response
        from kernel.types import RiskClass
        from kernel.policy import PolicyEngine
        from kernel.tools import ToolRegistry, Tool
        from kernel.bus import EventBus

        registry = ToolRegistry()
        def noop() -> str:
            return "ok"
        registry.register(Tool(
            name="noop", description="No-op",
            parameters={"type": "object", "properties": {}},
            handler=noop, risk=RiskClass.READ,
        ))
        policy = PolicyEngine(registry)

        # Fake gateway: text-only stop (no tool calls)
        class FakeGateway:
            async def complete(self, messages, tools=(), response_schema=None):
                return Response(text="All done", finish="stop")

        runner = AgentRunner(gateway=FakeGateway(), policy=policy, registry=registry, bus=EventBus())
        result = asyncio.run(runner.run_task("test"))
        assert result.finish == "stop"
        assert result.text == "All done"
        assert result.steps == 1


# ═══════════════════════════════════════════════════════════════════════
# GUIPlanner tests
# ═══════════════════════════════════════════════════════════════════════

class TestGUIPlanner:
    """Tests for kernel.computer.planner.GUIPlanner."""

    def test_import(self):
        from kernel.computer.planner import GUIPlanner, GUIPlanResult
        assert GUIPlanner is not None
        assert GUIPlanResult is not None

    def test_no_orchestrator_returns_error(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        result = asyncio.run(planner.plan_and_execute("open chrome"))
        assert result.success is False
        assert "Orchestrator not available" in result.error

    def test_extract_app_name(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        assert planner._extract_app_name("open chrome") == "chrome"
        assert planner._extract_app_name("launch notepad") == "notepad"
        assert planner._extract_app_name("do something") == ""

    def test_extract_url(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        url1 = planner._extract_url("navigate to github.com")
        assert "github.com" in url1
        url2 = planner._extract_url("go to example.com")
        assert "example.com" in url2

    def test_extract_typed_text(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        assert planner._extract_typed_text('type "hello world"') == "hello world"
        assert planner._extract_typed_text("write some text here") == "some text here"

    def test_extract_click_target(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        assert planner._extract_click_target("click the submit button") == "the submit button"

    def test_build_plan_open(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        steps = planner._build_plan("open chrome and navigate to github.com")
        assert len(steps) >= 2  # observe + spawn + navigate
        tool_names = [s.get("path", "") for s in steps]
        assert "screen_describe" in tool_names
        assert "spawn_app" in tool_names

    def test_build_plan_type(self):
        from kernel.computer.planner import GUIPlanner
        planner = GUIPlanner()
        steps = planner._build_plan('type "hello" in the search box')
        tool_names = [s.get("path", "") for s in steps]
        assert "ui_act" in tool_names


# ═══════════════════════════════════════════════════════════════════════
# ResearchRunner tests
# ═══════════════════════════════════════════════════════════════════════

class TestResearchRunner:
    """Tests for kernel.loop.research_runner.ResearchRunner."""

    def test_import(self):
        from kernel.loop.research_runner import ResearchRunner
        assert ResearchRunner is not None

    def test_no_orchestrator_returns_message(self):
        from kernel.loop.research_runner import ResearchRunner
        runner = ResearchRunner()
        result = asyncio.run(runner.run_research("AI papers"))
        assert "not available" in result


# ═══════════════════════════════════════════════════════════════════════
# ProviderTestRunner tests
# ═══════════════════════════════════════════════════════════════════════

class TestProviderTestRunner:
    """Tests for kernel.loop.provider_test.ProviderTestRunner."""

    def test_import(self):
        from kernel.loop.provider_test import ProviderTestRunner, ProviderResult
        assert ProviderTestRunner is not None
        assert ProviderResult is not None

    def test_provider_result_fields(self):
        from kernel.loop.provider_test import ProviderResult
        r = ProviderResult(
            provider="gemini", model="test", text="hello",
            steps=1, finish="stop", duration_s=0.5,
        )
        assert r.provider == "gemini"
        assert r.finish == "stop"

    def test_compare_report(self):
        from kernel.loop.provider_test import ProviderTestRunner, ProviderResult
        runner = ProviderTestRunner()
        results = {
            "gemini": ProviderResult(provider="gemini", model="g", text="ok", steps=1, finish="stop", duration_s=0.1),
            "ollama": ProviderResult(provider="ollama", model=        "o", text="fail", steps=0, finish="error",
        duration_s=0, error="timeout"),
        }
        report = runner.compare(results)
        assert "gemini" in report
        assert "ollama" in report
        assert "PASS" in report or "✓" in report


# ═══════════════════════════════════════════════════════════════════════
# PromptAssembler tests
# ═══════════════════════════════════════════════════════════════════════

class TestPromptAssembler:
    """Tests for kernel.persona.prompt_assembler.PromptAssembler."""

    def test_import(self):
        from kernel.persona import PromptAssembler
        assert PromptAssembler is not None

    def test_assemble_no_memory(self):
        from kernel.persona.prompt_assembler import PromptAssembler
        a = PromptAssembler(base_prompt="You are a test assistant.")
        result = a.assemble()
        assert "ULTRON" in result.system_instruction
        assert "test assistant" in result.system_instruction
        assert result.memory_count == 0

    def test_assemble_with_user_name(self):
        from kernel.persona.prompt_assembler import PromptAssembler
        a = PromptAssembler(base_prompt="Hello", user_name="Fatih")
        result = a.assemble()
        assert "Fatih" in result.system_instruction

    def test_assemble_with_extra_sections(self):
        from kernel.persona.prompt_assembler import PromptAssembler
        a = PromptAssembler(base_prompt="Hello")
        result = a.assemble(extra_sections=["[PROACTIVE] Check weather"])
        assert "Check weather" in result.system_instruction

    def test_memory_retrieval_graceful_failure(self):
        """Memory retrieval failure must never break prompt assembly."""
        from kernel.persona.prompt_assembler import PromptAssembler

        class BrokenMemory:
            def search(self, q, k=8):
                raise RuntimeError("DB corrupted")

        a = PromptAssembler(memory=BrokenMemory(), base_prompt="Hello")
        result = a.assemble()
        assert "Hello" in result.system_instruction
        assert result.memory_count == 0

    def test_memory_retrieval_with_fake_memory(self):
        from kernel.persona.prompt_assembler import PromptAssembler

        class FakeResult:
            category = "preferences"
            content = "likes coffee"

        class FakeMemory:
            def search(self, q, k=8):
                return [FakeResult()]

        a = PromptAssembler(memory=FakeMemory(), base_prompt="Hello")
        result = a.assemble(conversation_summary="coffee")
        assert result.memory_count == 1
        assert "likes coffee" in result.system_instruction


# ═══════════════════════════════════════════════════════════════════════
# PersonaTraits tests
# ═══════════════════════════════════════════════════════════════════════

class TestPersonaTraits:
    """Tests for kernel.persona.traits.PersonaTraits."""

    def test_import(self):
        from kernel.persona.traits import PersonaTraits, PersonaStyle
        assert PersonaTraits is not None
        assert PersonaStyle is not None

    def test_ultron_directive(self):
        from kernel.persona.traits import PersonaTraits, PersonaStyle
        t = PersonaTraits(style=PersonaStyle.ULTRON)
        d = t.to_directive()
        assert "ULTRON" in d
        assert "BARITONE" in d

    def test_jarvis_directive(self):
        from kernel.persona.traits import PersonaTraits, PersonaStyle
        t = PersonaTraits(style=PersonaStyle.JARVIS)
        d = t.to_directive()
        assert "J.A.R.V.I.S" in d
        assert "BRITISH" in d

    def test_high_humor_adds_modifier(self):
        from kernel.persona.traits import PersonaTraits, PersonaStyle
        t = PersonaTraits(style=PersonaStyle.ULTRON, humor=0.8)
        d = t.to_directive()
        assert "HUMOR" in d

    def test_low_verbosity_adds_modifier(self):
        from kernel.persona.traits import PersonaTraits, PersonaStyle
        t = PersonaTraits(style=PersonaStyle.ULTRON, verbosity=0.1)
        d = t.to_directive()
        assert "TERSE" in d

    def test_from_config(self):
        from kernel.persona.traits import PersonaTraits
        t = PersonaTraits.from_config({"persona_style": "jarvis", "persona_humor": 0.5})
        assert t.style.value == "jarvis"
        assert t.humor == 0.5

    def test_from_config_defaults(self):
        from kernel.persona.traits import PersonaTraits
        t = PersonaTraits.from_config({})
        assert t.style.value == "ultron"
        assert t.formality == 0.8

    def test_build_persona_directive(self):
        from kernel.persona.traits import build_persona_directive
        d = build_persona_directive({"persona_style": "friday"})
        assert "F.R.I.D.A.Y" in d


# ═══════════════════════════════════════════════════════════════════════
# Session summary tests
# ═══════════════════════════════════════════════════════════════════════

class TestSessionSummary:
    """Tests for kernel.memory.session_summary."""

    def test_import(self):
        from kernel.memory.session_summary import generate_session_summary
        assert generate_session_summary is not None

    def test_empty_session(self):
        from kernel.memory.session_summary import generate_session_summary
        s = generate_session_summary(
            user_messages=[], tool_calls=[], assistant_responses=[]
        )
        assert s.summary_text == "Empty session"
        assert s.tools_used == ()

    def test_session_with_tools(self):
        from kernel.memory.session_summary import generate_session_summary
        s = generate_session_summary(
            user_messages=["hello"],
            tool_calls=["web_search", "write_file", "web_search"],
            assistant_responses=["Here's the result"],
        )
        assert "web_search" in s.tools_used
        assert "write_file" in s.tools_used
        assert len(s.tools_used) == 2  # deduplicated

    def test_topic_extraction(self):
        from kernel.memory.session_summary import generate_session_summary
        s = generate_session_summary(
            user_messages=["Research the latest AI papers", "What about quantum computing"],
            tool_calls=[],
            assistant_responses=[],
        )
        assert len(s.user_topics) > 0

    def test_memory_storage(self):
        from kernel.memory.session_summary import generate_session_summary

        class FakeMemory:
            def __init__(self):
                self.stored = []
            def remember(self, **kwargs):
                self.stored.append(kwargs)

        mem = FakeMemory()
        _ = generate_session_summary(
            user_messages=["test"],
            tool_calls=["echo"],
            assistant_responses=["ok"],
            memory=mem,
        )
        assert len(mem.stored) == 1
        assert mem.stored[0]["entity"] == "session_summary"

    def test_memory_failure_doesnt_break(self):
        from kernel.memory.session_summary import generate_session_summary

        class BrokenMemory:
            def remember(self, **kwargs):
                raise RuntimeError("DB full")

        s = generate_session_summary(
            user_messages=["test"], tool_calls=[], assistant_responses=[],
            memory=BrokenMemory(),
        )
        assert s.summary_text != ""  # still generated


# ═══════════════════════════════════════════════════════════════════════
# BusDashboardBridge tests
# ═══════════════════════════════════════════════════════════════════════

class TestBusDashboardBridge:
    """Tests for kernel.proactive.dashboard_bridge.BusDashboardBridge."""

    def test_import(self):
        from kernel.proactive.dashboard_bridge import BusDashboardBridge
        assert BusDashboardBridge is not None

    def test_attach_detach(self):
        from kernel.proactive.dashboard_bridge import BusDashboardBridge
        from kernel.bus import EventBus

        bus = EventBus()
        dashboard = MagicMock()
        bridge = BusDashboardBridge(bus, dashboard)
        bridge.attach()
        assert len(bridge._subscriptions) == 5  # W5: + health.*
        bridge.detach()
        assert len(bridge._subscriptions) == 0

    def test_tool_event_broadcast(self):
        from kernel.proactive.dashboard_bridge import BusDashboardBridge
        from kernel.bus import EventBus
        from kernel.types import Event

        bus = EventBus()
        dashboard = MagicMock()
        bridge = BusDashboardBridge(bus, dashboard)
        bridge.attach()

        # Simulate a tool event
        event = Event(type="tool.completed", payload={"name": "web_search", "ok": True})
        bridge._on_tool_event(event)

        # Dashboard broadcast should have been called (via asyncio.create_task)
        # We can't easily test the async broadcast, but we can test the method doesn't crash
        bridge.detach()


# ═══════════════════════════════════════════════════════════════════════
# HealthMonitor tests
# ═══════════════════════════════════════════════════════════════════════

class TestHealthMonitor:
    """Tests for kernel.diagnostics.health.HealthMonitor."""

    def test_import(self):
        from kernel.diagnostics.health import HealthMonitor, HealthStatus
        assert HealthMonitor is not None
        assert HealthStatus.HEALTHY == "healthy"

    def test_healthy_system(self):
        from kernel.diagnostics.health import HealthMonitor, HealthStatus
        monitor = HealthMonitor()
        report = asyncio.run(monitor.check_health())
        assert report.status == HealthStatus.HEALTHY
        assert report.issues == []

    def test_record_error(self):
        from kernel.diagnostics.health import HealthMonitor
        monitor = HealthMonitor()
        monitor.record_error("tool", "web_search")
        monitor.record_error("tool", "web_search")
        summary = monitor.get_error_summary()
        assert summary["tool"] == 2

    def test_high_errors_degraded(self):
        from kernel.diagnostics.health import HealthMonitor, HealthStatus
        monitor = HealthMonitor()
        for i in range(15):
            monitor.record_error("component", f"error_{i}")
        report = asyncio.run(monitor.check_health())
        assert report.status == HealthStatus.DEGRADED or report.status == HealthStatus.UNHEALTHY
        assert len(report.issues) > 0

    def test_disk_space_check(self, tmp_path):
        from kernel.diagnostics.health import HealthMonitor
        monitor = HealthMonitor(base_dir=tmp_path)
        report = asyncio.run(monitor.check_health())
        assert "disk_free_gb" in report.metrics

    def test_memory_db_check(self, tmp_path):
        from kernel.diagnostics.health import HealthMonitor
        db_path = tmp_path / "memory.sqlite3"
        db_path.write_bytes(b"x" * 1024)
        monitor = HealthMonitor(base_dir=tmp_path)
        report = asyncio.run(monitor.check_health())
        assert "memory_db_size_mb" in report.metrics


# ═══════════════════════════════════════════════════════════════════════
# CostTracker tests
# ═══════════════════════════════════════════════════════════════════════

class TestCostTracker:
    """Tests for kernel.diagnostics.cost_tracker.CostTracker."""

    def test_import(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        assert CostTracker is not None

    def test_record_usage(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker()
        record = tracker.record_usage(
            provider="gemini", input_tokens=1000, output_tokens=500
        )
        assert record.cost_usd > 0
        assert record.provider == "gemini"

    def test_ollama_free(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker()
        record = tracker.record_usage(
            provider="ollama", input_tokens=1000, output_tokens=500
        )
        assert record.cost_usd == 0.0

    def test_get_report(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker()
        tracker.record_usage(provider="gemini", input_tokens=1000, output_tokens=500)
        tracker.record_usage(provider="ollama", input_tokens=2000, output_tokens=1000)
        report = tracker.get_report()
        assert report.record_count == 2
        assert report.total_cost_usd > 0
        assert "gemini" in report.by_provider
        assert "ollama" in report.by_provider

    def test_budget_check_within(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker(budget_usd=10.0)
        tracker.record_usage(provider="gemini", input_tokens=100, output_tokens=50)
        ok, msg = tracker.check_budget()
        assert ok is True

    def test_budget_check_exceeded(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker(budget_usd=0.0001)
        tracker.record_usage(provider="gemini", input_tokens=10000, output_tokens=5000)
        ok, msg = tracker.check_budget()
        assert ok is False
        assert "exceeded" in msg

    def test_suggest_provider_simple(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker()
        assert tracker.suggest_provider("simple") == "ollama"

    def test_suggest_provider_complex(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker()
        assert tracker.suggest_provider("complex") == "gemini"

    def test_suggest_provider_budget_exceeded(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker(budget_usd=0.0001)
        tracker.record_usage(provider="gemini", input_tokens=10000, output_tokens=5000)
        assert tracker.suggest_provider("complex") == "ollama"

    def test_report_since_filter(self):
        from kernel.diagnostics.cost_tracker import CostTracker
        tracker = CostTracker()
        tracker.record_usage(provider="gemini", input_tokens=100, output_tokens=50)
        time.sleep(0.01)
        cutoff = time.time()
        time.sleep(0.01)
        tracker.record_usage(provider="gemini", input_tokens=200, output_tokens=100)
        report = tracker.get_report(since=cutoff)
        assert report.record_count == 1


# ═══════════════════════════════════════════════════════════════════════
# LiveSession / build_live_config tests (import-only, no SDK)
# ═══════════════════════════════════════════════════════════════════════

class TestLiveSessionImport:
    """Import-only tests for kernel.gateway.live (SDK may not be installed)."""

    def test_import_live_module(self):
        from kernel.gateway import live
        assert hasattr(live, "LiveSession")
        assert hasattr(live, "build_live_config")
        assert hasattr(live, "FunctionResponse")

    def test_gateway_exports(self):
        from kernel.gateway import LiveSession, build_live_config
        assert LiveSession is not None
        assert build_live_config is not None
