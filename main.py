import platform as _platform
import subprocess as _subprocess

# ── Nuclear: force CREATE_NO_WINDOW on EVERY subprocess call on Windows ───────
# This patches Popen itself, so no per-file flag is needed anywhere.
if _platform.system() == "Windows":
    _OrigPopen = _subprocess.Popen

    class _Popen(_OrigPopen):
        def __init__(self, args, **kw):
            kw["creationflags"] = kw.get("creationflags", 0) | _subprocess.CREATE_NO_WINDOW
            kw.pop("startupinfo", None)   # drop any stale/shared STARTUPINFO
            super().__init__(args, **kw)

    _subprocess.Popen = _Popen
# ─────────────────────────────────────────────────────────────────────────────

import asyncio
import threading
import time
import sys
import traceback

from ui import UltronUI

# Phase W0: the god module's task/handler blocks now live in app/ mixins —
# UltronLive inherits them so callers and characterization pins are unchanged.
from app.audio import AudioTasksMixin
from app.briefing import BriefingMixin
from app.handlers import LegacyHandlersMixin
from app.monitors import MonitorTasksMixin

from actions.system_monitor    import SystemMonitor
from config import loader
from kernel.bus import EventBus
from kernel.gateway import DEFAULT_GEMINI_LIVE_MODEL, GatewaySettings
from kernel.gateway.live import FunctionResponse, LiveSession, build_live_config
from kernel.loop.runner import AgentRunner, TaskResult
from kernel.persona import PromptAssembler
from kernel.proactive.dashboard_bridge import BusDashboardBridge
from kernel.computer.planner import GUIPlanner
from kernel.loop.research_runner import ResearchRunner
from kernel.loop.provider_test import ProviderTestRunner
from kernel.diagnostics.health import HealthMonitor
from kernel.diagnostics.cost_tracker import CostTracker
from kernel.legacy import LegacyToolRuntime
from kernel.memory import MemoryEngine, register_memory_tools
from kernel.orchestrator import JobQueue, Orchestrator
from kernel.computer import InputGateway, build_computer_tools
from kernel.coding import build_coding_tools
from kernel.research import build_research_tools
from kernel.proactive import (
    ConsentClass as ProactiveConsentClass,
    ProactiveEngine as KernelProactiveEngine,
    TriggerRule,
)
from kernel.types import Event, RiskClass, ToolCall


BASE_DIR    = loader.get_base_dir()
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = DEFAULT_GEMINI_LIVE_MODEL  # model string centralized in the gateway (Kill List #3)
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 512

class ApiKeyMissing(Exception):
    """Raised when config/api_keys.json is missing, broken, or has no real key."""


class _UsageTrackingGateway:
    """Phase W5: transparent wrapper that feeds every completion's token
    usage into the CostTracker (the audit's '/cost never records' finding).
    Sits at the ONE seam all agent-loop/orchestrator traffic crosses."""

    def __init__(self, inner, cost_tracker):
        self._inner = inner
        self._cost = cost_tracker

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def complete(self, messages, tools=(), response_schema=None):
        response = await self._inner.complete(messages, tools=tools,
                                              response_schema=response_schema)
        try:
            usage = dict(response.usage or {})
            if usage:
                self._cost.record_usage(
                    provider=response.provider or "unknown",
                    model=response.model or "",
                    input_tokens=int(usage.get("input", 0)),
                    output_tokens=int(usage.get("output", 0)),
                )
        except Exception:
            pass  # observability must never break the model path
        return response


def _get_api_key() -> str:
    key = loader.get_api_key()   # None when missing, empty, or placeholder
    if key is None:
        raise ApiKeyMissing(
            "config/api_keys.json is missing, invalid, or has no real Gemini key"
        )
    return key


def _load_system_prompt() -> str:
    try:
        return PROMPT_PATH.read_text(encoding="utf-8")
    except Exception:
        return (
            "You are ULTRON, a highly intelligent AI assistant. "
            "Be concise, direct, and always use the provided tools to complete tasks. "
            "Never simulate or guess results — always call the appropriate tool."
        )

from core.tool_declarations import TOOL_DECLARATIONS

# --- Plugin system ---


class ConsentGate:
    """Bridges kernel ASK decisions to a UI yes/no dialog (Phase R1).

    The dialog opens on the Qt main thread via the window's queued
    `_consent_request` signal — the codebase's established cross-thread
    pattern (same as write_log). The loop waits with a timeout. Timeout,
    missing UI, or a broken dialog FAIL CLOSED — deny, never execute.
    """

    def __init__(self, ui, timeout_s: float = 45.0):
        self._ui = ui
        self._timeout_s = timeout_s

    async def request(self, call, risk) -> bool:
        win = getattr(self._ui, "_win", None)
        if win is None or not callable(getattr(win, "_consent_request", None)):
            return False
        done = asyncio.Event()
        loop = asyncio.get_running_loop()
        answer: list[bool] = [False]

        def on_answer(allowed: bool) -> None:
            answer[0] = bool(allowed)
            loop.call_soon_threadsafe(done.set)

        win._consent_request(str(call.name), str(risk.value),
                             str(dict(call.args)), on_answer)
        try:
            await asyncio.wait_for(done.wait(), timeout=self._timeout_s)
        except asyncio.TimeoutError:
            print(f"[Consent] {call.name} - no answer in "
                  f"{self._timeout_s}s, denied")
            return False
        return answer[0]


class UltronLive(
    LegacyHandlersMixin,    # the 20 _handle_* legacy bridges (Phase W0)
    AudioTasksMixin,        # mic/recv/play audio pumps (Phase W0)
    BriefingMixin,          # startup greeting + proactive check-in (Phase W0)
    MonitorTasksMixin,      # system monitor + proactive tick + relays (Phase W0)
):

    def __init__(self, ui: UltronUI):
        self.ui             = ui
        self._asst_name     = "ULTRON"   # updated each session from config
        self.session              = None
        self.audio_in_queue       = None
        self.out_queue            = None
        self._loop                = None
        self._is_speaking         = False
        self._speaking_lock       = threading.Lock()
        self._phone_active        = False   # True while phone mic is streaming; pauses PC mic
        self._pending_vision       = None    # (img_bytes, mime_type, question, angle) to inject after tool response
        self._vision_cam_active    = False   # True if camera was opened for vision → auto-close after response
        self._vision_close_pending = False   # True after vision injected; next turn_complete closes camera
        self._vision_last_time     = 0.0     # monotonic time of last screen_process call (cooldown guard)
        self._vision_busy          = False   # True while a vision capture/inject cycle is in flight
        self._interrupted          = False   # True while draining audio after user interrupt
        self.ui.on_text_command   = self._on_text_command
        self.ui.on_remote_clicked = self._make_remote_key
        self.ui.on_interrupt      = self.interrupt
        self._turn_done_event: asyncio.Event | None = None
        self._dashboard     = None
        self._briefing_sent    = False          # morning briefing fires once per process
        self._sys_monitor      = SystemMonitor()  # persistent cooldown state
        self._last_user_speech = time.monotonic()  # updated on every user utterance
        self._memory = MemoryEngine(BASE_DIR / ".ultron" / "memory.sqlite3")
        self._migrate_legacy_memory()
        self._bus = EventBus()
        self._proactive = KernelProactiveEngine(
            self._build_proactive_rules(),
            state_path=BASE_DIR / ".ultron" / "proactive_state.json",
            busy=self._proactive_busy,
        )
        self._proactive.attach(self._bus)
        self._bus.subscribe("proactive.decision", self._on_proactive_decision)
        self._consent_gate = ConsentGate(ui)
        self._tool_runtime = LegacyToolRuntime(
            declarations=TOOL_DECLARATIONS,
            handlers=self._build_legacy_handlers(),
            audit_path=BASE_DIR / ".ultron" / "audit.sqlite3",
        )
        # Phase W2: the kernel tool families go LIVE — the same registry the
        # voice session and orchestrator already execute through gains the
        # built kernel tools (memory_search / computer control / coding
        # workspace / web_read). The agent can finally search its own memory
        # and drive the desktop. No new kernel code: registration is the fix
        # (the audit's "zero production callers" finding, closed).
        register_memory_tools(self._tool_runtime.registry, self._memory)
        self._input_gateway = InputGateway()
        build_computer_tools(
            self._tool_runtime.registry, self._input_gateway,
            spawn_allowed=True,
        )
        build_coding_tools(
            self._tool_runtime.registry,
            BASE_DIR / ".ultron" / "coding_workspace",
        )
        build_research_tools(
            self._tool_runtime.registry,
            consent=lambda: bool(loader.load_config().get("web_research_enabled", False)),
        )
        # Phase W gate: the two live-research pieces the P2-D plan expects —
        # a URL-returning search (the legacy web_search speaks prose; the
        # orchestrator plan needs `first_url`) and the report writer. Both go
        # through the SAME registry/policy choke point.
        self._register_research_gate_tools()

        # Phase O2: agent runner for multi-step complex tasks
        self._agent_runner: AgentRunner | None = None  # built lazily (needs gateway)
        # Phase W1: the orchestrator goes LIVE — durable queue + worker over
        # the SAME policy/registry/audit choke point the live session uses.
        # No new kernel code: JobQueue + Orchestrator exist (P2-C); this is
        # the composition the audit found missing (zero production callers).
        self._job_queue = JobQueue(BASE_DIR / ".ultron" / "jobs.sqlite3")
        self._orchestrator = Orchestrator(
            self._job_queue,
            self._tool_runtime.registry,
            self._tool_runtime.policy,
            bus=self._bus,
            consent=self._consent_gate.request if self._consent_gate else None,
            source="live",
        )
        # Phase I1: autonomous GUI planner (W1: real orchestrator injected)
        self._gui_planner = GUIPlanner(
            orchestrator=self._orchestrator,
            registry=self._tool_runtime.registry,
        )
        # Phase I2: research subagent (W1: real orchestrator injected)
        self._research_runner = ResearchRunner(
            orchestrator=self._orchestrator,
            registry=self._tool_runtime.registry,
        )
        # Phase I3: session summary tracking
        self._session_user_messages: list[str] = []
        self._session_tool_calls: list[str] = []
        self._session_assistant_responses: list[str] = []
        # Phase I4: multi-provider testing
        self._provider_test: ProviderTestRunner | None = None
        # Phase Q3: self-diagnostics
        self._health_monitor = HealthMonitor(base_dir=BASE_DIR / ".ultron")
        # Phase Q4: cost tracking
        self._cost_tracker = CostTracker()

    def _register_research_gate_tools(self) -> None:
        """Phase W: live-research plan support (the P2-D plan's contract).

        `web_search_url` returns structured results with `first_url` (the
        orchestrator plan template `{search.first_url}` needs it; the legacy
        `web_search` speaks prose). `fs_write_report` persists the finished
        report under .ultron/reports/. Both register on the live registry so
        the durable worker executes them behind policy/consent like any tool.
        """
        registry = self._tool_runtime.registry

        @registry.tool(
            name="web_search_url",
            description="Web search returning structured results "
                        "(title/snippet/url) for pipeline use. Read-only.",
            parameters={"type": "object", "properties": {
                "query": {"type": "string",
                          "description": "the search query"},
            }, "required": ["query"]},
            risk=RiskClass.READ,
        )
        def web_search_url(call: ToolCall) -> dict:
            import re as _url_re
            from actions.web_search import _ddg_search
            query = str(call.args.get("query", "")).strip()
            # Tier 1: the DuckDuckGo HTML endpoint — the ddgs API
            # package is flaky post-rename (rate-limits return junk or
            # nothing), this one is stable and on-topic.
            results: list[dict] = []
            first = ""
            if query:
                try:
                    import requests as _req
                    resp = _req.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": query},
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=15,
                    )
                    anchors = _url_re.findall(
                        r'class="result__a"[^>]*href="([^"]+)"', resp.text)
                    urls = [u for u in anchors if u.startswith("http")][:5]
                    results = [{"title": "", "snippet": "", "url": u}
                               for u in urls]
                    first = urls[0] if urls else ""
                except Exception:
                    pass  # fall through to the next tier
            # Tier 2: the ddgs API package (kept as fallback).
            if not first and query:
                try:
                    from actions.web_search import _ddg_search
                    results = _ddg_search(query, max_results=5)
                    first = next((r["url"] for r in results
                                  if r.get("url", "").startswith("http")), "")
                except Exception:
                    pass
            if not first and query:
                # tier 3: the Gemini grounded-search seam — pull real URLs
                # out of the grounded prose (quota-gated, last resort).
                try:
                    from actions._llm import complete_grounded_search
                    text = complete_grounded_search(
                        f"Search the web for: {query}. "
                        "Include the source page URLs.")
                    urls = [m for m in _url_re.findall(
                        r"https?://[^\s)\]>'\"]+", text)
                            if "google." not in m][:5]
                    results = [{"title": "", "snippet": "", "url": u}
                               for u in urls]
                    first = urls[0] if urls else ""
                except Exception:
                    pass  # clean empty result — the plan reports honestly
            return {"results": results[:5], "first_url": first}

        reports_dir = BASE_DIR / ".ultron" / "reports"

        @registry.tool(
            name="fs_write_report",
            description="Save a finished research report (name + text) under "
                        "the assistant's reports folder. Writes files only "
                        "inside that folder.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "file name"},
                "text": {"type": "string", "description": "report body"},
            }, "required": ["name", "text"]},
            risk=RiskClass.WRITE,
        )
        def fs_write_report(call: ToolCall) -> dict:
            import re as _re
            reports_dir.mkdir(parents=True, exist_ok=True)
            safe = _re.sub(r"[^\w.\- ]", "_",
                           str(call.args.get("name", "report.txt"))).strip()
            if not safe:
                safe = "report.txt"
            path = reports_dir / safe
            if path.parent != reports_dir:
                return {"ok": False, "error": "name escapes the reports folder"}
            text = str(call.args.get("text", ""))
            path.write_text(text, encoding="utf-8")
            return {"ok": True, "path": str(path), "bytes": len(text)}

    def _make_remote_key(self):
        """Called from Qt main thread when user presses Remote Control."""
        if self._dashboard is None:
            self.ui.write_log(
                "SYS: Dashboard unavailable. "
                "Run: pip install fastapi \"uvicorn[standard]\" cryptography"
            )
            return None
        try:
            url = self._dashboard.get_url()
        except RuntimeError as exc:
            self.ui.write_log(f"SYS: Remote Control unavailable: {exc}")
            return None
        key = self._dashboard.new_key()
        manual = self._dashboard.get_manual_url()
        return url, key, f"{url}/auto-login?key={key}", manual

    def _on_text_command(self, text: str):
        if not text:
            return
        clean_text = str(text).strip()
        if clean_text in ("/toggle_mic", "toggle_mic", "mute", "unmute"):
            if clean_text == "mute":
                self.ui.muted = True
            elif clean_text == "unmute":
                self.ui.muted = False
            else:
                self.ui.muted = not self.ui.muted
            new_state = "MUTED" if self.ui.muted else "LISTENING"
            self.set_app_state(new_state)
            self.ui.write_log(f"SYS: Microphone {'MUTED (OFF)' if self.ui.muted else 'UNMUTED (ON)'}.")
            return

        # Phase O2: /agent prefix forces multi-step agent loop
        if clean_text.startswith("/agent "):
            task_text = clean_text[7:].strip()
            if task_text and self._loop:
                asyncio.run_coroutine_threadsafe(
                    self._run_agent_task(task_text), self._loop
                )
            return

        # Phase W3: complex typed commands → AgentLoop (shared router)
        if self._maybe_route_agent(clean_text, source="typed"):
            return

        # Phase I1: /gui prefix — autonomous GUI task
        if clean_text.startswith("/gui ") and self._loop:
            task_text = clean_text[5:].strip()
            if task_text:
                asyncio.run_coroutine_threadsafe(
                    self._run_gui_task(task_text), self._loop
                )
            return

        # Phase I2: /research prefix — research subagent
        if clean_text.startswith("/research ") and self._loop:
            topic = clean_text[10:].strip()
            if topic:
                asyncio.run_coroutine_threadsafe(
                    self._run_research(topic), self._loop
                )
            return

        # Phase I4: /providers — test all providers
        if clean_text == "/providers" and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._run_provider_test(), self._loop
            )
            return

        # Phase Q3: /health — system health check
        if clean_text == "/health" and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._run_health_check(), self._loop
            )
            return

        # Phase Q4: /cost — cost report
        if clean_text == "/cost" and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._run_cost_report(), self._loop
            )
            return

        # Default: send to voice session
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": clean_text}]},
                turn_complete=True
            ),
            self._loop
        )

    def set_app_state(self, state: str):
        self.ui.set_state(state)
        if self._dashboard:
            try:
                loop = self._loop or asyncio.get_event_loop()
                if loop and loop.is_running():
                    asyncio.run_coroutine_threadsafe(
                        self._dashboard.broadcast({"type": "state", "state": state}), loop
                    )
            except Exception:
                pass

    def set_speaking(self, value: bool):
        with self._speaking_lock:
            self._is_speaking = value
        if value:
            self.set_app_state("SPEAKING")
        elif not self.ui.muted:
            self.set_app_state("LISTENING")

    def interrupt(self) -> None:
        """Stop ULTRON mid-speech: drain queued audio and open mic immediately."""
        self._interrupted = True
        q = self.audio_in_queue
        if q:
            drained = 0
            while True:
                try:
                    q.get_nowait()
                    drained += 1
                except Exception:
                    break
            if drained:
                print(f"[ULTRON] ✋ Interrupted — {drained} audio chunks discarded")
        self.set_speaking(False)
        if self._turn_done_event:
            self._turn_done_event.clear()
        self.ui.write_log("SYS: Interrupted — listening...")

    def speak(self, text: str):
        if not self._loop or not self.session:
            return
        asyncio.run_coroutine_threadsafe(
            self.session.send_client_content(
                turns={"parts": [{"text": text}]},
                turn_complete=True
            ),
            self._loop
        )

    def speak_error(self, tool_name: str):
        short = "could not be completed"
        self.ui.write_log(f"ERR: {tool_name} — {short}")
        self.speak(f"Sir, {tool_name} encountered an error. {short}")

    # ------------------------------------------------------------------
    # Phase O2 — Agent runner for multi-step complex tasks
    # ------------------------------------------------------------------

    def _get_agent_runner(self) -> AgentRunner:
        """Lazy-build the AgentRunner (needs gateway, which needs API key).
        Phase W5: the runner's gateway is wrapped so EVERY completion's token
        usage lands in the CostTracker — /cost finally shows real numbers."""
        if self._agent_runner is None:
            consent = self._consent_gate.request if self._consent_gate else None
            self._agent_runner = AgentRunner.build(
                tool_runtime=self._tool_runtime,
                bus=self._bus,
                consent=consent,
            )
            self._agent_runner.gateway = _UsageTrackingGateway(
                self._agent_runner.gateway, self._cost_tracker,
            )
        return self._agent_runner

    # Keywords that signal a multi-step task worth routing through the agent loop
    _COMPLEX_TASK_KEYWORDS = frozenset((
        "research", "analyze", "investigate", "report", "write a",
        "create a", "build a", "plan", "summarize", "compare",
        "find and", "search and", "list all", "compile",
    ))

    def _is_complex_task(self, text: str) -> bool:
        """Heuristic: does this text request need multi-step planning?"""
        lower = text.lower()
        return any(kw in lower for kw in self._COMPLEX_TASK_KEYWORDS)

    async def _run_agent_task(self, text: str) -> str:
        """Run a complex task through the AgentLoop and speak the result."""
        runner = self._get_agent_runner()
        self.set_app_state("THINKING")
        self.ui.write_log(f"AGENT: {text[:80]}{'...' if len(text) > 80 else ''}")
        result: TaskResult = await runner.run_task(text)
        if result.text:
            self.speak(result.text)
        else:
            self.speak("Task completed, sir.")
        return result.text

    def _maybe_route_agent(self, text: str, *, source: str) -> bool:
        """Phase W3: the ONE router both input tiers use (two brains, one
        decision). Complex utterances — typed OR spoken — go to the
        AgentLoop; reflexes stay with the LiveSession's native function
        calling. Returns True when routed (caller must not echo the text into
        the session). Consent + abort: the runner carries the consent gate;
        the session's interrupt breaks the turn — the loop observes both."""
        clean = str(text or "").strip()
        if not clean or not self._is_complex_task(clean) or not self._loop:
            return False
        self.ui.write_log(f"SYS: routing {source} task to the agent loop.")
        asyncio.run_coroutine_threadsafe(
            self._run_agent_task(clean), self._loop
        )
        return True

    # ------------------------------------------------------------------
    # Phase W4 — automatic memory formation
    # ------------------------------------------------------------------
    def _persist_session_summary(self) -> None:
        """Session ended: consume the I3 tracking lists into a
        session_summary fact (the audit's 'collected but never consumed').
        Sync + exception-safe — called from the reconnect handler."""
        if not self._session_user_messages:
            return
        try:
            from kernel.memory.session_summary import generate_session_summary
            generate_session_summary(
                user_messages=list(self._session_user_messages),
                tool_calls=list(self._session_tool_calls),
                assistant_responses=list(self._session_assistant_responses),
                memory=self._memory,
            )
            print(f"[Memory] session summary persisted "
                  f"({len(self._session_user_messages)} user turns)")
        except Exception as e:
            print(f"[Memory] WARN session summary skipped: {e}")
        finally:
            self._session_user_messages.clear()
            self._session_tool_calls.clear()
            self._session_assistant_responses.clear()

    async def _run_memory_formation(self) -> None:
        """Phase W4 background task: idle-time consolidation. Every 10 min,
        when the user has been quiet ≥10 min, run the P3-A Consolidator
        (extraction → decay → reflection) through the kernel gateway. No key
        → the task exits quietly (consolidation is a judge-driven job)."""
        while True:
            await asyncio.sleep(600)
            if time.monotonic() - self._last_user_speech < 600:
                continue  # user active — never burn tokens mid-conversation
            try:
                runner = self._get_agent_runner()
                from kernel.memory.consolidation import Consolidator
                consolidator = Consolidator(
                    self._memory, runner.gateway, bus=self._bus,
                )
                report = await consolidator.consolidate()
                self.ui.write_log(
                    f"SYS: Memory consolidation — {report}"
                )
            except ApiKeyMissing:
                return  # no gateway available in this environment
            except Exception as e:
                print(f"[Memory] consolidation cycle skipped: {e}")

    # ------------------------------------------------------------------
    # Phase I1 — GUI autonomous planner (honest-off until Phase W1)
    # ------------------------------------------------------------------
    async def _run_gui_task(self, task: str) -> str:
        """GUI planner — LIVE (Phase W1).

        The orchestrator is now constructed in the composition root, so the
        planner's observe→plan→enqueue path actually executes: the plan's
        tool steps run on the durable worker behind the same policy/consent
        choke point as every other tool call.
        """
        self.set_app_state("THINKING")
        self.ui.write_log(f"GUI: planning '{task[:80]}'")
        try:
            result = await self._gui_planner.plan_and_execute(task)
        except Exception as exc:
            self.set_app_state("LISTENING")
            msg = f"GUI task failed to start: {exc}"
            self.ui.write_log(f"GUI: {msg}")
            self.speak(msg)
            return msg
        self.set_app_state("LISTENING")
        if result.success:
            msg = f"{result.summary} I will report when it completes."
            self.ui.write_log(f"GUI: {msg}")
        else:
            msg = result.error or "The GUI plan could not be built."
            self.ui.write_log(f"GUI: {msg}")
        self.speak(msg)
        return msg

    # ------------------------------------------------------------------
    # Phase I2 — Research subagent (honest-off until Phase W1)
    # ------------------------------------------------------------------
    async def _run_research(self, topic: str) -> str:
        """Research subagent — LIVE (Phase W1).

        research_report_plan → orchestrator.enqueue → durable worker executes
        search → web_read → report steps in the background.
        """
        self.set_app_state("THINKING")
        self.ui.write_log(f"RESEARCH: '{topic[:80]}'")
        try:
            msg = await self._research_runner.run_research(topic)
        except Exception as exc:
            msg = f"Research task failed to start: {exc}"
            self.ui.write_log(f"RESEARCH: {msg}")
        self.set_app_state("LISTENING")
        self.speak(msg)
        return msg

    # ------------------------------------------------------------------
    # Phase I4 — Multi-provider test
    # ------------------------------------------------------------------
    async def _run_provider_test(self) -> str:
        """Test all providers and speak the comparison."""
        self.set_app_state("THINKING")
        self.ui.write_log("PROVIDERS: Testing all configured providers...")
        consent = self._consent_gate.request if self._consent_gate else None
        runner = ProviderTestRunner(
            policy=self._tool_runtime._policy,
            registry=self._tool_runtime._registry,
            consent=consent,
        )
        results = await runner.run_task("Create a note called 'provider-test' with content 'hello'")
        report = runner.compare(results)
        # Speak a summary
        passed = sum(1 for r in results.values() if r.finish == "stop")
        msg = f"Provider test complete. {passed}/{len(results)} providers passed."
        self.speak(msg)
        self.ui.write_log(f"PROVIDERS: {report}")
        return msg

    # ------------------------------------------------------------------
    # Phase Q3 — Health check
    # ------------------------------------------------------------------
    async def _run_health_check(self) -> str:
        """Run a health check and speak the result."""
        self.set_app_state("THINKING")
        self.ui.write_log("HEALTH: Running system health check...")
        report = await self._health_monitor.check_health()
        if report.status == "healthy":
            msg = "All systems healthy, sir."
        else:
            issues = "; ".join(report.issues[:3])
            msg = f"System status: {report.status}. Issues: {issues}"
        self.speak(msg)
        return msg

    # ------------------------------------------------------------------
    # Phase Q4 — Cost report
    # ------------------------------------------------------------------
    async def _run_cost_report(self) -> str:
        """Generate a cost report and speak it."""
        self.set_app_state("THINKING")
        report = self._cost_tracker.get_report()
        ok, budget_msg = self._cost_tracker.check_budget()
        if report.record_count == 0:
            msg = "No token usage recorded yet, sir."
        else:
            msg = (
                f"Cost report: ${report.total_cost_usd:.4f} total. "
                f"{report.total_input_tokens:,} input tokens, "
                f"{report.total_output_tokens:,} output tokens. "
                f"{budget_msg}."
            )
        self.speak(msg)
        return msg

    def _build_config(self):
        """Build Live session config via the gateway wrapper.

        Phase O3: uses PromptAssembler for auto-RAG memory context.
        """
        # Load customization from config
        _cfg = loader.load_config()
        self._asst_name = (_cfg.get("assistant_name") or "ULTRON").strip()
        _user_name = (_cfg.get("user_name") or "").strip()

        sys_prompt = _load_system_prompt()

        # Phase O3: PromptAssembler handles voice directive + time + identity
        # + auto-RAG memory retrieval + base prompt in one call. Phase W4:
        # the RAG query is now the recent conversation topics instead of the
        # audit-flagged static "user preferences and history" string.
        recent_topics = " ".join(self._session_user_messages[-5:])
        assembler = PromptAssembler(
            memory=self._memory,
            base_prompt=sys_prompt,
            asst_name=self._asst_name,
            user_name=_user_name,
        )
        assembled = assembler.assemble(conversation_summary=recent_topics)

        return build_live_config(
            system_prompt=assembled.system_instruction,
            # Phase W2: the LIVE session now declares the full registry —
            # legacy actions AND the kernel tool families (memory_search,
            # computer control, coding, web_read). Declarations come from
            # the registry (single source), not the static legacy list.
            tool_declarations=self._tool_runtime.registry.declarations(),
            voice_name="Charon",
        )
    def _build_proactive_rules(self) -> list[TriggerRule]:
        """App-layer rules for the kernel proactive engine (P4-D): bus event
        patterns → spoken emissions. The check-in rule is a marker: its
        emission triggers a Gemini-authored prompt with live memory context."""
        return [
            TriggerRule(
                "reminder-due", "reminder.due",
                "Sir, you asked me to remind you: {message}",
                consent=ProactiveConsentClass.ALWAYS, cooldown_s=0.0,
            ),
            TriggerRule(
                "home-motion", "home.detection",
                "Sir, I detected motion in the {zone}.",
                consent=ProactiveConsentClass.NEVER_WHEN_BUSY,
            ),
            TriggerRule(
                "job-done", "job.completed",
                "Sir, your background task '{title}' finished.",
                consent=ProactiveConsentClass.ALWAYS, cooldown_s=60.0,
            ),
            TriggerRule(
                "check-in", "system.tick",
                "check-in",
                consent=ProactiveConsentClass.NEVER_WHEN_BUSY,
                cooldown_s=1800, max_per_hour=2,
            ),
        ]

    def _proactive_busy(self) -> bool:
        """NEVER_WHEN_BUSY gate: ULTRON is speaking, or the user muted us
        (muted means no interruptions)."""
        with self._speaking_lock:
            return self._is_speaking or self.ui.muted

    async def _on_proactive_decision(self, event: Event) -> None:
        payload = dict(event.payload or {})
        if payload.get("outcome") != "fire" or not self.session:
            return
        rule = payload.get("rule", "")
        try:
            if rule == "check-in":
                await self._send_proactive_checkin()
            else:
                message = str(payload.get("message", "")).strip()
                if message:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": message}]},
                        turn_complete=True,
                    )
                    self.ui.write_log(f"SYS: Proactive — {message[:120]}")
        except Exception as e:
            print(f"[Proactive] WARN speak failed: {e}")

    def _build_legacy_handlers(self):
        """Expose the pre-kernel handlers only through the migration seam.
        TOOL_REGISTRY maps name→method-name (Phase W0), so resolve via
        getattr here — the mixin may be anywhere in the MRO."""
        def wrap(method_name):
            handler = getattr(self, method_name)
            async def invoke(args):
                return await handler(args, asyncio.get_running_loop())
            return invoke

        handlers = {name: wrap(method) for name, method in self.TOOL_REGISTRY.items()}
        handlers["save_memory"] = wrap("_handle_save_memory")
        return handlers

    # Phase W0: handlers live on the LegacyHandlersMixin; the registry maps
    # tool name -> handler METHOD NAME (bound at call time by
    # _build_legacy_handlers via getattr), so importing main never needs the
    # method objects at class-body time.
    TOOL_REGISTRY = {
        "open_app": "_handle_open_app",
        "weather_report": "_handle_weather_report",
        "browser_control": "_handle_browser_control",
        "file_controller": "_handle_file_controller",
        "reminder": "_handle_reminder",
        "youtube_video": "_handle_youtube_video",
        "screen_process": "_handle_screen_process",
        "close_camera": "_handle_close_camera",
        "computer_settings": "_handle_computer_settings",
        "desktop_control": "_handle_desktop_control",
        "code_helper": "_handle_code_helper",
        "dev_agent": "_handle_dev_agent",
        "web_search": "_handle_web_search",
        "file_processor": "_handle_file_processor",
        "computer_control": "_handle_computer_control",
        "game_updater": "_handle_game_updater",
        "flight_finder": "_handle_flight_finder",
        "system_status": "_handle_system_status",
        "shutdown_ultron": "_handle_shutdown",
    }

    async def _execute_tool(self, fc) -> FunctionResponse:
        name = fc.name
        args = dict(fc.args or {})

        print(f"[ULTRON] 🔧 {name}  {args}")
        self.set_app_state("THINKING")

        call = ToolCall(
            id=str(getattr(fc, "id", "") or f"live-{time.monotonic_ns()}"),
            name=name,
            args=args,
            source="gemini-live",
        )
        consent = self._consent_gate.request if self._consent_gate else None
        structured = await self._tool_runtime.execute(call, consent=consent)
        result = structured.data if structured.ok else structured.error
        if not structured.ok:
            self.ui.write_log(
                f"TOOL: {name} not completed ({structured.risk.value}: {structured.error})"
            )
            self._health_monitor.record_error("tool", name)  # Phase Q3: track errors
            # Phase W5: errors reach the dashboard via the bus bridge.
            try:
                await self._bus.publish(Event(
                    type="health.error",
                    payload={"component": "tool", "name": name,
                             "error": str(structured.error)[:200]},
                    source="live",
                ))
            except Exception:
                pass  # observability must never break the tool path

        if not self.ui.muted:
            self.set_app_state("LISTENING")

        print(f"[ULTRON] tool {name}: {'ok' if structured.ok else 'blocked/failed'}")
        return FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer
            self._dashboard = DashboardServer()
            self._dashboard.set_connect_callback(self._on_phone_connected)
            asyncio.create_task(self._dashboard.serve())
            asyncio.create_task(self._process_dashboard_commands())
            # Phase O4: bridge kernel EventBus → dashboard WebSocket
            self._bus_bridge = BusDashboardBridge(self._bus, self._dashboard)
            self._bus_bridge.attach()
            # webbrowser.open(f"http://127.0.0.1:{PORT}")
        except Exception as e:
            print(f"[Dashboard] Disabled: {e}")
            self._dashboard = None

        while True:
            try:
                print("[ULTRON] Connecting...")
                self.set_app_state("THINKING")
                config = self._build_config()

                # Gateway-managed Live session (Phase O): model string and
                # SDK import centralised in kernel.gateway.live. The settings
                # flow through GatewaySettings.from_config so provider config
                # stays the single source of truth (Phase R4).
                live = LiveSession(
                    settings=GatewaySettings.from_config(loader.load_config()),
                    api_key=_get_api_key(),
                )
                await live.connect(config=config)

                async with asyncio.TaskGroup() as tg:
                    self.session          = live.session
                    self.audio_in_queue   = asyncio.Queue()
                    self.out_queue        = asyncio.Queue(maxsize=200)
                    self._turn_done_event = asyncio.Event()

                    # Reset transient state that must not carry over from a previous session
                    self._pending_vision       = None
                    self._vision_cam_active    = False
                    self._vision_close_pending = False
                    self._vision_busy          = False
                    self._vision_last_time     = 0.0
                    self._interrupted          = False

                    print("[ULTRON] Connected.")
                    self.set_app_state("LISTENING")
                    self.ui.write_log("SYS: ULTRON online.")

                    if self._dashboard:
                        await self._dashboard.broadcast({"type": "status", "state": "active"})

                    tg.create_task(self._send_realtime())
                    tg.create_task(self._listen_audio())
                    tg.create_task(self._receive_audio())
                    tg.create_task(self._play_audio())
                    tg.create_task(self._run_system_monitor())
                    tg.create_task(self._run_proactive_mode())
                    # Phase W1: the durable orchestrator worker goes live —
                    # background jobs (research/GUI plans) now actually run.
                    tg.create_task(self._orchestrator.run_worker(max_jobs=None))
                    # Phase W4: idle-time memory formation (consolidation).
                    tg.create_task(self._run_memory_formation())
                    if self._dashboard:
                        tg.create_task(self._relay_phone_audio())

                    # Wake Word or Morning briefing — fires once per process launch
                    if not self._briefing_sent:
                        self._briefing_sent = True
                        if "--wake-word" in sys.argv:
                            tg.create_task(self.session.send_client_content(
                                turns={"parts": [{"text": "wake up ultron"}]},
                                turn_complete=True
                            ))
                        elif loader.load_config().get("morning_brief_enabled", True):
                            tg.create_task(self._send_startup_briefing())

            except KeyboardInterrupt:
                raise
            except SystemExit:
                raise
            except BaseException as e:
                # Catches both Exception and BaseExceptionGroup (Python 3.11+
                # TaskGroup raises BaseExceptionGroup when tasks are cancelled
                # externally, which `except Exception` would miss, letting the
                # exception escape the while-loop and causing asyncio.run() to
                # start shutdown — resulting in "executor after shutdown" errors).
                err_str = str(e)
                print(f"[ULTRON] Error ({type(e).__name__}): {e}")
                traceback.print_exc()

                # Phase W4: the session ended — persist what happened before
                # the reconnect. generate_session_summary consumes the I3
                # tracking lists and writes a session_summary fact.
                self._persist_session_summary()

                # Invalid / missing / broken API key — stop hammering the API, prompt re-configuration
                if (
                    isinstance(e, ApiKeyMissing)
                    or "API key not valid" in err_str
                    or "1007" in err_str
                ):
                    self.ui.write_log("ERR: API key missing or invalid — please re-enter your key.")
                    self.ui.set_state("SLEEPING")
                    self.ui.prompt_reconfig()
                    while not self.ui._win._ready:
                        await asyncio.sleep(1)
                    print("[ULTRON] New API key saved — reconnecting...")
                    _conn_backoff = 3
                    continue

                # Network / timeout errors — log clearly and back off
                is_net_err = any(k in err_str for k in (
                    "TimeoutError", "timed out", "getaddrinfo", "CancelledError",
                    "ConnectionRefusedError", "OSError", "Cannot connect",
                ))
                if is_net_err:
                    _conn_backoff = min(getattr(self, "_conn_backoff", 3) * 2, 60)
                    self._conn_backoff = _conn_backoff
                    self.ui.write_log(
                        f"NET: Bağlantı kurulamadı — {_conn_backoff}s sonra tekrar deneniyor. "
                        "(VPN gerekiyor olabilir)"
                    )
                else:
                    self._conn_backoff = 3
            finally:
                self.session = None
                if 'live' in dir():
                    await live.close()

            self.set_speaking(False)
            self.set_app_state("SLEEPING")

            if self._dashboard:
                await self._dashboard.broadcast({"type": "status", "state": "sleeping"})

            delay = getattr(self, "_conn_backoff", 3)
            print(f"[ULTRON] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)


import socket

_single_instance_sock = None

def _ensure_single_instance():
    global _single_instance_sock
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 39152))
        _single_instance_sock = sock
    except OSError:
        print("[ULTRON] ⚠️ ULTRON is already running in another process! Exiting duplicate instance to prevent window flickering.", file=sys.stderr)
        sys.exit(0)

def main():
    _ensure_single_instance()
    ui = UltronUI("face.png")

    def runner():
        ui.wait_for_api_key()
        ultron = UltronLive(ui)
        try:
            asyncio.run(ultron.run())
        except KeyboardInterrupt:
            print("\n🔴 Shutting down...")

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()

if __name__ == "__main__":
    main()
