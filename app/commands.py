"""app/commands.py — the command dispatch + agent tier (Phase P3).

Moved verbatim from main.py: the typed/spoken command router, the agent
runner lifecycle (with the W5 usage-tracking gateway), and the /gui
/research /providers /health /cost commands. One router, both input
tiers (W3).
"""

from __future__ import annotations

import asyncio

from app.observability import _UsageTrackingGateway
from kernel.loop.provider_test import ProviderTestRunner
from kernel.loop.runner import AgentRunner, TaskResult


class CommandMixin:
    """Expects the host to provide ui, _loop, _tool_runtime, _consent_gate,
    _gui_planner, _research_runner, _health_monitor, _cost_tracker,
    _agent_runner, session, and the set_app_state/speak/speak_error UI
    helpers that stay on the composition root."""

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

        # Phase P2: /user — list / switch / create profiles (multi-user live)
        if clean_text.startswith("/user"):
            asyncio.run_coroutine_threadsafe(
                self._handle_user_command(clean_text), self._loop
            ) if self._loop else None
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

    async def _handle_user_command(self, text: str) -> str:
        """Phase P2: /user [list | switch <id> | create <id> <name>]."""
        parts = text.split(maxsplit=2)
        sub = parts[1] if len(parts) > 1 else "list"
        um = self._users
        if sub == "list":
            users = um.list_users()
            current = um.get_current_user()
            lines = [f"* {u.user_id}: {u.display_name}"
                     + (" (active)" if current and u.user_id == current.user_id else "")
                     for u in users]
            msg = "Users: " + "; ".join(lines)
        elif sub == "switch" and len(parts) > 2:
            ok = um.set_current_user(parts[2].strip())
            msg = (f"Switched to {parts[2].strip()}, sir. I will address you "
                   f"as {um.get_current_user().display_name}."
                   if ok else f"No user named {parts[2].strip()}, sir. Say: /user list")
        elif sub == "create" and len(parts) > 2:
            rest = parts[2].split(maxsplit=1)
            uid = rest[0].strip()
            name = rest[1].strip() if len(rest) > 1 else uid
            um.create_user(uid, name)
            um.set_current_user(uid)
            msg = f"Profile {uid} created for {name}, sir — you are now {name}."
        else:
            msg = "Usage: /user list | /user switch <id> | /user create <id> <name>"
        self.ui.write_log(f"USER: {msg}")
        self.speak(msg)
        return msg

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

