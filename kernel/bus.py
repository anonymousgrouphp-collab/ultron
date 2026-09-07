"""kernel/bus.py — P1-A: the typed async event bus.

The ONLY way kernel subsystems talk to each other; nothing imports main.py ever
again (roadmap §4 Phase 1). Delivery semantics (v0, deliberately simple):

- publish() assigns the monotonic sequence number + timestamp, appends to the
  bounded history ring, then awaits each matching subscriber IN SUBSCRIPTION
  ORDER (deterministic; parallel fan-out is the orchestrator's job, later).
- A raising subscriber is logged and isolated — it can never break the
  publisher or other subscribers.
- Patterns: exact type ("tool.completed"), segment wildcard ("tool.*"), or "*".
  Segments only: "tool.*" does NOT match "toolbox.filled".
- Both async and plain-sync handlers are accepted (sync is awaited if awaitable).
"""

from __future__ import annotations

import dataclasses
import inspect
import logging
from collections import deque
from collections.abc import Awaitable, Callable
from typing import Union

from .types import Event

log = logging.getLogger(__name__)

Handler = Callable[[Event], Union[Awaitable[None], None]]


class Subscription:
    """Handle returned by EventBus.subscribe(); call .cancel() to stop delivery."""

    __slots__ = ("pattern", "handler", "active")

    def __init__(self, pattern: str, handler: Handler) -> None:
        if not pattern:
            raise ValueError("subscription pattern is required")
        self.pattern = pattern
        self.handler = handler
        self.active = True

    def cancel(self) -> None:
        self.active = False


def _matches(pattern: str, event_type: str) -> bool:
    """Segment-based match: exact, 'prefix.*' (strictly under the prefix — bare
    prefix type does NOT match), or '*'."""
    if pattern == "*":
        return True
    if pattern.endswith(".*"):
        prefix = pattern[:-2].split(".")
        segs = event_type.split(".")
        return len(segs) > len(prefix) and segs[: len(prefix)] == prefix
    return pattern == event_type


class EventBus:
    """Typed async pub/sub with a bounded history ring (debug + eval traces)."""

    def __init__(self, history_size: int = 500) -> None:
        if history_size < 1:
            raise ValueError("history_size must be >= 1")
        self._subs: list[Subscription] = []
        self._history: deque[Event] = deque(maxlen=history_size)
        self._seq = 0

    # -- subscription ------------------------------------------------------

    def subscribe(self, pattern: str, handler: Handler) -> Subscription:
        sub = Subscription(pattern, handler)
        self._subs.append(sub)
        return sub

    def unsubscribe(self, sub: Subscription) -> None:
        sub.cancel()
        self._subs = [s for s in self._subs if s is not sub]

    # -- publishing --------------------------------------------------------

    async def publish(self, event: Event) -> Event:
        """Stamp + record the event, then deliver to matching subscribers in
        subscription order. Returns the stamped event (with seq assigned)."""
        self._seq += 1
        stamped = dataclasses.replace(event, seq=self._seq)
        self._history.append(stamped)
        for sub in list(self._subs):
            if not sub.active or not _matches(sub.pattern, stamped.type):
                continue
            try:
                result = sub.handler(stamped)
                if inspect.isawaitable(result):
                    await result
            except Exception:  # subscriber isolation — never break the publisher
                log.exception("bus: subscriber %r failed on %s",
                              sub.pattern, stamped.type)
        return stamped

    # -- introspection -----------------------------------------------------

    @property
    def history(self) -> tuple[Event, ...]:
        """Bounded event history (oldest first) — the eval harness replays this."""
        return tuple(self._history)

    @property
    def subscriber_count(self) -> int:
        return sum(1 for s in self._subs if s.active)


__all__ = ["EventBus", "Handler", "Subscription"]
