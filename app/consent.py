"""app/consent.py — the UI consent bridge (Phase P3, extended by research/12 D2).

Kernel ASK decisions → a confirmation flow that can be answered from MORE
than one place: the local yes/no dialog (original path) or any transport
that calls `resolve(request_id, allowed)` — the dashboard popup is the
planned second transport (the request id travels on the bus event).

Fail-closed in every direction (timeout / missing UI / broken dialog /
resolver error): no answer means deny, never execute.
"""

from __future__ import annotations

import asyncio
import uuid


class ConsentGate:
    """Bridges kernel ASK decisions to a human answer (Phase R1 + D2).

    Flow per request:
    1. A uuid request id is minted and registered in `_pending`.
    2. `tool.confirmation_request {id, name, risk, args}` is published on the
       bus (observability + future transports; best-effort — a broken bus
       must not break consent).
    3. The local dialog opens on the UI thread via the queued
       `_consent_request` signal; its answer resolves the request.
    4. Alternatively, `resolve(id, allowed)` (dashboard/CLI/test transport)
       resolves the same request — FIRST answer wins, later ones are no-ops.
    5. `tool.confirmation_resolved {id, allowed, via}` is published and the
       boolean returns to the policy engine. Timeout → deny (fail closed).
    """

    def __init__(self, ui, timeout_s: float = 45.0, bus=None):
        self._ui = ui
        self._timeout_s = timeout_s
        self._bus = bus
        self._pending: dict[str, asyncio.Future] = {}
        self._loops: dict[str, asyncio.AbstractEventLoop] = {}

    async def request(self, call, risk) -> bool:
        win = getattr(self._ui, "_win", None)
        if win is None or not callable(getattr(win, "_consent_request", None)):
            return False
        loop = asyncio.get_running_loop()
        request_id = uuid.uuid4().hex
        future: asyncio.Future = loop.create_future()
        self._pending[request_id] = future
        self._loops[request_id] = loop

        def on_answer(allowed: bool) -> None:
            # Runs on the UI thread — always hop back via call_soon_threadsafe
            # (the codebase's established cross-thread pattern).
            loop.call_soon_threadsafe(self._settle, request_id,
                                      bool(allowed), "dialog")

        try:
            self._publish("tool.confirmation_request", {
                "id": request_id,
                "name": str(call.name),
                "risk": str(risk.value),
                "args": dict(call.args),
            })
            win._consent_request(str(call.name), str(risk.value),
                                 str(dict(call.args)), on_answer)
            allowed = await asyncio.wait_for(future, timeout=self._timeout_s)
        except asyncio.TimeoutError:
            print(f"[Consent] {call.name} - no answer in "
                  f"{self._timeout_s}s, denied")
            allowed = False
        finally:
            self._pending.pop(request_id, None)
            self._loops.pop(request_id, None)
        return allowed

    def resolve(self, request_id: str, allowed: bool) -> bool:
        """Answer a pending request from an external transport (D2). Safe to
        call from any thread. Returns True when THIS call settled the
        request; False when the id is unknown or already answered (the
        first answer always wins)."""
        if request_id not in self._pending:
            return False
        loop = self._loops.get(request_id)
        if loop is None or loop.is_closed():
            return False
        loop.call_soon_threadsafe(self._settle, request_id,
                                  bool(allowed), "remote")
        return True

    def pending_requests(self) -> tuple[str, ...]:
        return tuple(self._pending)

    def _settle(self, request_id: str, allowed: bool, via: str) -> None:
        future = self._pending.get(request_id)
        if future is None or future.done():
            return  # first answer wins; late answers are no-ops
        future.set_result(allowed)
        self._publish("tool.confirmation_resolved",
                      {"id": request_id, "allowed": allowed, "via": via})

    def _publish(self, event_type: str, payload: dict) -> None:
        if self._bus is None:
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return  # no loop on this thread — observability stays best-effort
        try:
            from kernel.types import Event
            loop.create_task(
                self._bus.publish(Event(type=event_type, payload=payload,
                                        source="consent")))
        except Exception:  # noqa: BLE001 — observability must never break consent
            pass


__all__ = ["ConsentGate"]
