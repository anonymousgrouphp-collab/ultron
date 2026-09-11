"""app/research_gate.py — live-research plan tools (Phase P3).

Moved verbatim from main.py: `web_search_url` (3-tier: DDG HTML endpoint →
ddgs package → Gemini grounded seam) and `fs_write_report` (reports folder,
path-jailed) — the P2-D plan contract, registered on the live registry.
"""

from __future__ import annotations

from kernel.types import RiskClass, ToolCall


class ResearchGateMixin:
    """Expects the host to provide _tool_runtime; BASE_DIR resolves from
    main at call time (the composition root owns the sandbox path)."""

    def _register_research_gate_tools(self) -> None:
        """Phase W: live-research plan support (the P2-D plan's contract).

        `web_search_url` returns structured results with `first_url` (the
        orchestrator plan template `{search.first_url}` needs it; the legacy
        `web_search` speaks prose). `fs_write_report` persists the finished
        report under .ultron/reports/. Both register on the live registry so
        the durable worker executes them behind policy/consent like any tool.
        """
        registry = self._tool_runtime.registry

        @registry.tool(
            name="web_search_url",
            description="Web search returning structured results "
                        "(title/snippet/url) for pipeline use. Read-only.",
            parameters={"type": "object", "properties": {
                "query": {"type": "string",
                          "description": "the search query"},
            }, "required": ["query"]},
            risk=RiskClass.READ,
        )
        def web_search_url(call: ToolCall) -> dict:
            import re as _url_re
            from actions.web_search import _ddg_search
            query = str(call.args.get("query", "")).strip()
            # Tier 1: the DuckDuckGo HTML endpoint — the ddgs API
            # package is flaky post-rename (rate-limits return junk or
            # nothing), this one is stable and on-topic.
            results: list[dict] = []
            first = ""
            if query:
                try:
                    import requests as _req
                    resp = _req.post(
                        "https://html.duckduckgo.com/html/",
                        data={"q": query},
                        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"},
                        timeout=15,
                    )
                    anchors = _url_re.findall(
                        r'class="result__a"[^>]*href="([^"]+)"', resp.text)
                    urls = [u for u in anchors if u.startswith("http")][:5]
                    results = [{"title": "", "snippet": "", "url": u}
                               for u in urls]
                    first = urls[0] if urls else ""
                except Exception:
                    pass  # fall through to the next tier
            # Tier 2: the ddgs API package (kept as fallback).
            if not first and query:
                try:
                    from actions.web_search import _ddg_search
                    results = _ddg_search(query, max_results=5)
                    first = next((r["url"] for r in results
                                  if r.get("url", "").startswith("http")), "")
                except Exception:
                    pass
            if not first and query:
                # tier 3: the Gemini grounded-search seam — pull real URLs
                # out of the grounded prose (quota-gated, last resort).
                try:
                    from actions._llm import complete_grounded_search
                    text = complete_grounded_search(
                        f"Search the web for: {query}. "
                        "Include the source page URLs.")
                    urls = [m for m in _url_re.findall(
                        r"https?://[^\s)\]>'\"]+", text)
                            if "google." not in m][:5]
                    results = [{"title": "", "snippet": "", "url": u}
                               for u in urls]
                    first = urls[0] if urls else ""
                except Exception:
                    pass  # clean empty result — the plan reports honestly
            return {"results": results[:5], "first_url": first}

        # BASE_DIR resolves from the composition root at call time
        # (the sandbox path the smoke test monkeypatches).
        from main import BASE_DIR
        reports_dir = BASE_DIR / ".ultron" / "reports"

        @registry.tool(
            name="fs_write_report",
            description="Save a finished research report (name + text) under "
                        "the assistant's reports folder. Writes files only "
                        "inside that folder.",
            parameters={"type": "object", "properties": {
                "name": {"type": "string", "description": "file name"},
                "text": {"type": "string", "description": "report body"},
            }, "required": ["name", "text"]},
            risk=RiskClass.WRITE,
        )
        def fs_write_report(call: ToolCall) -> dict:
            import re as _re
            reports_dir.mkdir(parents=True, exist_ok=True)
            safe = _re.sub(r"[^\w.\- ]", "_",
                           str(call.args.get("name", "report.txt"))).strip()
            if not safe:
                safe = "report.txt"
            path = reports_dir / safe
            if path.parent != reports_dir:
                return {"ok": False, "error": "name escapes the reports folder"}
            text = str(call.args.get("text", ""))
            path.write_text(text, encoding="utf-8")
            return {"ok": True, "path": str(path), "bytes": len(text)}

