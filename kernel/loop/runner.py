"""kernel/loop/runner.py — Phase O2: agent runner for multi-step voice tasks.

Bridges the voice session (which uses LegacyToolRuntime for single-turn tool
calls) to the kernel's AgentLoop for multi-step complex tasks.  The runner
builds its own Gateway from config and reuses the same ToolRegistry,
PolicyEngine, EventBus, and ConsentCallback as the voice session.

Usage::

    runner = AgentRunner.build(
        tool_runtime=ultron._tool_runtime,
        bus=ultron._bus,
        consent=consent_callback,
    )
    result = await runner.run_task("Research AI papers and save a report")
    if result.text:
        ultron.speak(result.text)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kernel.bus import EventBus
from kernel.gateway import GatewaySettings, Message, build_gateway
from kernel.loop.loop import AgentLoop, LoopResult
from kernel.policy import ConsentCallback, PolicyEngine
from kernel.tools import ToolRegistry

__all__ = ["AgentRunner", "TaskResult"]


@dataclass(frozen=True)
class TaskResult:
    """Simplified result for the voice session."""

    text: str
    steps: int
    finish: str  # stop | max_steps | aborted | error
    tool_names: tuple[str, ...] = ()
    error: str | None = None


class AgentRunner:
    """Runs multi-step tasks through the kernel AgentLoop.

    Created via ``AgentRunner.build()`` which extracts the ToolRegistry
    and PolicyEngine from the existing LegacyToolRuntime and builds a
    Gateway from the current config.
    """

    def __init__(
        self,
        gateway: Any,
        policy: PolicyEngine,
        registry: ToolRegistry,
        bus: EventBus | None = None,
        consent: ConsentCallback | None = None,
        max_steps: int = 8,
    ) -> None:
        self._loop = AgentLoop(
            gateway=gateway,
            policy=policy,
            registry=registry,
            bus=bus,
            consent=consent,
            max_steps=max_steps,
            source="voice-runner",
        )

    @classmethod
    def build(
        cls,
        *,
        tool_runtime: Any,
        bus: EventBus | None = None,
        consent: ConsentCallback | None = None,
        max_steps: int = 8,
    ) -> "AgentRunner":
        """Build an AgentRunner from the voice session's existing components.

        Extracts the ToolRegistry and PolicyEngine from the
        LegacyToolRuntime and builds a Gateway from config.
        """
        from config import loader

        cfg = loader.load_config()
        settings = GatewaySettings.from_config(cfg)

        # Extract registry and policy from the legacy runtime
        registry: ToolRegistry = tool_runtime._registry
        policy: PolicyEngine = tool_runtime._policy

        # Build gateway from config
        api_key = loader.get_api_key()
        gateway = build_gateway(settings, api_key=api_key)

        return cls(
            gateway=gateway,
            policy=policy,
            registry=registry,
            bus=bus,
            consent=consent,
            max_steps=max_steps,
        )

    async def run_task(
        self,
        user_message: str,
        *,
        extra_context: str = "",
    ) -> TaskResult:
        """Run a multi-step task through the AgentLoop.

        Parameters
        ----------
        user_message:
            The user's request (e.g. from voice transcription).
        extra_context:
            Optional additional context to prepend (e.g. memory highlights).
        """
        parts = []
        if extra_context:
            parts.append(extra_context)
        parts.append(user_message)

        messages = [Message(role="user", text="\n".join(parts))]

        try:
            result: LoopResult = await self._loop.run(messages)
        except Exception as exc:
            return TaskResult(
                text=f"Task failed: {exc}",
                steps=0,
                finish="error",
                error=str(exc),
            )

        return TaskResult(
            text=result.text,
            steps=result.steps,
            finish=result.finish,
            tool_names=tuple(c.name for c in result.tool_calls),
            error=None if result.finish == "stop" else f"finish={result.finish}",
        )
