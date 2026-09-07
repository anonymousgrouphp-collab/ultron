"""Temporary adapter from the pre-kernel handlers to the Phase 1 tool seam.

This is a migration seam, not a second tool framework. It turns the existing
Gemini declarations and legacy handler functions into ``Tool`` records so every
live request crosses policy, audit, timeout, and structured-result handling.
P1-F removes this adapter as handlers become native tools.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from kernel.bus import EventBus
from kernel.policy import AuditLog, ConsentCallback, PolicyEngine
from kernel.tools import Tool, ToolRegistry
from kernel.types import RiskClass, ToolCall, ToolResult

LegacyHandler = Callable[[dict[str, Any]], Awaitable[Any] | Any]


# A legacy handler with several operations receives the highest risk it can
# perform. P1-F will replace these broad tools with narrow native tools.
LEGACY_TOOL_RISKS: dict[str, RiskClass] = {
    "open_app": RiskClass.EXECUTE,
    "weather_report": RiskClass.READ,
    "browser_control": RiskClass.EXECUTE,
    "file_controller": RiskClass.DESTRUCTIVE,
    "send_message": RiskClass.WRITE,
    "reminder": RiskClass.WRITE,
    "youtube_video": RiskClass.EXECUTE,
    "screen_process": RiskClass.READ,
    "close_camera": RiskClass.WRITE,
    "computer_settings": RiskClass.DESTRUCTIVE,
    "desktop_control": RiskClass.WRITE,
    "code_helper": RiskClass.EXECUTE,
    "dev_agent": RiskClass.EXECUTE,
    "web_search": RiskClass.READ,
    "file_processor": RiskClass.EXECUTE,
    "computer_control": RiskClass.EXECUTE,
    "game_updater": RiskClass.EXECUTE,
    "flight_finder": RiskClass.WRITE,
    "system_status": RiskClass.READ,
    "shutdown_ultron": RiskClass.DESTRUCTIVE,
    "save_memory": RiskClass.WRITE,
}


def _json_schema(value: Any) -> Any:
    """Translate Gemini's uppercase type labels into ordinary JSON Schema."""
    if isinstance(value, Mapping):
        return {
            key: (
                str(item).lower()
                if key == "type" and isinstance(item, str)
                else _json_schema(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_json_schema(item) for item in value]
    return value


class LegacyToolRuntime:
    """The single execution seam for legacy declarations during P1-F.

    Construct it with the complete declared tool set, then call ``execute``.
    Its registry and policy stay private, preventing callers from directly
    invoking a legacy handler by lookup.
    """

    def __init__(
        self,
        *,
        declarations: Sequence[Mapping[str, Any]],
        handlers: Mapping[str, LegacyHandler],
        audit_path: str | Path | None = None,
        risks: Mapping[str, RiskClass] | None = None,
    ) -> None:
        self.bus = EventBus()
        self.registry = ToolRegistry()
        if audit_path is not None:
            Path(audit_path).parent.mkdir(parents=True, exist_ok=True)
        self.audit = AuditLog(audit_path)
        self.policy = PolicyEngine(audit=self.audit)

        risk_map = dict(risks or LEGACY_TOOL_RISKS)
        declared = {str(item.get("name", "")) for item in declarations}
        if not declared or "" in declared:
            raise ValueError("every legacy declaration needs a name")
        if declared != set(handlers) or declared != set(risk_map):
            raise ValueError(
                "legacy declarations, handlers, and risk classifications must match exactly"
            )

        for declaration in declarations:
            name = str(declaration["name"])
            self.registry.register(Tool(
                name=name,
                description=str(declaration["description"]),
                parameters=_json_schema(declaration["parameters"]),
                handler=self._handler_for(handlers[name]),
                risk=risk_map[name],
                timeout_s=30.0,
                max_retries=0,
            ))

    @staticmethod
    def _handler_for(handler: LegacyHandler):
        async def invoke(call: ToolCall) -> Any:
            result = handler(dict(call.args))
            if hasattr(result, "__await__"):
                return await result
            return result
        return invoke

    async def execute(
        self,
        call: ToolCall,
        *,
        consent: ConsentCallback | None = None,
    ) -> ToolResult:
        """Run a request through policy, audit, events, and the registry."""
        return await self.policy.run(call, self.registry, bus=self.bus, consent=consent)


__all__ = ["LEGACY_TOOL_RISKS", "LegacyHandler", "LegacyToolRuntime"]
