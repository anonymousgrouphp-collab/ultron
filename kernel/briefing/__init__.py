"""kernel/briefing — P2-F: the morning briefing pipeline v1 (J-08).

Sections (research/08 §4, v0 scope):
- weather    — Open-Meteo (free, no key; lat/lon passed in by the wiring layer)
- news       — RSS feeds (stdlib XML parse; feed list passed in)
- calendar   — local .ics events for today/tomorrow (zero-setup default)
- events     — overnight system/job events (passed in: queue + audit are the
               honest "while you were away" source)
- memory     — highlights from the memory engine (passed in; Phase 3 tightens)

Data comes IN as plain values — the kernel never reads config or touches
network APIs beyond the injected `fetch`. Delivery via kernel/notify
(ntfy + Telegram). Queue integration: `make_briefing_kind(...)` builds a
step handler the P2-C Orchestrator registers as kind "briefing".
"""

from kernel.briefing.briefing import (
    Briefing,
    BRIEFING_JOB_KIND,
    Section,
    build_briefing,
    make_briefing_kind,
    render,
)

__all__ = [
    "BRIEFING_JOB_KIND",
    "Briefing",
    "Section",
    "build_briefing",
    "make_briefing_kind",
    "render",
]
