"""app/observability.py — cost/usage seam + the shared key error (Phase P3).

Moved verbatim from main.py. `_UsageTrackingGateway` sits at the ONE seam
all agent-loop traffic crosses and feeds the CostTracker; `ApiKeyMissing`
is the shared key gate (main re-exports it for the characterization pins).
"""

from __future__ import annotations


class ApiKeyMissing(Exception):
    """Raised when config/api_keys.json is missing, broken, or has no real key."""


class _UsageTrackingGateway:
    """Phase W5: transparent wrapper that feeds every completion's token
    usage into the CostTracker (the audit's '/cost never records' finding).
    Sits at the ONE seam all agent-loop/orchestrator traffic crosses."""

    def __init__(self, inner, cost_tracker):
        self._inner = inner
        self._cost = cost_tracker

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def complete(self, messages, tools=(), response_schema=None):
        response = await self._inner.complete(messages, tools=tools,
                                              response_schema=response_schema)
        try:
            usage = dict(response.usage or {})
            if usage:
                self._cost.record_usage(
                    provider=response.provider or "unknown",
                    model=response.model or "",
                    input_tokens=int(usage.get("input", 0)),
                    output_tokens=int(usage.get("output", 0)),
                )
        except Exception:
            pass  # observability must never break the model path
        return response

__all__ = ["ApiKeyMissing", "_UsageTrackingGateway"]

