"""kernel/diagnostics/cost_tracker.py — Phase Q4: cost optimization and token tracking.

Tracks token usage per task/provider and estimates costs.  This enables:
1. Per-task cost reporting
2. Model routing by task complexity (simple → cheap local, complex → expensive cloud)
3. Budget alerts
4. Monthly cost reports

Usage::

    tracker = CostTracker()
    tracker.record_usage(provider="gemini", input_tokens=500, output_tokens=200)
    report = tracker.get_report()
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

__all__ = ["CostTracker", "CostReport", "UsageRecord"]


# Approximate costs per 1K tokens (USD) — update as pricing changes
_PROVIDER_COSTS: dict[str, dict[str, float]] = {
    "gemini": {"input": 0.000125, "output": 0.000375},  # Gemini Flash
    "ollama": {"input": 0.0, "output": 0.0},              # Free (local)
    "openai": {"input": 0.00015, "output": 0.0006},       # GPT-4o-mini
}


@dataclass(frozen=True)
class UsageRecord:
    """One token usage record."""

    provider: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    task_id: str = ""
    timestamp: float = field(default_factory=time.time)


@dataclass
class CostReport:
    """Aggregated cost report."""

    total_cost_usd: float
    total_input_tokens: int
    total_output_tokens: int
    by_provider: dict[str, float] = field(default_factory=dict)
    record_count: int = 0
    period_start: float = 0.0
    period_end: float = 0.0


@dataclass
class CostTracker:
    """Tracks token usage and estimates costs.

    Parameters
    ----------
    budget_usd:
        Monthly budget limit (0 = no limit).
    alert_threshold:
        Alert when this fraction of budget is used (default 0.8 = 80%).
    """

    budget_usd: float = 0.0
    alert_threshold: float = 0.8
    _records: list[UsageRecord] = field(default_factory=list)

    def record_usage(
        self,
        *,
        provider: str,
        model: str = "",
        input_tokens: int = 0,
        output_tokens: int = 0,
        task_id: str = "",
    ) -> UsageRecord:
        """Record token usage for a request."""
        costs = _PROVIDER_COSTS.get(provider, {"input": 0, "output": 0})
        cost = (input_tokens * costs["input"] + output_tokens * costs["output"]) / 1000

        record = UsageRecord(
            provider=provider,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
            task_id=task_id,
        )
        self._records.append(record)
        return record

    def get_report(
        self,
        *,
        since: float | None = None,
    ) -> CostReport:
        """Generate a cost report.

        Parameters
        ----------
        since:
            Timestamp to filter from (default: all records).
        """
        records = self._records
        if since is not None:
            records = [r for r in records if r.timestamp >= since]

        total_cost = sum(r.cost_usd for r in records)
        total_input = sum(r.input_tokens for r in records)
        total_output = sum(r.output_tokens for r in records)

        by_provider: dict[str, float] = {}
        for r in records:
            by_provider[r.provider] = by_provider.get(r.provider, 0) + r.cost_usd

        return CostReport(
            total_cost_usd=round(total_cost, 6),
            total_input_tokens=total_input,
            total_output_tokens=total_output,
            by_provider={k: round(v, 6) for k, v in by_provider.items()},
            record_count=len(records),
            period_start=records[0].timestamp if records else 0,
            period_end=records[-1].timestamp if records else 0,
        )

    def check_budget(self) -> tuple[bool, str]:
        """Check if budget is exceeded.

        Returns (ok, message).
        """
        if self.budget_usd <= 0:
            return True, "No budget set"

        report = self.get_report()
        if report.total_cost_usd >= self.budget_usd:
            return False, (
                f"Budget exceeded: ${report.total_cost_usd:.4f} / "
                f"${self.budget_usd:.2f}"
            )
        if report.total_cost_usd >= self.budget_usd * self.alert_threshold:
            return True, (
                f"Budget warning: ${report.total_cost_usd:.4f} / "
                f"${self.budget_usd:.2f} "
                f"({report.total_cost_usd / self.budget_usd * 100:.0f}%)"
            )
        return True, "Within budget"

    def suggest_provider(self, task_complexity: str = "medium") -> str:
        """Suggest the cheapest provider for a task complexity level.

        Parameters
        ----------
        task_complexity:
            "simple", "medium", or "complex".
        """
        ok, _ = self.check_budget()
        if not ok:
            # Budget exceeded — use free local model
            return "ollama"

        if task_complexity == "simple":
            return "ollama"  # Free for simple tasks
        elif task_complexity == "complex":
            return "gemini"  # Best quality for complex tasks
        else:
            return "gemini"  # Default to best quality
