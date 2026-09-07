"""kernel/coding — P2-E: the coding subagent toolkit (J-11).

Replaces the one-shot legacy dev_agent with kernel primitives:
`build_coding_tools` jails a workspace and registers list_files / read_file /
write_file / run_command; `spawn_coding_job` runs the J-11 loop
(plan → write → run → observe → fix) as an orchestrator subagent driven by the
P1-G AgentLoop — no ambient pip-install, no shell=True, capped outputs, and
the research/03 §7 sandbox ladder's DEFAULT rung: a Windows Job Object
(memory cap + kill-on-close) around every spawned process.

Honesty note (per research/03 §7): a Job Object enforces LIMITS, it is not a
security boundary vs hostile code. Code the user did not consent to runs on a
stronger rung (Windows Sandbox / WSL2 container) — out of v0 scope, documented
residual risk.
"""

from kernel.coding.sandbox import JOB_OBJECT_AVAILABLE, run_sandboxed
from kernel.coding.tools import CODING_TOOLS, build_coding_tools, spawn_coding_job

__all__ = [
    "CODING_TOOLS",
    "JOB_OBJECT_AVAILABLE",
    "build_coding_tools",
    "run_sandboxed",
    "spawn_coding_job",
]
