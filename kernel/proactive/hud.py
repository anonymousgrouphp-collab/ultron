"""kernel/proactive/hud.py — P4-D: the HUD v2 event feed (J-20), kernel half.

J-20's full "holographic HUD" is a web-rendering project (research/07); the
KERNEL half is well-defined today: a bounded, renderable, JSON-able snapshot
of what the HUD should show, assembled from the event bus — the dashboard
becomes just another bus client. One source of truth, no polling.

HudFeed subscribes to the interesting segments (jobs, proactive decisions,
memory consolidation, home events, tool failures) and maintains:
- `cards`: bounded newest-first set of display records
- `stats`: monotonically increasing counters (events seen per segment)

Everything is plain data — the kernel never renders, never serves HTTP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from kernel.bus import EventBus
from kernel.types import Event

__all__ = ["HudCard", "HudFeed", "INTEREST_SEGMENTS"]

# what the HUD cares about (segment prefixes); failures matter as much as wins
INTEREST_SEGMENTS = {
    "job": "task",            # orchestrator job lifecycle
    "proactive": "proactive",  # engine decisions (fires AND suppressions)
    "memory": "memory",        # consolidation/reflection events
    "home": "home",            # HA/MQTT events (P4-C)
    "tool": "tools",           # tool failures (tool.failed) surface as alerts
}


@dataclass(frozen=True)
class HudCard:
    """One displayable HUD record."""

    kind: str                  # task | proactive | memory | home | tools
    title: str
    line: str
    ts: float = field(default_factory=time.time)

    def as_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, "title": self.title, "line": self.line,
                "ts": self.ts}


def _card_for(event: Event) -> HudCard | None:
    seg = event.type.split(".")[0]
    kind = INTEREST_SEGMENTS.get(seg)
    if kind is None:
        return None
    p = dict(event.payload)
    if event.type.startswith("job."):
        return HudCard(kind, f"{p.get('kind', 'job')} {p.get('job_id', '')}",
                       f"{event.type}: {p.get('status', p.get('state', ''))}")
    if event.type.startswith("proactive."):
        return HudCard(kind, str(p.get("rule", "rule")), f"{p.get('outcome')}: "
                                                     f"{p.get('message', '')[:80]}")
    if event.type.startswith("memory."):
        return HudCard(kind, "memory", event.type)
    if event.type.startswith("home."):
        return HudCard(kind, str(p.get("topic", p.get("entity", "home"))),
                       f"{event.type}: {str(p)[:80]}")
    if event.type == "tool.failed":
        return HudCard(kind, str(p.get("name", "tool")),
                       f"failed: {p.get('error', '') or 'unknown error'}")
    return None


class HudFeed:
    """Bounded HUD state assembled from bus events."""

    def __init__(self, *, max_cards: int = 60) -> None:
        if max_cards < 1:
            raise ValueError("max_cards must be >= 1")
        self._max = max_cards
        self._cards: list[HudCard] = []
        self._stats: dict[str, int] = {}

    def attach(self, bus: EventBus) -> None:
        bus.subscribe("*", self._on_event)

    async def _on_event(self, event: Event) -> None:
        self.observe(event)

    def observe(self, event: Event) -> None:
        seg = event.type.split(".")[0]
        self._stats[seg] = self._stats.get(seg, 0) + 1
        card = _card_for(event)
        if card is None:
            return
        self._cards.append(card)
        if len(self._cards) > self._max:
            del self._cards[: len(self._cards) - self._max]

    # -- render ---------------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        """The full HUD state — JSON-able, newest-first, bounded."""
        return {
            "cards": [c.as_dict() for c in reversed(self._cards)],
            "stats": dict(sorted(self._stats.items())),
            "ts": time.time(),
        }
