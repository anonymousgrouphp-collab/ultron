"""app/consent.py — the UI consent bridge (Phase P3).

Moved verbatim from main.py: kernel ASK decisions → Qt yes/no dialog,
fail-closed in every direction (timeout / missing UI / broken dialog).
"""

from __future__ import annotations

import asyncio


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


