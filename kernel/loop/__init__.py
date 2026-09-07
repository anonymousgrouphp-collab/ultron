"""kernel/loop — P1-G: the plan→act→observe agent loop.

Drives a model (P1-C gateway) through tool cycles gated by policy+audit
(P1-E/B). v0 carries max-steps, clean abort, error capture, and a replayable
trace; consolidation of memory/eval integration builds on this.
"""

from kernel.loop.loop import AgentLoop, LoopResult, TraceStep

__all__ = ["AgentLoop", "LoopResult", "TraceStep"]
