"""kernel/webagent/tools.py — research/12 D1: the `run_web_agent` tool.

Registration follows the research-gate pattern (kernel/research/web.py):
`enabled` is a fixed bool for tests, `consent()` is the wiring layer's
config-backed callable — a broken gate DENIES. The gateway factory is
injectable so the app layer decides how a key is sourced (and so tests
inject fakes); a factory that returns None produces a clean fail
("needs a Gemini key"), never an exception.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from kernel.types import RiskClass, ToolCall, ToolResult
from kernel.webagent.agent import WebAgent

log = logging.getLogger(__name__)

__all__ = ["build_web_agent_tools"]


def build_web_agent_tools(
    registry,
    *,
    gateway_factory: Callable[[], Any],
    enabled: bool | None = None,
    consent: Callable[[], bool] | None = None,
    viewport_w: int = 1440,
    viewport_h: int = 900,
    max_turns: int = 20,
    timeout_s: float = 600.0,
    browser_factory=None,
) -> None:
    """Register `run_web_agent`. gateway_factory() returns a computer-use
    gateway (kernel.gateway.cu.ComputerUseGateway) or None when no API key
    is configured."""

    def allowed() -> bool:
        if enabled is not None:
            return bool(enabled)
        if consent is not None:
            try:
                return bool(consent())
            except Exception:  # noqa: BLE001 — a broken gate must deny
                log.exception("web-agent consent gate failed — denying")
                return False
        return False

    @registry.tool(
        name="run_web_agent",
        description="Drive a real web browser to complete a task: navigate "
                    "sites, click, type, scroll, and read the result. Use "
                    "for multi-step web tasks a single search cannot answer "
                    "(e.g. 'find a USB-C cable under $10 on Amazon'). Runs "
                    "in its own headless browser window; takes up to a few "
                    "minutes.",
        parameters={"type": "object",
                    "properties": {"prompt": {"type": "string",
                                              "description": "the complete "
                                                             "task for the "
                                                             "browser agent"}},
                    "required": ["prompt"]},
        risk=RiskClass.WRITE,
        timeout_s=timeout_s,
        max_retries=0,
    )
    async def run_web_agent(call: ToolCall) -> ToolResult:
        if not allowed():
            return ToolResult.fail(
                call, "the web agent is disabled "
                      "(set web_agent_enabled=true in config to enable it)")
        try:
            gateway = gateway_factory()
        except Exception as exc:  # noqa: BLE001 — a broken factory is a fail, not a crash
            return ToolResult.fail(
                call, f"the web-agent gateway is unavailable "
                      f"({type(exc).__name__})")
        if gateway is None:
            return ToolResult.fail(
                call, "the web agent needs a Gemini API key (it is a "
                      "gateway-provider tool) — add one in Settings to "
                      "enable browser automation")
        events: list[str] = []
        agent = WebAgent(
            gateway,
            viewport_w=viewport_w, viewport_h=viewport_h,
            max_turns=max_turns,
            browser_factory=browser_factory,
            on_event=lambda kind, text: events.append(f"[{kind}] {text}"),
        )
        outcome = await agent.run_task(str(call.args.get("prompt", "")))
        outcome["log"] = events
        return ToolResult.success(call, data=outcome, risk=RiskClass.WRITE)
