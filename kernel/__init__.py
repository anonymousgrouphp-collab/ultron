"""kernel — the ULTRON agent runtime (Phase 1).

Delivered so far:
- P1-A :mod:`kernel.types` + :mod:`kernel.bus` (contracts + event bus)
- P1-B :mod:`kernel.tools` (Tool record + ToolRegistry choke point)
- P1-C :mod:`kernel.gateway` (provider-neutral model gateway: Gemini + Ollama)
- P1-E :mod:`kernel.policy` (PolicyEngine gating + AuditLog)

Later streams add memory/, loop/, orchestrator/ — each talks through the bus,
none import main.py (Kill List).
"""

from kernel.bus import EventBus, Subscription
from kernel.gateway import (
    Gateway,
    GatewayError,
    GatewaySettings,
    GeminiAdapter,
    Message,
    OllamaAdapter,
    Provider,
    Response,
    build_gateway,
)
from kernel.policy import AuditLog, Decision, Policy, PolicyEngine
from kernel.tools import Tool, ToolRegistry, default_registry
from kernel.types import Event, RiskClass, ToolCall, ToolResult

__all__ = [
    "AuditLog", "Decision", "Event", "EventBus", "Gateway", "GatewayError",
    "GatewaySettings", "GeminiAdapter", "Message", "OllamaAdapter", "Policy",
    "PolicyEngine", "Provider", "Response", "RiskClass", "Subscription", "Tool",
    "ToolCall", "ToolRegistry", "ToolResult", "build_gateway",
    "default_registry",
]
