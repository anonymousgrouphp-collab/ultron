"""kernel/memory/improvement_service.py — Phase Q2: procedural memory improvement live.

Wraps the existing P5-B `kernel/memory/improve.py` to make the
self-improvement loop usable from the live voice session.

The user can say "improve your performance on web tasks" and the
service will:
1. Recall relevant skills from procedural memory
2. Replay them to verify they still work
3. Attempt the task fresh if no skill exists
4. Capture successful approaches as new skills
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

__all__ = ["ImprovementService"]


@dataclass
class ImprovementService:
    """Makes the self-improvement loop usable from the live session.

    Parameters
    ----------
    memory:
        The MemoryEngine instance.
    loop_factory:
        A callable that creates an AgentLoop for replay.
    """

    memory: Any = None
    loop_factory: Any = None

    async def improve_task(self, task: str) -> str:
        """Run the improvement loop for a specific task.

        Returns a human-readable summary of what happened.
        """
        if self.memory is None:
            return "Memory engine not available, sir."

        try:
            from kernel.memory.improve import improve_run

            if self.loop_factory is None:
                return "Agent loop not available for improvement, sir."

            loop = self.loop_factory()
            result = await improve_run(
                task=task,
                memory=self.memory,
                loop=loop,
            )

            if result.get("success"):
                return (
                    f"Improvement complete for '{task}'. "
                    f"Approach captured as a skill. "
                    f"Steps: {result.get('steps', 0)}."
                )
            else:
                return (
                    f"Could not improve '{task}'. "
                    f"Error: {result.get('error', 'unknown')}."
                )
        except Exception as exc:
            return f"Improvement failed: {exc}"

    async def list_skills(self) -> str:
        """List available procedural skills."""
        if self.memory is None:
            return "Memory engine not available, sir."

        try:
            procedures = self.memory.search_procedures("", k=20)
            if not procedures:
                return "No skills learned yet, sir."

            lines = [f"Learned skills ({len(procedures)}):"]
            for p in procedures[:10]:
                name = getattr(p, "name", "unknown")
                summary = getattr(p, "summary", "")
                lines.append(f"  - {name}: {summary[:60]}")
            return "\n".join(lines)
        except Exception as exc:
            return f"Error listing skills: {exc}"
