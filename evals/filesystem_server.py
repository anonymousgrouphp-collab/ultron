"""evals/filesystem_server.py — a REAL external MCP server for the Phase-2 gate.

Runs as its own process over stdio (the standard MCP host integration) and
serves report-file tools jailed to one root directory. In live deployment the
same server would be pointed at the user's Desktop; in the gate it points at
the eval sandbox.

Run:  python evals/filesystem_server.py <root-dir>
"""

import sys
from pathlib import Path

from fastmcp import FastMCP

root = Path(sys.argv[1]).resolve()
root.mkdir(parents=True, exist_ok=True)
mcp = FastMCP("ultron-reports")


@mcp.tool
def write_report(name: str, text: str) -> str:
    """Write a report file into the reports root (path-jailed)."""
    candidate = (root / name).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("path escapes the reports root")
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(text, encoding="utf-8", newline="")
    return f"wrote {name} ({len(text.encode('utf-8'))} bytes)"


@mcp.tool
def read_report(name: str) -> str:
    """Read a report file from the reports root."""
    candidate = (root / name).resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("path escapes the reports root")
    return candidate.read_text(encoding="utf-8")


@mcp.tool
def list_reports() -> list[str]:
    """List report files in the reports root."""
    return sorted(p.name for p in root.iterdir() if p.is_file())


mcp.run()
