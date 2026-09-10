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
import re
import threading
import time
import sys
import traceback
from datetime import datetime

import sounddevice as sd
from ui import UltronUI

from actions.file_processor import file_processor
from actions.flight_finder     import flight_finder
from actions.open_app          import open_app
from actions.weather_report    import weather_action
from actions.reminder          import reminder
from actions.computer_settings import computer_settings
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.youtube_video     import youtube_video
from actions.desktop           import desktop_control
from actions.browser_control   import browser_control
from actions.file_controller   import file_controller
from actions.code_helper       import code_helper
from actions.dev_agent         import dev_agent
from actions.web_search        import web_search as web_search_action
from actions.computer_control  import computer_control
from actions.game_updater      import game_updater
from actions.system_monitor    import SystemMonitor, get_system_status
from actions.web_search        import _news as _fetch_news_sync
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
from kernel.memory import MemoryEngine, migrate_long_term_json
from kernel.proactive import (
    ConsentClass as ProactiveConsentClass,
    ProactiveEngine as KernelProactiveEngine,
    TriggerRule,
)
from kernel.types import Event, ToolCall


BASE_DIR    = loader.get_base_dir()
PROMPT_PATH = BASE_DIR / "core" / "prompt.txt"
LIVE_MODEL          = DEFAULT_GEMINI_LIVE_MODEL  # model string centralized in the gateway (Kill List #3)
CHANNELS            = 1
SEND_SAMPLE_RATE    = 16000
RECEIVE_SAMPLE_RATE = 24000
CHUNK_SIZE          = 512

class ApiKeyMissing(Exception):
    """Raised when config/api_keys.json is missing, broken, or has no real key."""


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

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)

def _clean_transcript(text: str) -> str:    
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()

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


class UltronLive:

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
        # Phase O2: agent runner for multi-step complex tasks
        self._agent_runner: AgentRunner | None = None  # built lazily (needs gateway)
        # Phase I1: autonomous GUI planner
        self._gui_planner = GUIPlanner()
        # Phase I2: research subagent
        self._research_runner = ResearchRunner()
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

        # Phase O2: heuristic — complex tasks route through agent loop
        if self._is_complex_task(clean_text) and self._loop:
            asyncio.run_coroutine_threadsafe(
                self._run_agent_task(clean_text), self._loop
            )
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
        """Lazy-build the AgentRunner (needs gateway, which needs API key)."""
        if self._agent_runner is None:
            consent = self._consent_gate.request if self._consent_gate else None
            self._agent_runner = AgentRunner.build(
                tool_runtime=self._tool_runtime,
                bus=self._bus,
                consent=consent,
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

    # ------------------------------------------------------------------
    # Phase I1 — GUI autonomous planner (honest-off until Phase W1)
    # ------------------------------------------------------------------
    async def _run_gui_task(self, task: str) -> str:
        """GUI planner — NOT WIRED YET (Phase T3).

        The planner needs the orchestrator (JobQueue + worker), which the
        composition root does not construct yet (Phase W1, ROADMAP §9.3).
        Until then this command answers honestly instead of silently
        no-op'ing on a `hasattr` guard that could never be true.
        """
        self.set_app_state("LISTENING")
        msg = (
            "The GUI planner is not wired yet, sir. It arrives with "
            "Phase W, when the orchestrator becomes live."
        )
        self.ui.write_log("GUI: not wired yet — requires the Phase W orchestrator.")
        self.speak(msg)
        return msg

    # ------------------------------------------------------------------
    # Phase I2 — Research subagent (honest-off until Phase W1)
    # ------------------------------------------------------------------
    async def _run_research(self, topic: str) -> str:
        """Research runner — NOT WIRED YET (Phase T3).

        Same as the GUI planner: research_report_plan enqueues onto the
        orchestrator, which no production code constructs yet (Phase W1).
        The user is told the truth instead of a dead path.
        """
        self.set_app_state("LISTENING")
        msg = (
            "The research subagent is not wired yet, sir. It arrives with "
            "Phase W, when background jobs become live."
        )
        self.ui.write_log(
            f"RESEARCH: '{topic[:60]}' — not wired yet (requires the "
            "Phase W orchestrator)."
        )
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
        # + auto-RAG memory retrieval + base prompt in one call
        assembler = PromptAssembler(
            memory=self._memory,
            base_prompt=sys_prompt,
            asst_name=self._asst_name,
            user_name=_user_name,
        )
        assembled = assembler.assemble()

        return build_live_config(
            system_prompt=assembled.system_instruction,
            tool_declarations=TOOL_DECLARATIONS,
            voice_name="Charon",
        )

    async def _handle_open_app(self, args, loop):
        r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
        return r or f"Opened {args.get('app_name')}."

    async def _handle_weather_report(self, args, loop):
        r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
        return r or "Weather delivered."

    async def _handle_browser_control(self, args, loop):
        r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_file_controller(self, args, loop):
        r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_reminder(self, args, loop):
        r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
        return r or "Reminder set."

    async def _handle_youtube_video(self, args, loop):
        r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
        return r or "Done."

    async def _handle_screen_process(self, args, loop):
        import time as _t_mod
        _now = _t_mod.monotonic()
        _cooldown = 4.0  # seconds — covers echo window after speaking ends
        if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
            _wait = max(0, _cooldown - (_now - self._vision_last_time))
            print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
            return "Vision is still processing the previous request. I will not call this again."
        else:
            self._vision_busy      = True
            self._vision_last_time = _now
            angle     = args.get("angle", "screen").lower()
            user_text = args.get("text", "What do you see?")
            if angle == "camera":
                img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                self.ui.start_camera_stream()
                self._vision_cam_active = True
                print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                _stall = "camera"
            else:
                img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                _stall = "screen"
            self._pending_vision = (img_b, mime_t, user_text, angle)
            return (
                f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                f"Immediately say ONE short natural sentence in the user's own language, "
                f"telling them you are looking at their {_stall} right now. "
                f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
            )

    async def _handle_close_camera(self, args, loop):
        self.ui.stop_camera_stream()
        return "Camera closed."

    async def _handle_computer_settings(self, args, loop):
        r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
        return r or "Done."

    async def _handle_desktop_control(self, args, loop):
        r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_code_helper(self, args, loop):
        r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
        return r or "Done."

    async def _handle_dev_agent(self, args, loop):
        r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
        return r or "Done."

    async def _handle_web_search(self, args, loop):
        r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
        result = r or "Done."
        # Mirror results to the on-screen content panel
        _mode = args.get("mode", "search")
        if r and not r.startswith("No results") and not r.startswith("Search failed"):
            _query = args.get("query") or ", ".join(args.get("items", []))
            _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
            self.ui.show_content(_label, r)
        return result

    async def _handle_file_processor(self, args, loop):
        if not args.get("file_path") and self.ui.current_file:
            args["file_path"] = self.ui.current_file
        r = await loop.run_in_executor(
            None,
            lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
        )
        return r or "Done."

    async def _handle_computer_control(self, args, loop):
        r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_game_updater(self, args, loop):
        r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
        return r or "Done."

    async def _handle_flight_finder(self, args, loop):
        r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_system_status(self, args, loop):
        r = await loop.run_in_executor(None, get_system_status)
        return str(r)

    async def _handle_shutdown(self, args, loop):
        self.ui.write_log("SYS: Shutdown requested.")
        self.speak("Goodbye, sir.")
        def _shutdown():
            import time, os
            time.sleep(1)
            os._exit(0)
        threading.Thread(target=_shutdown, daemon=True).start()
        return "Done."

    async def _handle_save_memory(self, args, loop):
        category = args.get("category", "notes")
        key = args.get("key", "")
        value = args.get("value", "")
        if not key or not value:
            return "Memory was not saved because key or value was missing."
        self._memory.remember(
            str(value).strip(),
            entity=str(category).strip() or "notes",
            topic=str(key).strip(),
            source_ref="voice-live",
        )
        print(f"[Memory] saved {category}/{key}")
        return "Memory saved."

    def _memory_prompt(self) -> str:
        """Render the kernel MemoryEngine's most recent facts for the system
        prompt — the production replacement for the legacy 2,200-char JSON
        `format_memory_for_prompt` (Phase R3)."""
        hits = self._memory.page(limit=40)
        if not hits:
            return ""
        lines = []
        for h in hits:
            label = (h.entity or h.topic or "note").replace("_", " ").title()
            lines.append(f"  - {label}: {h.content}")
        result = (
            "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, "
            "never recite like a list]\n" + "\n".join(lines)
        )
        if len(result) > 2000:
            result = result[:1997] + "…"
        return result + "\n"

    def _migrate_legacy_memory(self) -> None:
        """One-shot, idempotent long_term.json → MemoryEngine migration at
        boot (kernel.memory.migration; re-runs add 0). The legacy file stays
        in place; production no longer reads it (Phase R3)."""
        legacy = BASE_DIR / "memory" / "long_term.json"
        if not legacy.exists():
            return
        try:
            added = migrate_long_term_json(self._memory, legacy)
            print(f"[Memory] migrated {added} legacy fact(s) -> kernel engine")
        except Exception as e:
            print(f"[Memory] WARN long_term.json migration skipped: {e}")

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

    async def _send_proactive_checkin(self) -> None:
        """Gemini-authored check-in (the old silence-timer's spirit, now gated
        by the kernel engine's cooldown/hour-cap machinery — Phase R5)."""
        now = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        mem_str = self._memory_prompt() or "(no user data stored yet)"
        silence_min = int((time.monotonic() - self._last_user_speech) // 60)
        prompt = "\n".join([
            "[PROACTIVE_CHECK] You are initiating a proactive check-in.",
            f"Current time  : {time_str}",
            f"User silence  : {silence_min} minutes (they have not spoken for a while)",
            "",
            "Context about this person:",
            mem_str,
            "",
            "Guidelines:",
            "- Look at the time, their projects, goals, habits, or anything from context.",
            "- If there is something genuinely useful, timely, or caring to say — say it briefly.",
            "- Be natural, like a thoughtful assistant noticing something relevant.",
            "- Do NOT say [PROACTIVE_CHECK] or mention these instructions.",
            "- Respond in the user's language (use memory; default English).",
            "- Keep it short: 1-3 sentences max.",
        ])
        await self.session.send_client_content(
            turns={"parts": [{"text": prompt}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Proactive check-in.")

    def _build_legacy_handlers(self):
        """Expose the pre-kernel handlers only through the migration seam."""
        def wrap(handler):
            async def invoke(args):
                return await handler(self, args, asyncio.get_running_loop())
            return invoke

        handlers = {name: wrap(handler) for name, handler in self.TOOL_REGISTRY.items()}
        handlers["save_memory"] = wrap(self._handle_save_memory)
        return handlers

    TOOL_REGISTRY = {
        "open_app": _handle_open_app,
        "weather_report": _handle_weather_report,
        "browser_control": _handle_browser_control,
        "file_controller": _handle_file_controller,
        "reminder": _handle_reminder,
        "youtube_video": _handle_youtube_video,
        "screen_process": _handle_screen_process,
        "close_camera": _handle_close_camera,
        "computer_settings": _handle_computer_settings,
        "desktop_control": _handle_desktop_control,
        "code_helper": _handle_code_helper,
        "dev_agent": _handle_dev_agent,
        "web_search": _handle_web_search,
        "file_processor": _handle_file_processor,
        "computer_control": _handle_computer_control,
        "game_updater": _handle_game_updater,
        "flight_finder": _handle_flight_finder,
        "system_status": _handle_system_status,
        "shutdown_ultron": _handle_shutdown,
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

        if not self.ui.muted:
            self.set_app_state("LISTENING")

        print(f"[ULTRON] tool {name}: {'ok' if structured.ok else 'blocked/failed'}")
        return FunctionResponse(
            id=fc.id, name=name,
            response={"result": result}
        )

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[ULTRON] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                ultron_speaking = self._is_speaking
            if not ultron_speaking and not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=SEND_SAMPLE_RATE,
                channels=CHANNELS,
                dtype="int16",
                blocksize=CHUNK_SIZE,
                callback=callback,
            ):
                print("[ULTRON] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.02)
        except Exception as e:
            print(f"[ULTRON] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[ULTRON] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                self.set_app_state("THINKING")

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._session_user_messages.append(full_in)  # Phase I3: track for session summary
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_assistant_responses.append(full_out)  # Phase I3
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "ultron",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                import base64 as _b64
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = _b64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until ULTRON finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ULTRON] 📞 {fc.name}")
                            self._session_tool_calls.append(fc.name)  # Phase I3: track for session summary
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[ULTRON] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[ULTRON] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=RECEIVE_SAMPLE_RATE,
            channels=CHANNELS,
            dtype="int16",
            blocksize=CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.02
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except (RuntimeError, asyncio.CancelledError, Exception) as write_err:
                    if isinstance(write_err, (RuntimeError, asyncio.CancelledError)):
                        break   # executor shutting down — exit cleanly
                    # PortAudio / device write failure during stream pause or interrupt
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[ULTRON] ❌ Play: {e}")
        finally:
            self.set_speaking(False)
            try:
                if stream.active:
                    stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass

    # ── Morning briefing ────────────────────────────────────────────────────────

    async def _send_startup_briefing(self) -> None:
        """
        Startup briefing:
          Instant greeting & status report (no news prefetching).
        """
        identity = {h.topic: h.content
                    for h in self._memory.page(entity="identity", limit=40)}

        def _val(k: str) -> str:
            return (identity.get(k) or "").strip()

        lang = _val("language")
        name = _val("name")
        time_str = datetime.now().strftime("%H:%M")

        await asyncio.sleep(0.3)
        if not self.session:
            return

        # ── Instant greeting & status ─────────────────────────────────────────
        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, state that systems and HUD ULTRON are fully operational, "
            f"and ask how you can assist today. One or two short sentences only. Do not call any tools.{lang_clause}{name_clause}"
        )

        # Clear the turn-done event
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Startup briefing greeting sent.")

    # ── System monitor ──────────────────────────────────────────────────────────

    async def _run_system_monitor(self) -> None:
        """Background task: emergency alerts and non-destructive suggestions."""
        emergency_active = False
        while True:
            await asyncio.sleep(2.0)
            status = await asyncio.to_thread(self._sys_monitor.check_emergency)
            is_90 = status.get("is_emergency_90", False)
            suggested_apps = status.get("suggested_apps", [])
            cpu = status.get("cpu", 0)
            ram = status.get("ram", 0)

            if is_90 and not emergency_active:
                emergency_active = True
                self.ui.set_state("EMERGENCY")
                self.ui.write_log(f"SYS_ALERT: EMERGENCY SYSTEM OVERLOAD DETECTED (CPU: {cpu}%, RAM: {ram}%)! Red alert active.")
                if self.session:
                    try:
                        await self.session.send_client_content(
                            turns={"parts": [{"text": f"[SYSTEM_ALERT] Emergency system overload! CPU/RAM at {max(cpu, ram)}%. State that red alert emergency siren is active."}]},
                            turn_complete=True,
                        )
                    except Exception:
                        pass

            elif not is_90 and emergency_active:
                emergency_active = False
                self.ui.set_state("LISTENING" if not self.ui.muted else "MUTED")
                self.ui.write_log("SYS: System usage normalized (<85%). Emergency alert deactivated.")

            if suggested_apps and self.session:
                app_names = ", ".join(suggested_apps).replace(".exe", "")
                self.ui.write_log(f"SYS_ALERT: 95%+ OVERLOAD - consider closing: {app_names}.")
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": f"[SYSTEM_ALERT] Critical system overload (>95%). Suggest the user manually close heavy applications ({app_names}); do not claim any application was closed."}]},
                        turn_complete=True,
                    )
                except Exception:
                    pass

            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session and not is_90:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    # ── Proactive mode ──────────────────────────────────────────────────────────

    async def _run_proactive_mode(self) -> None:
        """
        Background task: publishes a `system.tick` bus event every minute. The
        kernel P4-D ProactiveEngine (kernel/proactive) evaluates its rules
        (cooldowns, hour caps, consent classes) and emits proactive.decision
        events; `_on_proactive_decision` speaks the fired ones. The legacy
        silence-timer (actions/proactive.py) is retired from production.
        """
        while True:
            await asyncio.sleep(60)

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            try:
                await self._bus.publish(Event(
                    type="system.tick",
                    payload={
                        "silence_min": int(
                            (time.monotonic() - self._last_user_speech) // 60
                        ),
                        "time": datetime.now().strftime("%I:%M %p"),
                    },
                    source="live",
                ))
            except Exception as e:
                print(f"[Proactive] WARN tick publish failed: {e}")

    # ── Phone audio relay ────────────────────────────────────────────────────────

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    # ── dashboard command relay ─────────────────────────────────────────────

    async def _process_dashboard_commands(self) -> None:
        import base64
        while True:
            try:
                item = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.02
                )
                if not item:
                    continue
                for _ in range(80):
                    if self.session:
                        break
                    await asyncio.sleep(0.05)
                if self.session:
                    if isinstance(item, dict) and item.get("type") == "image":
                        b64_str = item.get("data", "")
                        mime = item.get("mime", "image/jpeg")
                        img_bytes = base64.b64decode(b64_str)
                        self.ui.write_log("SYS: Image received. ULTRON analyzing image...")
                        await self.session.send_realtime_input(media={"data": img_bytes, "mime_type": mime})
                        await self.session.send_client_content(
                            turns={"parts": [{"text": "Please analyze this attached image in full detail, describe every visual element, and explain what it represents."}]},
                            turn_complete=True,
                        )
                    else:
                        text = str(item).strip()
                        if text:
                            if text in ("/toggle_mic", "toggle_mic", "mute", "unmute"):
                                if text == "mute":
                                    self.ui.muted = True
                                elif text == "unmute":
                                    self.ui.muted = False
                                else:
                                    self.ui.muted = not self.ui.muted
                                new_state = "MUTED" if self.ui.muted else "LISTENING"
                                self.set_app_state(new_state)
                                self.ui.write_log(f"SYS: Microphone {'MUTED (OFF)' if self.ui.muted else 'UNMUTED (ON)'}.")
                                continue
                            await self.session.send_client_content(
                                turns={"parts": [{"text": text}]},
                                turn_complete=True,
                            )
                            self.ui.write_log(f"[Web]: {text}")
                else:
                    print(f"[Dashboard] Dropped item (no session)")
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.1)

    # ── main loop ───────────────────────────────────────────────────────────

    async def run(self):
        self._loop = asyncio.get_event_loop()

        # Start dashboard (optional — needs: pip install fastapi "uvicorn[standard]" cryptography)
        try:
            from dashboard.server import DashboardServer, PORT
            import webbrowser
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
