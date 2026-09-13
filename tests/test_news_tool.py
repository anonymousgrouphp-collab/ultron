"""research/12 D9 tests: the news_headlines tool (fake transport + TTL)."""

import asyncio

from kernel.briefing.tools import DEFAULT_NEWS_FEEDS, build_news_tools
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall

RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
  <item><title>First headline</title></item>
  <item><title>Second headline</title></item>
</channel></rss>"""


def make_call(**args):
    return ToolCall(id="c-news", name="news_headlines", args=args,
                    source="test")


class FakeFetch:
    def __init__(self):
        self.calls = 0

    def __call__(self, url):
        self.calls += 1
        return 200, RSS


def _build(fetch, **kw):
    kw.setdefault("enabled", True)     # gate-off test passes enabled=False
    reg = ToolRegistry()
    build_news_tools(reg, fetch=fetch, **kw)
    return reg


def test_01_fetches_and_parses_titles():
    fetch = FakeFetch()
    reg = _build(fetch, feeds=("https://news.example/rss",))
    result = asyncio.run(reg.execute(make_call()))
    assert result.ok
    items = result.data["items"]
    assert [i["title"] for i in items] == ["First headline", "Second headline"]
    assert items[0]["feed"] == "news.example"
    assert result.data["live_fetch"] is True


def test_02_ttl_cache_serves_second_call_without_network():
    fetch = FakeFetch()
    reg = _build(fetch, feeds=("https://news.example/rss",), ttl_s=600.0)
    asyncio.run(reg.execute(make_call()))
    result = asyncio.run(reg.execute(make_call()))
    assert fetch.calls == 1                       # one network pull only
    assert result.data["live_fetch"] is False     # served from cache


def test_03_category_selects_single_feed():
    fetch = FakeFetch()
    reg = _build(fetch, feeds=("https://a.example/rss",
                               "https://b.example/rss"))
    asyncio.run(reg.execute(make_call(category="technology")))
    assert fetch.calls == 1                       # only the tech feed pulled


def test_04_dead_feed_degrades_to_unavailable():
    def dead(url):
        return 500, ""

    reg = _build(dead, feeds=("https://dead.example/rss",))
    result = asyncio.run(reg.execute(make_call()))
    assert result.ok                              # degraded, not crashed
    assert result.data["items"][0]["title"] == "(unavailable)"


def test_05_gate_off_denies():
    fetch = FakeFetch()
    reg = _build(fetch, enabled=False)
    result = asyncio.run(reg.execute(make_call()))
    assert not result.ok
    assert "disabled" in result.error


def test_05b_no_gate_configured_denies_fail_closed():
    fetch = FakeFetch()
    reg = ToolRegistry()
    build_news_tools(reg, fetch=fetch)   # neither enabled nor consent
    result = asyncio.run(reg.execute(make_call()))
    assert not result.ok


def test_06_read_risk_and_default_feeds_shape():
    fetch = FakeFetch()
    reg = _build(fetch)
    assert reg.risks()["news_headlines"] == RiskClass.READ
    assert len(DEFAULT_NEWS_FEEDS) == 3
