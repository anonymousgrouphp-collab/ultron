"""kernel/proactive/engine.py — P4-D: the honest event-driven proactive engine (J-19).

Replaces the legacy silence-timer (actions/proactive.py — fixed in P0-A5 but
still a *silence heuristic* wearing a "proactive" name). Research/08 §6:

    Triggers: time/cron · calendar-reminder · mqtt-event (door, motion, device
    fault) · system-event · memory-trigger · job-completion.
    Policy: every proactive speech act declares its trigger + consent class
    (always / ask-once / never-when-busy).

Design:
- Rules map BUS EVENT PATTERNS (the P1-A bus segment wildcards) to proactive
  acts. The engine is a bus subscriber — it never polls, never imports the
  app, never speaks (it returns/publishes Emissions; the app layer speaks).
- Consent classes are load-bearing:
    always          — fires (cooldowns still apply)
    ask_once        — first fire ASKS (the app surfaces a yes/no); the answer
                      is remembered across restarts (state file)
    never_when_busy — suppressed while a busy-signal callback reports the user
                      is active; the suppression is recorded, not silent
- Deterministic by injected clock: the P1-B ProactiveEngine's uptime bug
  (fresh boots never triggering because uptime < cooldown) is pinned away by
  construction — time comes from the caller, never from uptime arithmetic.
- Cooldowns + hourly caps: a chatty butler is worse than none.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable

from kernel.bus import EventBus, _matches
from kernel.types import Event

log = logging.getLogger(__name__)

__all__ = ["ConsentClass", "Emission", "ProactiveEngine", "TriggerRule"]


class ConsentClass(str, Enum):
    ALWAYS = "always"
    ASK_ONCE = "ask_once"
    NEVER_WHEN_BUSY = "never_when_busy"


@dataclass(frozen=True)
class TriggerRule:
    """One proactive behavior: WHICH event, WHAT to say, HOW consented."""

    name: str
    event: str                     # bus event pattern ("job.completed", "home.*")
    message: str                   # template; {fields} from the event payload
    consent: ConsentClass = ConsentClass.ALWAYS
    cooldown_s: float = 900.0      # per-rule re-fire guard
    max_per_hour: int = 4

    def render(self, payload: dict) -> str:
        try:
            return self.message.format(**{k: v for k, v in payload.items()
                                          if isinstance(v, (str, int, float))})
        except (KeyError, IndexError):
            return self.message


@dataclass(frozen=True)
class Emission:
    """What the engine decided about one trigger evaluation."""

    rule: str
    message: str
    outcome: str                   # "fire" | "ask" | "suppressed-busy" |
                                   # "suppressed-cooldown" | "suppressed-hour-cap" |
                                   # "suppressed-denied"
    detail: str = ""
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, object]:
        return {"rule": self.rule, "message": self.message,
                "outcome": self.outcome, "detail": self.detail, "ts": self.ts}


def _default_now() -> float:
    return time.time()


class ProactiveEngine:
    """Evaluate rules against bus events + a clock. Attach to an EventBus or
    drive `handle(event)` directly (tests, the HUD, the orchestrator)."""

    def __init__(self, rules: list[TriggerRule], *,
                 state_path: Path | None = None,
                 busy: Callable[[], bool] | None = None,
                 now: Callable[[], float] = _default_now) -> None:
        if not rules:
            raise ValueError("ProactiveEngine needs at least one rule")
        names = [r.name for r in rules]
        if len(set(names)) != len(names):
            raise ValueError(f"rule names must be unique, got {names}")
        self._rules = rules
        self._busy = busy
        self._now = now
        self._state_path = state_path
        # rule name -> {"denied": bool, "granted": bool}
        self._consents: dict[str, dict[str, bool]] = {}
        # rule name -> [fire timestamps] (cooldown + hour-cap windows)
        self._fires: dict[str, list[float]] = {r.name: [] for r in rules}
        self._load_state()

    # -- lifecycle -----------------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Subscribe one handler per distinct event pattern. Emissions are
        republished as `proactive.decision` events so the HUD/dashboard can
        show suppressed reasons too (a silent suppression is a bug magnet)."""
        self._bus = bus
        for pattern in sorted({r.event for r in self._rules}):
            bus.subscribe(pattern, self._on_bus_event)

    async def _on_bus_event(self, event: Event) -> None:
        for emission in self.handle(event):
            log.info("proactive %s: %s", emission.outcome, emission.message)
            if getattr(self, "_bus", None) is not None:
                await self._bus.publish(Event(
                    type="proactive.decision",
                    payload=emission.as_dict(),
                    source="proactive",
                ))

    # -- evaluation ------------------------------------------------------------

    def handle(self, event: Event) -> list[Emission]:
        """Evaluate every rule against one event. Pure decision logic: it
        never speaks, never calls tools — it returns Emissions for the app
        layer (and marks internal state: cooldowns, hour caps, consents)."""
        out: list[Emission] = []
        now = self._now()
        for rule in self._rules:
            if not _matches(rule.event, event.type):
                continue
            out.append(self._evaluate(rule, dict(event.payload), now))
        return out

    def _evaluate(self, rule: TriggerRule, payload: dict, now: float) -> Emission:
        message = rule.render(payload)
        fired = self._fires[rule.name]

        # ask_once consents are sticky (restart-safe via the state file)
        consent_state = self._consents.get(rule.name, {})
        if consent_state.get("denied"):
            return Emission(rule.name, message, "suppressed-denied")
        granted = consent_state.get("granted", False)

        if rule.consent is ConsentClass.ASK_ONCE and not granted:
            return Emission(rule.name, message, "ask",
                            detail="first occurrence — ask the user once")

        if rule.consent is ConsentClass.NEVER_WHEN_BUSY and self._busy and self._busy():
            return Emission(rule.name, message, "suppressed-busy")

        recent = [t for t in fired if now - t < 3600.0]
        if recent and now - max(fired) < rule.cooldown_s:
            return Emission(rule.name, message, "suppressed-cooldown",
                            detail=f"{rule.cooldown_s:g}s cooldown")
        if len(recent) >= rule.max_per_hour:
            return Emission(rule.name, message, "suppressed-hour-cap",
                            detail=f"max {rule.max_per_hour}/h")

        self._fires[rule.name] = [*recent, now]
        self._save_state()
        return Emission(rule.name, message, "fire")

    # -- consent decisions (the ask_once round-trip) ---------------------------

    def record_consent(self, rule_name: str, allowed: bool) -> None:
        if rule_name not in {r.name for r in self._rules}:
            raise ValueError(f"unknown rule {rule_name!r}")
        self._consents.setdefault(rule_name, {})["granted" if allowed else "denied"] = True
        if allowed:
            self._consents[rule_name]["denied"] = False
        self._save_state()

    # -- persistence -------------------------------------------------------------

    def _load_state(self) -> None:
        if self._state_path is None or not self._state_path.exists():
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            self._consents = {k: v for k, v in data.get("consents", {}).items()}
        except (json.JSONDecodeError, OSError):
            log.warning("proactive state unreadable — starting clean")

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"consents": self._consents}), encoding="utf-8")
        except OSError:  # pragma: no cover — state is best-effort
            log.warning("proactive state save failed", exc_info=True)
