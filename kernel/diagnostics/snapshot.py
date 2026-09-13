"""kernel/diagnostics/snapshot.py — research/12 D8: the one-call state snapshot.

ada_local's `get_system_info` insight: "what's on my schedule / what's my
status?" questions need ONE tool that composes every store the assistant
keeps, instead of the model chaining N read tools (slow on voice, more
consent prompts, more tokens). ULTRON's analogue composes the orchestrator
queue, the memory engine, and the active user. Hardware metrics stay in the
legacy `system_status` tool — this snapshot is ULTRON state, not the PC's.

Every section degrades independently: a broken store yields "(unavailable)",
never an exception (same rule as kernel/briefing).
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

__all__ = ["build_snapshot_tools"]

_JOB_STATUSES = ("queued", "running", "failed", "done")


def build_snapshot_tools(registry, *, job_queue=None, memory=None,
                         users=None) -> None:
    """Register the `system_snapshot` tool. Every dependency is optional —
    omitted stores are omitted from the snapshot (not even "(unavailable)")."""

    @registry.tool(
        name="system_snapshot",
        description="One-call snapshot of ULTRON's own state: current time, "
                    "active user, orchestrator job counts (queued/running/"
                    "failed/done), memory fact count. Use for 'what's my "
                    "status', 'what's running', 'what do you remember' style "
                    "questions. Hardware metrics live in system_status.",
        parameters={"type": "object", "properties": {}},
        risk=RiskClass.READ,
    )
    def system_snapshot(call: ToolCall) -> ToolResult | dict[str, Any]:
        data: dict[str, Any] = {
            "time": datetime.now().isoformat(timespec="seconds"),
        }

        if users is not None:
            try:
                user = users.get_current_user()
                data["user"] = getattr(user, "display_name", None) or "user"
            except Exception:  # noqa: BLE001 — one dead store can't kill the snapshot
                data["user"] = "(unavailable)"

        if job_queue is not None:
            jobs: dict[str, Any] = {}
            for status in _JOB_STATUSES:
                try:
                    jobs[status] = len(job_queue.list(status=status, limit=200))
                except Exception:  # noqa: BLE001
                    jobs[status] = "(unavailable)"
            data["jobs"] = jobs

        if memory is not None:
            try:
                data["memory_facts"] = memory.count()
            except Exception:  # noqa: BLE001
                data["memory_facts"] = "(unavailable)"

        return data
