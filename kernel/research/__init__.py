"""kernel/research — P2-D: consent-gated web research tools (J-05).

J-05 = "Instant research & synthesis": search → read → cite → save. The
search half reuses the EXISTING ported web_search tool (no second
implementation); this package adds the reading half and the composite plan:

- `web_read`   — fetch a URL and extract readable text (title + body).
- `research_report_plan(topic)` — a P2-C plan: search → read first result →
  write a report file (typically through the mounted filesystem MCP server,
  closing the loop to "report to Desktop").

Approach note (logged on the board): the `browser-use` package was rejected —
its agent loop makes LLM calls outside the kernel gateway (Kill List #4) and
would be a second agent loop. Substrate = the already-pinned requests-grade
stack (urllib + BeautifulSoup); Playwright remains the P4-A computer-control
substrate. Consent: every tool here is disabled unless the user enables it in
config (`web_research_enabled: true`) — config-as-consent, same pattern as
the P0-A2 camera flag.
"""

from kernel.research.web import build_research_tools, research_report_plan

__all__ = ["build_research_tools", "research_report_plan"]
