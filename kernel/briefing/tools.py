"""kernel/briefing/tools.py — research/12 D9: the news-headlines tool.

ada_local's briefing insight, on ULTRON's seams: "what's the news?" needs
ONE read-only tool that returns current headlines for the BRAIN to curate
(select, group, narrate) — the LLM-editing step lives in the model, not in
a second tool (Kill List #2). A per-feed TTL cache keeps repeated asks off
the network (the K9 search-cache pattern).

Transport is injectable for hermetic tests; the consent gate follows the
research-gate pattern (broken gate → deny).
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any
import urllib.request
from typing import Callable
from urllib.parse import urlsplit

from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

__all__ = ["DEFAULT_NEWS_FEEDS", "build_news_tools"]

Fetcher = Callable[[str], "tuple[int, str]"]

DEFAULT_NEWS_FEEDS = (
    "https://feeds.bbci.co.uk/news/rss.xml",                      # top stories
    "https://feeds.bbci.co.uk/news/technology/rss.xml",           # technology
    "https://feeds.bbci.co.uk/news/science_and_environment/rss.xml",  # science
)


def _default_fetch(url: str) -> tuple[int, str]:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (ULTRON assistant)"})
    with urllib.request.urlopen(request, timeout=10.0) as resp:
        return int(resp.status), resp.read().decode("utf-8", errors="replace")


def _feed_label(url: str) -> str:
    host = urlsplit(url).netloc.removeprefix("www.")
    return host or url


def build_news_tools(registry, *, fetch: Fetcher | None = None,
                     feeds: tuple[str, ...] | None = None,
                     ttl_s: float = 900.0, max_per_feed: int = 6,
                     enabled: bool | None = None,
                     consent: Callable[[], bool] | None = None) -> None:
    """Register `news_headlines`. fetch/feeds injectable; gate follows the
    research-gate pattern (`enabled` fixed for tests, `consent()` callable
    for the wiring layer; a broken gate DENIES)."""
    do_fetch: Fetcher = fetch or _default_fetch
    feed_list = tuple(feeds) if feeds is not None else DEFAULT_NEWS_FEEDS
    cache: dict[str, tuple[float, list[str]]] = {}
    cache_lock = threading.Lock()

    def allowed() -> bool:
        if enabled is not None:
            return bool(enabled)
        if consent is not None:
            try:
                return bool(consent())
            except Exception:  # noqa: BLE001 — a broken gate must deny
                log.exception("news consent gate failed — denying")
                return False
        return False

    def _headlines(feed: str) -> tuple[list[str], bool]:
        """Feed titles with a per-feed TTL cache; (titles, cache_hit)."""
        now = time.monotonic()
        with cache_lock:
            hit = cache.get(feed)
            if hit is not None and (now - hit[0]) < ttl_s:
                return hit[1], True
        from kernel.briefing.briefing import _rss_titles
        status, body = do_fetch(feed)
        if status >= 400:
            raise ValueError(f"HTTP {status}")
        titles = _rss_titles(body)[:max_per_feed]
        with cache_lock:
            cache[feed] = (now, titles)
        return titles, False

    @registry.tool(
        name="news_headlines",
        description="Fetch current news headlines across categories (top "
                    "stories, technology, science). Returns raw titles per "
                    "feed — YOU curate: pick the notable ones, group by "
                    "theme, and narrate them naturally when the user asks "
                    "for the news or a briefing.",
        parameters={
            "type": "object",
            "properties": {
                "category": {"type": "string", "enum": ["top", "technology",
                                                        "science", "all"],
                             "description": "which feeds to pull "
                                            "(default: all)"},
            },
        },
        risk=RiskClass.READ,
    )
    def news_headlines(call: ToolCall) -> "ToolResult | dict[str, Any]":
        if not allowed():
            return ToolResult.fail(
                call, "news fetching is disabled "
                      "(set news_enabled=true in config to enable it)")
        category = str(call.args.get("category") or "all").strip().lower()
        selected: tuple[str, ...]
        if category in ("top", "technology", "science"):
            selected = (feed_list[("top", "technology", "science").index(
                category)],)
        else:
            selected = feed_list
        items: list[dict] = []
        fetched_live = False
        for feed in selected:
            try:
                titles, cache_hit = _headlines(feed)
                if not cache_hit:
                    fetched_live = True
                items.extend({"feed": _feed_label(feed), "title": t}
                             for t in titles)
            except Exception as exc:  # noqa: BLE001 — a dead feed degrades
                log.info("feed %s unavailable: %s", feed,
                         type(exc).__name__)
                items.append({"feed": _feed_label(feed),
                              "title": "(unavailable)"})
        return {"items": items, "live_fetch": fetched_live}
