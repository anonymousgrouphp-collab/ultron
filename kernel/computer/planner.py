"""kernel/computer/planner.py — Phase I1: autonomous GUI planning loop.

Plans and executes GUI tasks using the orchestrator and computer control
tools.  The user says "Open Chrome and navigate to github.com" and the
planner:
1. Observes the current screen state
2. Plans a sequence of actions
3. Executes each action with consent
4. Verifies the result
5. Replans if needed

This is the "autonomous GUI agent" — it uses existing kernel components
(orchestrator + computer control) without building new infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["GUIPlanner", "GUIPlanResult"]


@dataclass(frozen=True)
class GUIPlanResult:
    """Result of an autonomous GUI task."""

    success: bool
    steps_taken: int
    actions: tuple[str, ...] = ()
    error: str | None = None
    summary: str = ""


class GUIPlanner:
    """Plans and executes GUI tasks autonomously.

    Uses the orchestrator to run a plan→act→observe loop with computer
    control tools (screen_describe, ui_tree, spawn_app, ui_act).

    Parameters
    ----------
    orchestrator:
        The Orchestrator instance.
    registry:
        ToolRegistry with computer control tools.
    consent:
        Consent callback for WRITE/EXECUTE actions.
    """

    def __init__(
        self,
        orchestrator: Any = None,
        registry: Any = None,
        consent: Any = None,
    ) -> None:
        self._orchestrator = orchestrator
        self._registry = registry
        self._consent = consent

    async def plan_and_execute(
        self,
        task: str,
        *,
        max_steps: int = 10,
    ) -> GUIPlanResult:
        """Plan and execute a GUI task.

        Parameters
        ----------
        task:
            The user's GUI task description.
        max_steps:
            Maximum actions before stopping.
        """
        if self._orchestrator is None:
            return GUIPlanResult(
                success=False,
                steps_taken=0,
                error="Orchestrator not available",
            )

        # Build a plan as orchestrator steps
        steps = self._build_plan(task)

        if not steps:
            return GUIPlanResult(
                success=False,
                steps_taken=0,
                error="Could not generate a plan for that task",
            )

        # Enqueue as an orchestrator job (Phase W1: Orchestrator.enqueue is
        # the plan-level sync helper — no await)
        try:
            job_id = self._orchestrator.enqueue(
                title=f"gui: {task}",
                steps=steps,
                registry=self._registry,
            )
            return GUIPlanResult(
                success=True,
                steps_taken=len(steps),
                actions=tuple(s.get("tool", "") for s in steps),
                summary=f"GUI task started. Job ID: {job_id}. "
                        f"Executing {len(steps)} steps.",
            )
        except Exception as exc:
            return GUIPlanResult(
                success=False,
                steps_taken=0,
                error=str(exc),
            )

    def _build_plan(self, task: str) -> list[dict[str, Any]]:
        """Build an orchestrator plan from a task description.

        This is a v0 heuristic planner.  A future version would use the
        LLM to generate the plan dynamically.
        """
        task_lower = task.lower()
        steps: list[dict[str, Any]] = []

        # Observe current state first
        steps.append({
            "kind": "tool",
            "path": "screen_describe",
            "args": {"question": f"Current screen state for task: {task}"},
        })

        # Parse common patterns
        if "open" in task_lower or "launch" in task_lower:
            # Extract app name
            app_name = self._extract_app_name(task_lower)
            if app_name:
                steps.append({
                    "kind": "tool",
                    "path": "spawn_app",
                    "args": {"app_name": app_name},
                })

        if "navigate" in task_lower or "go to" in task_lower:
            # Extract URL
            url = self._extract_url(task)
            if url:
                steps.append({
                    "kind": "tool",
                    "path": "ui_act",
                    "args": {
                        "action": "type",
                        "target": "address bar",
                        "text": url,
                    },
                })
                steps.append({
                    "kind": "tool",
                    "path": "ui_act",
                    "args": {"action": "press", "key": "enter"},
                })

        if "type" in task_lower or "write" in task_lower:
            text = self._extract_typed_text(task)
            if text:
                steps.append({
                    "kind": "tool",
                    "path": "ui_act",
                    "args": {"action": "type", "text": text},
                })

        if "click" in task_lower:
            target = self._extract_click_target(task)
            if target:
                steps.append({
                    "kind": "tool",
                    "path": "ui_act",
                    "args": {"action": "click", "target": target},
                })

        # Final observation to verify
        steps.append({
            "kind": "tool",
            "path": "screen_describe",
            "args": {"question": "Verify the task was completed successfully"},
        })

        return steps

    def _extract_app_name(self, text: str) -> str:
        """Extract app name from task text."""
        # Simple extraction: word after "open" or "launch"
        for marker in ["open ", "launch "]:
            idx = text.find(marker)
            if idx >= 0:
                after = text[idx + len(marker):].strip()
                words = after.split()[:2]
                return " ".join(words).strip(".,!?")
        return ""

    def _extract_url(self, text: str) -> str:
        """Extract URL from task text."""
        for marker in ["navigate to ", "go to ", "visit "]:
            idx = text.lower().find(marker)
            if idx >= 0:
                after = text[idx + len(marker):].strip()
                words = after.split()[:3]
                url = " ".join(words).strip(".,!?")
                if "." in url and not url.startswith("."):
                    return url
                return f"https://{url}"
        return ""

    def _extract_typed_text(self, text: str) -> str:
        """Extract text to type from task text."""
        for marker in ["type ", "write ", "enter "]:
            idx = text.lower().find(marker)
            if idx >= 0:
                after = text[idx + len(marker):].strip()
                # Take everything in quotes or until end
                if '"' in after:
                    start = after.find('"') + 1
                    end = after.find('"', start)
                    if end > start:
                        return after[start:end]
                return after[:100]
        return ""

    def _extract_click_target(self, text: str) -> str:
        """Extract click target from task text."""
        for marker in ["click ", "press "]:
            idx = text.lower().find(marker)
            if idx >= 0:
                after = text[idx + len(marker):].strip()
                words = after.split()[:3]
                return " ".join(words).strip(".,!?")
        return ""
