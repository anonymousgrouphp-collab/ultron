"""kernel/proactive/dashboard_bridge.py — Phase O4: EventBus → Dashboard bridge.

Subscribes to kernel EventBus segments and forwards events to the dashboard
via its WebSocket broadcast method.  This makes the dashboard a real-time
view of kernel activity without polling.

Events forwarded:
- tool.started / tool.completed / tool.failed → tool execution activity
- proactive.decision → proactive suggestions fired
- memory.consolidated → memory writes
- job.started / job.step / job.completed / job.failed → orchestrator activity
"""

from __future__ import annotations

from typing import Any

from kernel.bus import EventBus

__all__ = ["BusDashboardBridge"]


class BusDashboardBridge:
    """Bridges kernel EventBus events to dashboard WebSocket broadcasts.

    Usage::

        bridge = BusDashboardBridge(bus, dashboard)
        bridge.attach()  # subscribes to bus segments
        # ... later ...
        bridge.detach()  # unsubscribes
    """

    def __init__(self, bus: EventBus, dashboard: Any) -> None:
        self._bus = bus
        self._dashboard = dashboard
        self._subscriptions: list[Any] = []

    def attach(self) -> None:
        """Subscribe to relevant bus segments."""
        # Tool execution events
        self._subscriptions.append(
            self._bus.subscribe("tool.*", self._on_tool_event)
        )
        # Proactive decisions
        self._subscriptions.append(
            self._bus.subscribe("proactive.*", self._on_proactive_event)
        )
        # Memory events
        self._subscriptions.append(
            self._bus.subscribe("memory.*", self._on_memory_event)
        )
        # Orchestrator job events
        self._subscriptions.append(
            self._bus.subscribe("job.*", self._on_job_event)
        )
        # Health events (Phase W5: tool/gateway failures reach the HUD)
        self._subscriptions.append(
            self._bus.subscribe("health.*", self._on_health_event)
        )

    def detach(self) -> None:
        """Unsubscribe from all bus segments."""
        for sub in self._subscriptions:
            try:
                sub.unsubscribe()
            except Exception:
                pass
        self._subscriptions.clear()

    def _broadcast(self, event_type: str, data: dict[str, Any]) -> None:
        """Send an event to the dashboard (fire-and-forget)."""
        if self._dashboard is None:
            return
        try:
            import asyncio
            loop = asyncio.get_event_loop()
            if loop and loop.is_running():
                asyncio.create_task(
                    self._dashboard.broadcast({
                        "type": "kernel_event",
                        "event_type": event_type,
                        **data,
                    })
                )
        except Exception:
            pass

    def _on_tool_event(self, event: Any) -> None:
        """Forward tool execution events."""
        # Real kernel Events carry .payload; .detail was only ever set by
        # test fakes — reading it made production forwarding empty.
        detail = (getattr(event, "payload", None)
                  or getattr(event, "detail", None) or {})
        segment = getattr(event, "segment", "")
        self._broadcast(segment, {
            "tool": detail.get("name", ""),
            "ok": detail.get("ok", None),
            "risk": detail.get("risk", ""),
            "error": detail.get("error"),
            "duration_ms": detail.get("duration_ms", 0),
        })

    def _on_proactive_event(self, event: Any) -> None:
        """Forward proactive decision events."""
        # Real kernel Events carry .payload; .detail was only ever set by
        # test fakes — reading it made production forwarding empty.
        detail = (getattr(event, "payload", None)
                  or getattr(event, "detail", None) or {})
        self._broadcast("proactive.decision", {
            "rule": detail.get("rule", ""),
            "message": detail.get("message", ""),
            "fired": detail.get("fired", False),
            "suppressed": detail.get("suppressed", False),
        })

    def _on_memory_event(self, event: Any) -> None:
        """Forward memory consolidation events."""
        # Real kernel Events carry .payload; .detail was only ever set by
        # test fakes — reading it made production forwarding empty.
        detail = (getattr(event, "payload", None)
                  or getattr(event, "detail", None) or {})
        self._broadcast("memory.consolidated", {
            "summary": detail.get("summary", ""),
            "ops_count": detail.get("ops_count", 0),
        })

    def _on_job_event(self, event: Any) -> None:
        """Forward orchestrator job events."""
        # Real kernel Events carry .payload; .detail was only ever set by
        # test fakes — reading it made production forwarding empty.
        detail = (getattr(event, "payload", None)
                  or getattr(event, "detail", None) or {})
        segment = getattr(event, "segment", "")
        self._broadcast(segment, {
            "job_id": detail.get("job_id", ""),
            "status": detail.get("status", ""),
            "step": detail.get("step", ""),
            "error": detail.get("error"),
        })

    def _on_health_event(self, event: Any) -> None:
        """Phase W5: forward health errors to the dashboard."""
        # Real kernel Events carry .payload; .detail was only ever set by
        # test fakes — reading it made production forwarding empty.
        detail = (getattr(event, "payload", None)
                  or getattr(event, "detail", None) or {})
        self._broadcast("health.error", {
            "component": detail.get("component", ""),
            "name": detail.get("name", ""),
            "error": detail.get("error"),
        })
