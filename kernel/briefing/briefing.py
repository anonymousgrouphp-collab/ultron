"""kernel/briefing/briefing.py — P2-F: assemble + render the J-08 briefing."""

from __future__ import annotations

import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["BRIEFING_JOB_KIND", "Briefing", "Section", "build_briefing",
           "make_briefing_kind", "render"]

BRIEFING_JOB_KIND = "briefing"
Fetcher = Callable[[str], "tuple[int, str]"]
NEWS_PER_FEED = 3


@dataclass(frozen=True)
class Section:
    title: str
    lines: tuple[str, ...] = ()
    source: str = ""


@dataclass(frozen=True)
class Briefing:
    sections: tuple[Section, ...] = ()
    generated_ts: float = field(default_factory=time.time)


# ------------------------------------------------------------------ sections

def _weather_section(weather: Mapping[str, Any] | None,
                     fetch: Fetcher) -> Section | None:
    if not weather or "latitude" not in weather or "longitude" not in weather:
        return None
    url = (f"https://api.open-meteo.com/v1/forecast?latitude={weather['latitude']}"
           f"&longitude={weather['longitude']}"
           "&daily=temperature_2m_max,temperature_2m_min,"
           "precipitation_probability_max&forecast_days=2&timezone=auto")
    try:
        status, body = fetch(url)
        if status >= 400:
            raise ValueError(f"HTTP {status}")
        import json
        daily = json.loads(body)["daily"]
        lines = []
        for i, day in enumerate(daily["time"][:2]):
            lines.append(
                f"{day}: {daily['temperature_2m_max'][i]:.0f}° / "
                f"{daily['temperature_2m_min'][i]:.0f}°C, rain "
                f"{daily['precipitation_probability_max'][i]}%")
        return Section("Weather", tuple(lines), "open-meteo")
    except Exception as exc:  # noqa: BLE001 — a dead source can't kill the brief
        log.info("weather section unavailable: %s", type(exc).__name__)
        return Section("Weather", ("(unavailable)",), "open-meteo")


def _news_section(feeds: Sequence[str], fetch: Fetcher) -> Section | None:
    if not feeds:
        return None
    lines: list[str] = []
    for feed in feeds:
        try:
            status, body = fetch(str(feed))
            if status >= 400:
                raise ValueError(f"HTTP {status}")
            titles = _rss_titles(body)
            if not titles:
                lines.append(f"{feed}: (no items)")
            lines.extend(f"• {title}" for title in titles[:NEWS_PER_FEED])
        except Exception as exc:  # noqa: BLE001
            log.info("feed %s unavailable: %s", feed, type(exc).__name__)
            lines.append(f"{feed}: (unavailable)")
    return Section("News", tuple(lines), "rss") if lines else None


def _rss_titles(xml_text: str) -> list[str]:
    """Item titles from RSS 2.0 (<item><title>) or Atom (<entry><title>)."""
    root = ET.fromstring(xml_text)
    titles = []
    for tag in ("item", "entry"):
        for item in root.iter(tag):
            head = item.find("title")
            if head is not None and head.text:
                titles.append(" ".join(head.text.split()))
    return titles


def _calendar_section(ics_path: str | None) -> Section | None:
    if not ics_path:
        return None
    try:
        text = Path(ics_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return Section("Calendar", ("(calendar unavailable)",), "local-ics")
    today = date.today()
    horizon = {today, today + timedelta(days=1)}
    lines = []
    for block in text.split("BEGIN:VEVENT")[1:]:
        start = _ics_value(block, "DTSTART")
        summary = _ics_value(block, "SUMMARY")
        if not start or not summary:
            continue
        day = start.split("T")[0]
        try:
            event_day = datetime.strptime(day, "%Y%m%d").date()
        except ValueError:
            continue
        if event_day in horizon:
            clock = start[9:13] if "T" in start else ""
            when = f" {clock[:2]}:{clock[2:]}" if clock else ""
            lines.append(f"{event_day.isoformat()}{when} — {summary}")
    return Section("Calendar", tuple(lines) or ("(nothing scheduled)",),
                   "local-ics")


def _ics_value(block: str, prop: str) -> str:
    for line in block.splitlines():
        head, _, value = line.partition(":")
        if head.split(";")[0].strip().upper() == prop and value.strip():
            return value.strip().replace("\\,", ",").replace("\\;", ";")
    return ""


def _passthrough_section(title: str, items: Iterable[str] | None,
                         empty: str) -> Section | None:
    if items is None:
        return None
    lines = tuple(str(item) for item in items)
    return Section(title, lines or (empty,), "passed-in")


# ------------------------------------------------------------------ assemble

def build_briefing(
    *,
    weather: Mapping[str, Any] | None = None,
    feeds: Sequence[str] = (),
    calendar_path: str | None = None,
    events: Iterable[str] | None = None,
    memory_highlights: Iterable[str] | None = None,
    fetch: Fetcher | None = None,
) -> Briefing:
    """Assemble the briefing. Every section degrades independently — a dead
    source becomes an '(unavailable)' line, never an exception. Sections the
    caller left unset (None/empty) are omitted entirely."""
    do_fetch = fetch or (lambda url: (_ for _ in ()).throw(
        RuntimeError("no fetch transport wired")))
    sections = [
        _weather_section(weather, do_fetch),
        _news_section(feeds, do_fetch),
        _calendar_section(calendar_path),
        _passthrough_section("Overnight", events, "(nothing notable)"),
        _passthrough_section("Memory highlights", memory_highlights,
                             "(nothing flagged)"),
    ]
    return Briefing(sections=tuple(s for s in sections if s is not None))


def render(briefing: Briefing) -> str:
    stamp = datetime.fromtimestamp(briefing.generated_ts).strftime("%Y-%m-%d")
    blocks = [f"ULTRON briefing — {stamp}"]
    for section in briefing.sections:
        blocks.append("")
        blocks.append(f"[{section.title}]")
        blocks.extend(f"  {line}" for line in section.lines)
    return "\n".join(blocks)


# ------------------------------------------------------------------ queue tie

def make_briefing_kind(**section_args: Any):
    """Build an Orchestrator step handler for kind "briefing" (register with
    `orchestrator.register_kind(BRIEFING_JOB_KIND, ...)`) so a briefing can
    run as a durable, checkpointed queue job."""
    from kernel.orchestrator.runner import StepFailure

    async def handler(step, outputs, ctx):
        if section_args.get("fetch") is None:
            raise StepFailure("briefing steps need a fetch transport wired")
        brief = build_briefing(**section_args)
        return {"text": render(brief),
                "sections": [s.title for s in brief.sections]}

    return handler
