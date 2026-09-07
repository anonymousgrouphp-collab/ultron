"""kernel/orchestrator/subagent.py — P2-C: scoped subagents as queue jobs.

The 2026-proven pattern (research/05 §5): a parent spawns a child with its own
instruction, a RESTRICTED tool subset (scoped_registry — the policy engine
still gates every call), its own step budget, and a structured result. Jobs
make the fan-out queue-backed ("house party protocol" = N queued jobs, never a
blocked voice loop); progress flows to the HUD as job.* bus events.
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence

from kernel.orchestrator.plans import Step
from kernel.orchestrator.queue import Job, JobQueue
from kernel.tools import ToolRegistry

__all__ = ["scoped_registry", "spawn_subagent", "wait_for_job"]


def scoped_registry(registry: ToolRegistry, allowed: Sequence[str]) -> ToolRegistry:
    """Restricted copy: only the allowlisted tools exist for this subagent.
    The registry (and its risk map) is what declarations()/policy see — a
    scoped subagent literally cannot name a tool it wasn't given."""
    scoped = ToolRegistry()
    for name in allowed:
        tool = registry.get(name)
        if tool is None:
            raise ValueError(f"unknown tool {name!r} in subagent allowlist")
        scoped.register(tool)
    return scoped


def spawn_subagent(
    queue: JobQueue,
    *,
    instruction: str,
    tools: Sequence[str] | None = None,
    title: str = "",
    max_steps: int = 6,
    priority: int = 0,
    max_attempts: int = 3,
    job_id: str | None = None,
) -> str:
    """Enqueue a single agent step as a durable job; returns the job id.
    tools=None means the subagent sees the orchestrator's full registry.
    Sync by design — enqueueing is a local SQLite write; the JOB is what
    runs asynchronously."""
    if not instruction.strip():
        raise ValueError("subagent instruction is required")
    spec: dict = {"instruction": instruction, "max_steps": max(1, max_steps)}
    if tools is not None:
        spec["tools"] = list(tools)
    step = Step(id="agent", kind="agent", spec=spec)
    return queue.enqueue(
        "plan", {"plan": [{"id": step.id, "kind": step.kind,
                           "spec": dict(step.spec)}]},
        title=title or instruction[:60], priority=priority,
        max_attempts=max_attempts, job_id=job_id)


async def wait_for_job(queue: JobQueue, job_id: str, *,
                       poll_s: float = 0.2, timeout_s: float = 120.0) -> Job:
    """Poll until a job reaches a terminal state; clean TimeoutError otherwise."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout_s
    while loop.time() < deadline:
        job = queue.get(job_id)
        if job is not None and job.terminal:
            return job
        await asyncio.sleep(poll_s)
    raise TimeoutError(f"job {job_id} did not finish within {timeout_s:g}s")
