"""kernel/research/web.py — P2-D: web_read tool + the research→report plan."""

from __future__ import annotations

import logging
import re
import urllib.request
from collections.abc import Callable
from typing import Any

from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

__all__ = ["DEFAULT_TEXT_CAP", "build_research_tools", "research_report_plan"]

DEFAULT_TEXT_CAP = 8000
_UA = "ULTRON-kernel-research/0.1 (+local personal assistant)"

Fetcher = Callable[[str], "tuple[int, str]"]


def _default_fetch(url: str) -> tuple[int, str]:
    """GET with a UA + timeout; returns (status, body). Non-2xx → clean fail
    upstream; this helper raises only on network-level errors."""
    request = urllib.request.Request(url, headers={"User-Agent": _UA})
    with urllib.request.urlopen(request, timeout=15) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.status, response.read().decode(charset, errors="replace")


def _extract(html: str, cap: int) -> dict[str, Any]:
    """Title + readable text from HTML. Scripts/styles stripped; whitespace
    collapsed; body capped at `cap` chars (truncated flag set honestly)."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover — bs4 is a pinned dep
        raise RuntimeError("beautifulsoup4 is not installed") from exc
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    title = soup.title.string.strip() if soup.title and soup.title.string else ""
    text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
    return {
        "title": title,
        "text": text[:cap],
        "truncated": len(text) > cap,
        "chars": len(text),
    }


def build_research_tools(
    registry,
    *,
    fetch: Fetcher | None = None,
    consent: Callable[[], bool] | None = None,
    enabled: bool | None = None,
    text_cap: int = DEFAULT_TEXT_CAP,
) -> None:
    """Register the research tools. `fetch` is injectable for tests; the
    consent gate is `enabled` (fixed bool, tests) or `consent()` (callable —
    the wiring layer reads `web_research_enabled` from config there, keeping
    config.loader out of the kernel). No gate wired → tools refuse (safe
    default: research off until the user consents)."""
    do_fetch: Fetcher = fetch or _default_fetch

    def allowed() -> bool:
        if enabled is not None:
            return enabled
        if consent is not None:
            try:
                return bool(consent())
            except Exception:  # noqa: BLE001 — a broken gate must deny
                log.exception("research consent gate failed — denying")
                return False
        return False

    @registry.tool(
        name="web_read",
        description="Fetch a web page and return its title and readable text "
                    "(scripts/styles stripped, length-capped).",
        parameters={
            "type": "object",
            "properties": {"url": {"type": "string",
                                   "description": "absolute http(s) URL"}},
            "required": ["url"],
        },
        risk=RiskClass.READ,
    )
    def web_read(call: ToolCall) -> ToolResult:
        if not allowed():
            return ToolResult.fail(
                call, "web research is disabled by the user "
                      "(set web_research_enabled=true in config to consent)")
        url = str(call.args.get("url", "")).strip()
        if not re.match(r"^https?://[^\s]+$", url):
            return ToolResult.fail(call, "web_read needs an absolute http(s) URL")
        try:
            status, body = do_fetch(url)
        except Exception as exc:  # noqa: BLE001 — clean network-failure text
            log.info("web_read fetch failed for %s: %s", url, type(exc).__name__)
            return ToolResult.fail(call, f"could not fetch {url} "
                                         f"({type(exc).__name__})")
        if status >= 400:
            return ToolResult.fail(call, f"HTTP {status} for {url}")
        return ToolResult.success(call, data={"url": url, **_extract(body, text_cap)})


def research_report_plan(
    topic: str,
    *,
    search_tool: str = "web_search",
    report_tool: str = "fs_write_report",
    report_name: str = "research_report.txt",
    search_url_key: str = "first_url",
) -> list[dict[str, Any]]:
    """Build the Phase-2 gate's research→report plan (J-05 + J-08 pattern):

        search(topic) → web_read(first result) → report tool writes it out

    `search_tool` must return a mapping containing `search_url_key` (default
    "first_url"); `report_tool` must accept {name, text} — the mounted
    filesystem MCP server's write_report is the intended target ("report to
    Desktop"). Returns plain step dicts for a "plan" job payload."""
    if not topic.strip():
        raise ValueError("research topic is required")
    return [
        {"id": "search", "kind": "tool",
         "spec": {"name": search_tool, "args": {"query": topic}}},
        {"id": "read", "kind": "tool",
         "spec": {"name": "web_read",
                  "args": {"url": "{search." + search_url_key + "}"}}},
        {"id": "report", "kind": "tool",
         "spec": {"name": report_tool,
                  "args": {"name": report_name,
                           "text": "{read.title}\n\n{read.text}"}}},
    ]
