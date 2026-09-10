"""kernel/proactive/triggers.py — Phase P3: proactive intelligence triggers.

Extends the existing ProactiveEngine (P4-D) with:
1. Time-based triggers (morning briefing, commute reminders, meeting prep)
2. Context-aware suggestions (learned patterns, repeated requests)
3. Idle-time triggers (long silence = suggestion to do something)

These triggers fire proactive.decision events that the voice session speaks.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

__all__ = ["TimeTrigger", "ContextTrigger", "IdleTrigger"]


@dataclass
class TimeTrigger:
    """Fires at specific times of day.

    Parameters
    ----------
    hour:
        Hour to fire (0-23).
    minute:
        Minute to fire (0-59).
    message:
        The proactive message to speak.
    cooldown_s:
        Minimum seconds between firings (default 3600 = 1 hour).
    """

    hour: int = 7
    minute: int = 0
    message: str = ""
    cooldown_s: float = 3600.0
    _last_fired: float = 0.0

    def should_fire(self, now: float | None = None) -> bool:
        """Check if this trigger should fire now."""
        if now is None:
            now = time.time()

        from datetime import datetime
        dt = datetime.fromtimestamp(now)

        # Check if we're within 5 minutes of the target time
        target_minutes = self.hour * 60 + self.minute
        current_minutes = dt.hour * 60 + dt.minute
        if abs(current_minutes - target_minutes) > 5:
            return False

        # Check cooldown
        if (now - self._last_fired) < self.cooldown_s:
            return False

        self._last_fired = now
        return True

    def get_message(self) -> str:
        """Get the message to speak when this trigger fires."""
        return self.message


@dataclass
class ContextTrigger:
    """Fires based on learned user patterns.

    Parameters
    ----------
    pattern:
        The pattern to match (e.g. "user usually asks about weather at 7am").
    message:
        The proactive message to speak.
    min_occurrences:
        Minimum times the pattern must have been seen before firing.
    cooldown_s:
        Minimum seconds between firings.
    """

    pattern: str = ""
    message: str = ""
    min_occurrences: int = 3
    cooldown_s: float = 7200.0
    _occurrences: int = 0
    _last_fired: float = 0.0

    def record_occurrence(self) -> None:
        """Record that the pattern was observed."""
        self._occurrences += 1

    def should_fire(self, now: float | None = None) -> bool:
        """Check if this trigger should fire now."""
        if now is None:
            now = time.time()

        if self._occurrences < self.min_occurrences:
            return False

        if (now - self._last_fired) < self.cooldown_s:
            return False

        self._last_fired = now
        return True

    def get_message(self) -> str:
        """Get the message to speak when this trigger fires."""
        return self.message


@dataclass
class IdleTrigger:
    """Fires when the user has been idle for too long.

    Parameters
    ----------
    idle_threshold_s:
        Seconds of silence before firing (default 900 = 15 minutes).
    message:
        The proactive message to speak.
    cooldown_s:
        Minimum seconds between firings.
    """

    idle_threshold_s: float = 900.0
    message: str = "Is there anything you need help with, sir?"
    cooldown_s: float = 1800.0
    _last_fired: float = 0.0

    def should_fire(self, last_user_speech: float, now: float | None = None) -> bool:
        """Check if this trigger should fire based on user idle time."""
        if now is None:
            now = time.time()

        idle_time = now - last_user_speech
        if idle_time < self.idle_threshold_s:
            return False

        if (now - self._last_fired) < self.cooldown_s:
            return False

        self._last_fired = now
        return True

    def get_message(self) -> str:
        """Get the message to speak when this trigger fires."""
        return self.message
