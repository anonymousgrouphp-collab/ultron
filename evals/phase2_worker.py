"""evals/phase2_worker.py — the Phase-2 gate's worker process.

One orchestrator worker with the full gate toolset: web_search (gate fake),
web_read (P2-D, consented), and the MOUNTED external filesystem MCP server
(P2-B). Launched by phase2_gate.py; killed mid-job by it too.

Run:  python evals/phase2_worker.py <queue.db> <reports_root> [--slow-read]
"""

import asyncio
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from kernel.mcp_client import mount_stdio  # noqa: E402
from kernel.orchestrator import JobQueue, Orchestrator  # noqa: E402
from kernel.policy import AuditLog, PolicyEngine  # noqa: E402
from kernel.research import build_research_tools  # noqa: E402
from kernel.tools import ToolRegistry  # noqa: E402
from kernel.types import RiskClass, ToolCall, ToolResult  # noqa: E402

SLOW_READ_S = 2.0


async def build_worker(db: Path, reports_root: Path, slow_read: bool):
    queue = JobQueue(db)
    registry = ToolRegistry()

    @registry.tool(
        name="web_search",
        description="Gate-scoped search: returns the first result URL for "
                    "the canned gate corpus.",
        parameters={"type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]},
        risk=RiskClass.READ,
    )
    def web_search(call):
        return {"first_url": "https://gate.local/fusion",
                "query": str(call.args.get("query", ""))}

    async def yes(call: ToolCall, risk: RiskClass) -> bool:
        print(f"[gate] consent granted: {call.name} ({risk.value})",
              flush=True)
        return True

    build_research_tools(
        registry, enabled=True,
        fetch=lambda url: _fetch(url, slow_read),
    )

    audit = AuditLog(BASE / ".ultron" / "eval" / "phase2" / "audit.sqlite3")
    policy = PolicyEngine(audit=audit)

    mount = await mount_stdio(
        sys.executable,
        [str(BASE / "evals" / "filesystem_server.py"), str(reports_root)],
        prefix="fs", label="gate-reports",
        risk_overrides={"write_report": RiskClass.WRITE,
                        "read_report": RiskClass.READ},
        include=["write_report", "read_report", "list_reports"],
    )
    mount.into(registry)
    return queue, Orchestrator(queue, registry, policy, consent=yes,
                               lease_s=2.0, poll_s=0.2), mount


def _fetch(url: str, slow: bool) -> tuple[int, str]:
    if slow:
        time.sleep(SLOW_READ_S)  # widens the kill window deterministically
    return 200, ("<html><head><title>Fusion energy: net gain achieved</title>"
                 "</head><body><script>x()</script>"
                 "<p>Researchers report a sustained net energy gain.</p>"
                 "</body></html>")


async def amain() -> int:
    db = Path(sys.argv[1])
    reports_root = Path(sys.argv[2])
    slow_read = "--slow-read" in sys.argv[3:]
    queue, orchestrator, mount = await build_worker(db, reports_root, slow_read)
    try:
        # ONE event loop for mount + worker: the MCP session is bound to the
        # loop that built it — split loops would use it dead.
        await orchestrator.run_worker(worker=f"gate-{int(slow_read)}",
                                      max_jobs=None)
    finally:
        await mount.stop()
    return 0


def main() -> int:
    return asyncio.run(amain())


if __name__ == "__main__":
    raise SystemExit(main())
