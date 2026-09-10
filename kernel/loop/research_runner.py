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
            plan = research_report_plan(topic)
        except Exception as exc:
            return f"Could not build research plan: {exc}"

        if not plan:
            return "No research plan generated for that topic, sir."

        # Enqueue as an orchestrator job
        try:
            job_id = await self.orchestrator.enqueue(
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
            job = await self.orchestrator.get_job(job_id)
            if job is None:
                return f"Job {job_id} not found."
            return (
                f"Research job {job_id}: {job.status.value}. "
                f"Steps completed: {len(job.completed_steps)}/{len(job.steps)}."
            )
        except Exception as exc:
            return f"Error checking job status: {exc}"
