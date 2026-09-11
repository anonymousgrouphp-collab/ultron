"""kernel/orchestrator/runner.py — P2-C: the orchestrator (worker + execution).

The Orchestrator is the bridge between the durable queue and the kernel:

    claim job → execute plan steps → checkpoint after EVERY step → complete

- Step kinds are pluggable. Built-ins:
    "tool"  — run a registry tool through the PolicyEngine (same choke point
              as everything else), args template-resolved from prior outputs.
    "agent" — run a P1-G AgentLoop as a subagent (own instruction, optional
              tool allowlist = scoped registry, own step budget); needs a
              gateway on the Orchestrator.
- Checkpointing: outputs (JSON-able) + done-step ids are persisted after every
  step, so a killed worker's job resumes exactly where it stopped — this is
  the roadmap's "survives a kernel restart mid-task".
- Liveness: a heartbeat task renews the claim lease while the job runs; a
  killed worker stops renewing and the job becomes claimable again.
- Events on the optional bus: job.started / job.step / job.completed /
  job.failed / job.canceled (source="orchestrator").
- Error semantics: StepFailure (expected failures — tool fail results, policy
  denials) fails the job WITHOUT retry; unexpected crashes retry up to
  max_attempts (the checkpoint keeps completed steps either way).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from kernel.bus import EventBus
from kernel.gateway import Message
from kernel.jsonable import jsonable
from kernel.loop import AgentLoop
from kernel.orchestrator.plans import Step, resolve_template, steps_from_payload
from kernel.orchestrator.queue import Job, JobQueue
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import Event, ToolCall

log = logging.getLogger(__name__)

__all__ = ["ExecContext", "Orchestrator", "StepFailure", "StepHandler"]


class StepFailure(Exception):
    """An EXPECTED step failure (clean message) — the job fails, no retry."""


StepHandler = Callable[[Step, dict[str, Any], "ExecContext"], Awaitable[Any]]


@dataclass(frozen=True)
class ExecContext:
    job_id: str
    worker: str
    attempt: int


class Orchestrator:
    """Claim-and-execute worker over a durable JobQueue."""

    def __init__(
        self,
        queue: JobQueue,
        registry: ToolRegistry,
        policy: PolicyEngine,
        *,
        bus: EventBus | None = None,
        gateway: Any | None = None,          # kernel Completer (agent steps)
        consent: Any | None = None,          # ConsentCallback passthrough
        source: str = "orchestrator",
        poll_s: float = 0.5,
        lease_s: float = 30.0,
    ) -> None:
        if poll_s <= 0 or lease_s <= 0:
            raise ValueError("poll_s and lease_s must be > 0")
        self._queue = queue
        self._registry = registry
        self._policy = policy
        self._bus = bus
        self._gateway = gateway
        self._consent = consent
        self._source = source
        self._poll_s = poll_s
        self._lease_s = lease_s
        self._kinds: dict[str, StepHandler] = {}
        self._kinds["tool"] = self._run_tool_step
        self._kinds["agent"] = self._run_agent_step

    def enqueue(self, steps: "Mapping[str, Any] | Sequence[Mapping[str, Any]]",
                *, title: str = "", kind: str = "plan",
                priority: int = 0, max_attempts: int = 3,
                registry: Any = None) -> str:
        """Plan-level enqueue (Phase W1): the convenience the app layer needs.

        Wraps `JobQueue.enqueue(kind, payload)` with the exact payload shape
        `steps_from_payload` parses on the worker side:
        `{"plan": [{"id", "kind", "spec"}...]}`. Each accepted step is
        normalized to that canonical form:

        - canonical `{"id", "kind", "spec"}` dicts (as `research_report_plan`
          builds) pass through untouched;
        - shorthand `{"kind", "path", "args"}` dicts (as
          `GUIPlanner._build_plan` builds) gain an auto-assigned `step-N` id
          and `spec = {"name": path, "args": args}` — the `_run_tool_step`
          contract;
        - a mapping with a "plan" key is taken as the step sequence itself.

        `registry` is accepted and IGNORED: the worker always runs against
        the orchestrator's own registry (the single choke point — callers
        can't swap in an ungated one)."""
        if isinstance(steps, Mapping):
            steps_seq = steps.get("plan")
        else:
            steps_seq = steps
        if not steps_seq:
            raise ValueError("enqueue needs at least one step")
        plan: list[dict[str, Any]] = []
        for i, raw in enumerate(steps_seq, start=1):
            step = dict(raw)
            if "id" in step and "kind" in step:
                step.setdefault("spec", {})
            elif "kind" in step and step.get("path"):
                step = {"id": f"step-{i}", "kind": step["kind"],
                        "spec": {"name": str(step["path"]),
                                 "args": dict(step.get("args") or {})}}
            else:
                raise ValueError(
                    f"step {i} needs 'id'+'kind' or 'kind'+'path', "
                    f"got keys {sorted(step)}")
            plan.append(step)
        payload = {"plan": plan}
        return self._queue.enqueue(
            kind, payload, title=title, priority=priority,
            max_attempts=max_attempts,
        )

    def get_job(self, job_id: str) -> Job | None:
        """Queue lookup passthrough (app layers ask for status by job id)."""
        return self._queue.get(job_id)

    # -- step kinds --------------------------------------------------------

    def register_kind(self, kind: str, handler: StepHandler) -> None:
        """Add a custom step kind (e.g. "briefing"). Overrides built-ins."""
        if not kind:
            raise ValueError("step kind is required")
        self._kinds[kind] = handler

    async def _run_tool_step(self, step: Step, outputs: dict[str, Any],
                             ctx: ExecContext) -> Any:
        spec = dict(step.spec)
        name = str(spec.get("name") or "")
        if not name:
            raise StepFailure("tool step needs a 'name' in its spec")
        try:
            args = resolve_template(dict(spec.get("args") or {}), outputs)
        except ValueError as exc:
            raise StepFailure(str(exc)) from None
        if not isinstance(args, Mapping):
            raise StepFailure("resolved tool args must be a mapping")
        call = ToolCall(id=f"job-{ctx.job_id[:12]}-{step.id}-{ctx.attempt}",
                        name=name, args=dict(args), source=self._source)
        result = await self._policy.run(call, self._registry, bus=self._bus,
                                        consent=self._consent)
        if not result.ok:
            raise StepFailure(result.error or "tool failed")
        return jsonable(result.data)

    async def _run_agent_step(self, step: Step, outputs: dict[str, Any],
                              ctx: ExecContext) -> Any:
        if self._gateway is None:
            raise StepFailure("agent steps need a model gateway on the orchestrator")
        spec = dict(step.spec)
        instruction = str(spec.get("instruction") or "").strip()
        if not instruction:
            raise StepFailure("agent step needs an 'instruction' in its spec")
        allowed = spec.get("tools")
        registry = self._registry
        if allowed is not None:
            from kernel.orchestrator.subagent import scoped_registry
            registry = scoped_registry(self._registry, list(allowed))
        loop = AgentLoop(
            self._gateway, self._policy, registry, bus=self._bus,
            max_steps=max(1, int(spec.get("max_steps", 6))),
            consent=self._consent, source=f"{self._source}:agent",
        )
        result = await loop.run([Message(role="user", text=instruction)])
        return {
            "finish": result.finish,
            "text": result.text,
            "steps": result.steps,
            "tool_calls": [jsonable({"name": c.name, "args": dict(c.args),
                                     "source": c.source})
                           for c in result.tool_calls],
            "trace": [{"step": t.step, "kind": t.kind, "detail": jsonable(t.detail)}
                      for t in result.trace],
        }

    # -- worker ------------------------------------------------------------

    async def run_worker(self, *, worker: str | None = None,
                         max_jobs: int | None = 1) -> int:
        """Claim and run jobs. max_jobs=None runs until canceled (polling when
        the queue is empty); an int bounds the run (0 processed when the queue
        is empty). Returns the number of jobs processed."""
        if max_jobs is not None and max_jobs < 0:
            raise ValueError("max_jobs must be None or >= 0")
        worker = worker or f"w-{uuid.uuid4().hex[:8]}"
        processed = 0
        while max_jobs is None or processed < max_jobs:
            job = self._queue.claim(worker, self._lease_s)
            if job is None:
                if max_jobs is None:
                    await asyncio.sleep(self._poll_s)
                    continue
                return processed
            processed += 1
            await self._run_job(job, worker)
        return processed

    async def _run_job(self, job: Job, worker: str) -> None:
        ctx = ExecContext(job_id=job.id, worker=worker, attempt=job.attempts)
        await self._publish("job.started",
                            {"job": job.id, "kind": job.kind,
                             "title": job.title, "attempt": job.attempts}, job.id)
        beat = asyncio.create_task(self._heartbeat(job.id, worker))
        try:
            steps = steps_from_payload(job.payload)
            outputs = job.checkpoint_outputs()
            done = job.done_steps()
            for step in steps:
                if step.id in done:
                    continue
                current = self._queue.get(job.id)
                if current is None or current.status == "canceled":
                    await self._publish("job.canceled", {"job": job.id}, job.id)
                    return
                await self._publish("job.step",
                                    {"job": job.id, "step": step.id,
                                     "kind": step.kind}, job.id)
                handler = self._kinds.get(step.kind)
                if handler is None:
                    raise StepFailure(
                        f"no step-kind handler registered for {step.kind!r}")
                raw = await handler(step, outputs, ctx)
                outputs[step.id] = jsonable(raw)
                done.add(step.id)
                if not self._queue.checkpoint(
                        job.id, {"done": sorted(done), "outputs": outputs},
                        worker):
                    await self._publish("job.canceled", {"job": job.id}, job.id)
                    return
            self._queue.complete(job.id, {"outputs": outputs}, worker)
            await self._publish("job.completed",
                                {"job": job.id, "outputs": outputs}, job.id)
        except StepFailure as failure:
            self._queue.fail(job.id, str(failure), worker=worker, retry=False)
            await self._publish("job.failed",
                                {"job": job.id, "error": str(failure),
                                 "retry": False}, job.id)
        except asyncio.CancelledError:
            self._queue.fail(job.id, "worker canceled", worker=worker, retry=True)
            raise
        except Exception as exc:  # noqa: BLE001 — crashes retry via the queue
            log.exception("job %s crashed", job.id)
            message = f"{type(exc).__name__}: {exc}"[:300]
            retried = self._queue.fail(job.id, message, worker=worker, retry=True)
            await self._publish("job.failed",
                                {"job": job.id, "error": message,
                                 "retry": retried}, job.id)
        finally:
            beat.cancel()

    async def _heartbeat(self, job_id: str, worker: str) -> None:
        interval = self._lease_s / 3.0
        while True:
            await asyncio.sleep(interval)
            if not self._queue.heartbeat(job_id, worker, self._lease_s):
                return  # lost ownership (canceled) — stop renewing

    async def _publish(self, event_type: str, payload: Mapping[str, Any],
                       job_id: str) -> None:
        if self._bus is None:
            return
        await self._bus.publish(Event(type=event_type, payload=dict(payload),
                                      source=self._source))
