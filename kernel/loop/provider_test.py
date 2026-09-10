"""kernel/loop/provider_test.py — Phase I4: multi-provider live test wiring.

Runs the AgentLoop on different gateway providers (Gemini, Ollama, OpenAI)
and compares results.  This is the infrastructure for provider comparison
and model-specific failure identification.

Usage::

    from kernel.loop.provider_test import ProviderTestRunner
    runner = ProviderTestRunner()
    results = await runner.run_task("Create a note called 'test' with content 'hello'")
    for provider, result in results.items():
        print(f"{provider}: {result.finish} in {result.steps} steps")
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

from kernel.bus import EventBus
from kernel.gateway import GatewaySettings, Message, Provider, build_gateway
from kernel.loop.loop import AgentLoop, LoopResult
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry

__all__ = ["ProviderTestRunner", "ProviderResult"]


@dataclass(frozen=True)
class ProviderResult:
    """Result from running a task on one provider."""

    provider: str
    model: str
    text: str
    steps: int
    finish: str
    duration_s: float
    error: str | None = None
    tool_names: tuple[str, ...] = ()


@dataclass
class ProviderTestRunner:
    """Runs tasks across multiple providers for comparison.

    Parameters
    ----------
    policy:
        The PolicyEngine to use for tool execution.
    registry:
        The ToolRegistry with available tools.
    bus:
        Optional EventBus for tool events.
    consent:
        Optional consent callback (auto-allows for testing).
    """

    policy: PolicyEngine | None = None
    registry: ToolRegistry | None = None
    bus: EventBus | None = None
    consent: Any = None

    async def run_task(
        self,
        task: str,
        *,
        providers: list[Provider] | None = None,
        max_steps: int = 8,
    ) -> dict[str, ProviderResult]:
        """Run the same task on multiple providers and compare.

        Parameters
        ----------
        task:
            The task prompt.
        providers:
            Which providers to test (default: all configured ones).
        max_steps:
            Maximum steps per provider.
        """
        if providers is None:
            providers = [Provider.GEMINI, Provider.OLLAMA, Provider.OPENAI]

        from config import loader
        api_key = loader.get_api_key()

        results: dict[str, ProviderResult] = {}
        messages = [Message(role="user", text=task)]

        for provider in providers:
            try:
                settings = GatewaySettings(provider=provider)
                gateway = build_gateway(settings, api_key=api_key)

                loop = AgentLoop(
                    gateway=gateway,
                    policy=self.policy,  # type: ignore[arg-type]
                    registry=self.registry,  # type: ignore[arg-type]
                    bus=self.bus,
                    consent=self.consent,
                    max_steps=max_steps,
                    source=f"provider-test-{provider.value}",
                )

                start = time.monotonic()
                result: LoopResult = await loop.run(messages)
                duration = time.monotonic() - start

                results[provider.value] = ProviderResult(
                    provider=provider.value,
                    model=gateway.model,
                    text=result.text,
                    steps=result.steps,
                    finish=result.finish,
                    duration_s=round(duration, 2),
                    tool_names=tuple(c.name for c in result.tool_calls),
                )
            except Exception as exc:
                results[provider.value] = ProviderResult(
                    provider=provider.value,
                    model="unknown",
                    text="",
                    steps=0,
                    finish="error",
                    duration_s=0,
                    error=str(exc),
                )

        return results

    def compare(self, results: dict[str, ProviderResult]) -> str:
        """Generate a human-readable comparison report."""
        lines = ["Provider Comparison Report", "=" * 40]
        for name, r in sorted(results.items()):
            status = "✓" if r.finish == "stop" else f"✗ ({r.finish})"
            lines.append(
                f"\n{name} ({r.model}): {status}"
                f"\n  Steps: {r.steps}, Duration: {r.duration_s}s"
                f"\n  Tools: {', '.join(r.tool_names) or 'none'}"
                f"\n  Response: {r.text[:100]}{'...' if len(r.text) > 100 else ''}"
            )
            if r.error:
                lines.append(f"  Error: {r.error}")
        return "\n".join(lines)
