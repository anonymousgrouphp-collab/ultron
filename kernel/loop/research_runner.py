"""kernel/loop/research_runner.py — Phase I2: research subagent live via voice.

Wraps the existing P2-D research tools and P2-C orchestrator to run
research tasks triggered by voice commands.  The user says something like
"Research the latest AI papers and save a summary" and the runner:
1. Builds a research plan via `research_report_plan`
2. Enqueues it as an orchestrator job
3. Returns the result for the voice session to speak

This connects existing kernel components (research + orchestrator) into
the live voice product without building new kernel code.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ResearchRunner"]


@dataclass
class ResearchRunner:
    """Runs research tasks through the orchestrator.

    Parameters
    ----------
    orchestrator:
        The Orchestrator instance from the voice session.
    registry:
        The ToolRegistry with research tools.
    """

    orchestrator: Any = None
    registry: Any = None

    async def run_research(
        self,
        topic: str,
        *,
        report_path: str | None = None,
    ) -> str:
        """Run a research task and return the summary.

        Parameters
        ----------
        topic:
            The research topic.
        report_path:
            Optional path to save the report (default: Desktop).
        """
        if self.orchestrator is None or self.registry is None:
            return "Research subsystem not available, sir."

        # Build the research plan
        from kernel.research import research_report_plan

        try:
            # Phase W gate: use the live registry's URL-returning search
            # (the legacy web_search speaks prose; the plan's
            # {search.first_url} template needs a structured result).
            kw: dict[str, Any] = {"search_tool": "web_search_url"}
            if report_path:
                kw["report_name"] = report_path
            plan = research_report_plan(topic, **kw)
        except Exception as exc:
            return f"Could not build research plan: {exc}"

        if not plan:
            return "No research plan generated for that topic, sir."

        # Enqueue as an orchestrator job (Phase W1: Orchestrator.enqueue is
        # the plan-level sync helper — no await)
        try:
            job_id = self.orchestrator.enqueue(
                title=f"research: {topic}",
                steps=plan,
                registry=self.registry,
            )
            return (
                f"Research task started, sir. I'm investigating '{topic}' "
                f"in the background. Job ID: {job_id}. "
                f"I'll have a report ready shortly."
            )
        except Exception as exc:
            return f"Failed to start research task: {exc}"

    async def get_status(self, job_id: str) -> str:
        """Check the status of a research job."""
        if self.orchestrator is None:
            return "Research subsystem not available."
        try:
            job = self.orchestrator.get_job(job_id)  # W1: sync lookup
            if job is None:
                return f"Job {job_id} not found."
            done = len(job.done_steps())
            total = len((job.payload or {}).get("plan") or [])
            return (
                f"Research job {job_id}: {job.status}. "
                f"Steps completed: {done}/{total}."
            )
        except Exception as exc:
            return f"Error checking job status: {exc}"
