"""app/memory_formation.py — idle-time memory formation (Phase P3).

Moved verbatim from main.py (W4): session-summary persistence on reconnect
+ the Consolidator idle task.
"""

from __future__ import annotations

import asyncio
import time

from app.observability import ApiKeyMissing


class MemoryFormationMixin:
    """Expects the host to provide _memory, _session_* lists, _bus,
    _last_user_speech, ui, and the CommandMixin's _get_agent_runner()."""

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
            # Periodic persistence during idle periods (REV-04): persist session
            # summary and clear lists to prevent RAM leak in healthy sessions
            if getattr(self, "_session_user_messages", None) and (
                time.monotonic() - self._last_user_speech >= 600 or len(self._session_user_messages) >= 50
            ):
                self._persist_session_summary()

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
