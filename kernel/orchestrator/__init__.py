"""kernel/orchestrator — P2-C: durable queue + orchestrator + subagents.

JobQueue  — SQLite WAL durable queue (claim/lease/heartbeat/checkpoint/cancel).
Orchestrator — claim-and-execute worker: plan steps (tool/agent/custom kinds),
  checkpoint after every step, crash recovery via lease expiry, job.* events.
Step/StepFailure — plan vocabulary + expected-failure signal.
scoped_registry / spawn_subagent / wait_for_job — subagent fan-out.
"""

from kernel.orchestrator.plans import Step, resolve_template, steps_from_payload
from kernel.orchestrator.queue import Job, JobQueue
from kernel.orchestrator.runner import ExecContext, Orchestrator, StepFailure
from kernel.orchestrator.subagent import (
    scoped_registry,
    spawn_subagent,
    wait_for_job,
)

__all__ = [
    "ExecContext",
    "Job",
    "JobQueue",
    "Orchestrator",
    "Step",
    "StepFailure",
    "resolve_template",
    "scoped_registry",
    "spawn_subagent",
    "steps_from_payload",
    "wait_for_job",
]
