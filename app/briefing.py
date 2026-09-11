"""app/briefing.py — startup greeting + proactive check-in prompts (Phase W0).

Extracted verbatim from main.py (`_send_startup_briefing`,
`_send_proactive_checkin`). Mixin for UltronLive.
"""

from __future__ import annotations

import asyncio
from datetime import datetime


class BriefingMixin:
    """Expects the host to provide: _memory, session, ui, _turn_done_event,
    _last_user_speech, _memory_prompt()."""

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

        # ── Instant greeting & status via kernel briefing engine (REV-06) ───
        from kernel.briefing.briefing import build_briefing, render
        highlights = [f"{h.topic}: {h.content}" for h in self._memory.page(limit=5)]
        briefing_obj = build_briefing(memory_highlights=highlights)
        briefing_text = render(briefing_obj)

        lang_clause = f" Respond in {lang}." if lang else ""
        name_clause = f" Address the user as {name}." if name else ""
        p1 = (
            f"Greet the user, mention it is {time_str}, state that systems and HUD ULTRON are fully operational. "
            f"System briefing:\n{briefing_text}\n"
            "Ask how you can assist today. One or two short sentences only. Do not call any tools."
            f"{lang_clause}{name_clause}"
        )

        # Clear the turn-done event
        if self._turn_done_event:
            self._turn_done_event.clear()

        await self.session.send_client_content(
            turns={"parts": [{"text": p1}]},
            turn_complete=True,
        )
        self.ui.write_log("SYS: Startup briefing greeting sent.")

    async def _send_proactive_checkin(self) -> None:
        """Gemini-authored check-in (the old silence-timer's spirit, now gated
        by the kernel engine's cooldown/hour-cap machinery — Phase R5)."""
        now = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        mem_str = self._memory_prompt() or "(no user data stored yet)"
        import time as _time
        silence_min = int((_time.monotonic() - self._last_user_speech) // 60)
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
