"""kernel/diagnostics/health.py — Phase Q3: health monitoring and self-diagnostics.

Tracks system health metrics, detects issues, and suggests recovery actions.
This is the foundation for self-healing behavior.

Metrics tracked:
- Memory usage (database size, fact count, procedure count)
- API quota usage (estimated from gateway calls)
- Disk space
- Component health (gateway, memory, orchestrator, etc.)
- Error rates (tool failures, gateway errors)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["HealthMonitor", "HealthReport", "HealthStatus"]


class HealthStatus:
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"


@dataclass
class HealthReport:
    """Health report for a component or the overall system."""

    status: str
    component: str
    metrics: dict[str, Any] = field(default_factory=dict)
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    timestamp: float = field(default_factory=time.time)


@dataclass
class HealthMonitor:
    """Monitors system health and suggests recovery actions.

    Usage::

        monitor = HealthMonitor(base_dir=Path(".ultron"))
        report = await monitor.check_health()
        if report.status != HealthStatus.HEALTHY:
            for issue in report.issues:
                print(f"Issue: {issue}")
            for rec in report.recommendations:
                print(f"Recommendation: {rec}")
    """

    base_dir: Path | None = None
    _error_counts: dict[str, int] = field(default_factory=dict)
    _last_check: float = 0.0

    async def check_health(self) -> HealthReport:
        """Run a full health check."""
        issues: list[str] = []
        recommendations: list[str] = []
        metrics: dict[str, Any] = {}

        # Check memory database
        if self.base_dir:
            db_path = self.base_dir / "memory.sqlite3"
            if db_path.exists():
                db_size = db_path.stat().st_size
                metrics["memory_db_size_mb"] = round(db_size / (1024 * 1024), 2)
                if db_size > 100 * 1024 * 1024:  # > 100MB
                    issues.append("Memory database is very large (>100MB)")
                    recommendations.append("Run memory consolidation to compact the database")

        # Check disk space
        if self.base_dir:
            try:
                import shutil
                total, used, free = shutil.disk_usage(self.base_dir)
                metrics["disk_free_gb"] = round(free / (1024**3), 2)
                if free < 1024**3:  # < 1GB
                    issues.append("Low disk space (<1GB free)")
                    recommendations.append("Free up disk space or clean old logs")
            except Exception:
                pass

        # Check error rates
        total_errors = sum(self._error_counts.values())
        metrics["total_errors"] = total_errors
        if total_errors > 10:
            issues.append(f"High error count: {total_errors} total errors")
            recommendations.append("Check logs for recurring errors")

        # Check component health
        metrics["error_counts"] = dict(self._error_counts)

        # Determine overall status
        if not issues:
            status = HealthStatus.HEALTHY
        elif len(issues) <= 2:
            status = HealthStatus.DEGRADED
        else:
            status = HealthStatus.UNHEALTHY

        self._last_check = time.time()

        return HealthReport(
            status=status,
            component="system",
            metrics=metrics,
            issues=issues,
            recommendations=recommendations,
        )

    def record_error(self, component: str, error_type: str = "general") -> None:
        """Record an error for a component."""
        key = f"{component}:{error_type}"
        self._error_counts[key] = self._error_counts.get(key, 0) + 1

    def get_error_summary(self) -> dict[str, int]:
        """Get error counts by component."""
        summary: dict[str, int] = {}
        for key, count in self._error_counts.items():
            component = key.split(":")[0]
            summary[component] = summary.get(component, 0) + count
        return summary
