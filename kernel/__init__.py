"""kernel — the ULTRON agent runtime (Phase 1).

P1-A delivered the contracts: :mod:`kernel.types` (RiskClass, ToolCall,
ToolResult, Event) and :mod:`kernel.bus` (EventBus). Later streams add
tools/, gateway/, memory/, policy/, loop/, orchestrator/ — each talks through
the bus, none import main.py (Kill List).
"""

from kernel.bus import EventBus, Subscription
from kernel.types import Event, RiskClass, ToolCall, ToolResult

__all__ = ["Event", "EventBus", "RiskClass", "Subscription", "ToolCall", "ToolResult"]
