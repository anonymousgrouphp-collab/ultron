"""kernel/evals/comparison.py — Phase Q1: model comparison and regression detection.

Compares benchmark results across providers and detects per-task regressions.
This enables:
1. Model comparison reports (which model is best for which category)
2. Per-task regression detection (did task X fail on provider Y?)
3. Performance metrics (latency, token usage, cost per task)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["ModelComparator", "ComparisonReport"]


@dataclass(frozen=True)
class TaskResult:
    """Result of a single benchmark task."""

    task_id: str
    category: str
    passed: bool
    duration_s: float = 0.0
    steps: int = 0
    error: str | None = None


@dataclass(frozen=True)
class ComparisonReport:
    """Comparison report across providers."""

    providers: dict[str, dict[str, Any]]
    category_scores: dict[str, dict[str, float]]
    regressions: list[str]
    improvements: list[str]


@dataclass
class ModelComparator:
    """Compares benchmark results across providers.

    Usage::

        comparator = ModelComparator()
        comparator.record("gemini", [TaskResult("t1", "files", True, 0.5)])
        comparator.record("ollama", [TaskResult("t1", "files", False, 1.0, error="timeout")])
        report = comparator.compare()
    """

    _results: dict[str, list[TaskResult]] = field(default_factory=dict)
    _baseline: dict[str, bool] | None = None

    def record(self, provider: str, results: list[TaskResult]) -> None:
        """Record results for a provider."""
        self._results[provider] = results

    def set_baseline(self, baseline: dict[str, bool]) -> None:
        """Set the baseline results for regression detection.

        Parameters
        ----------
        baseline:
            Mapping of task_id → passed (from the tracked baseline).
        """
        self._baseline = baseline

    def compare(self) -> ComparisonReport:
        """Generate a comparison report across all providers."""
        providers: dict[str, dict[str, Any]] = {}
        category_scores: dict[str, dict[str, float]] = {}
        regressions: list[str] = []
        improvements: list[str] = []

        for provider, results in self._results.items():
            total = len(results)
            passed = sum(1 for r in results if r.passed)
            avg_duration = (
                sum(r.duration_s for r in results) / total if total else 0
            )

            providers[provider] = {
                "total": total,
                "passed": passed,
                "score": round(passed / total, 3) if total else 0,
                "avg_duration_s": round(avg_duration, 3),
            }

            # Per-category scores
            categories: dict[str, list[bool]] = {}
            for r in results:
                categories.setdefault(r.category, []).append(r.passed)

            for cat, cat_results in categories.items():
                if provider not in category_scores:
                    category_scores[provider] = {}
                category_scores[provider][cat] = round(
                    sum(cat_results) / len(cat_results), 3
                )

            # Regression detection vs baseline
            if self._baseline:
                for r in results:
                    base_pass = self._baseline.get(r.task_id)
                    if base_pass is True and not r.passed:
                        regressions.append(
                            f"{provider}: {r.task_id} regressed (was passing)"
                        )
                    elif base_pass is False and r.passed:
                        improvements.append(
                            f"{provider}: {r.task_id} improved (was failing)"
                        )

        return ComparisonReport(
            providers=providers,
            category_scores=category_scores,
            regressions=regressions,
            improvements=improvements,
        )

    def format_report(self, report: ComparisonReport) -> str:
        """Format a comparison report as human-readable text."""
        lines = ["Model Comparison Report", "=" * 40]

        for provider, stats in sorted(report.providers.items()):
            lines.append(
                f"\n{provider}: {stats['passed']}/{stats['total']} "
                f"({stats['score']*100:.0f}%) "
                f"avg {stats['avg_duration_s']:.2f}s"
            )

        if report.category_scores:
            lines.append("\nCategory Scores:")
            for provider, cats in sorted(report.category_scores.items()):
                for cat, score in sorted(cats.items()):
                    lines.append(f"  {provider}/{cat}: {score*100:.0f}%")

        if report.regressions:
            lines.append(f"\n⚠ Regressions ({len(report.regressions)}):")
            for r in report.regressions:
                lines.append(f"  - {r}")

        if report.improvements:
            lines.append(f"\n✓ Improvements ({len(report.improvements)}):")
            for i in report.improvements:
                lines.append(f"  + {i}")

        return "\n".join(lines)

    def save(self, path: Path) -> None:
        """Save results to a JSON file."""
        data = {}
        for provider, results in self._results.items():
            data[provider] = [
                {
                    "task_id": r.task_id,
                    "category": r.category,
                    "passed": r.passed,
                    "duration_s": r.duration_s,
                    "steps": r.steps,
                    "error": r.error,
                }
                for r in results
            ]
        path.write_text(json.dumps(data, indent=2))

    def load(self, path: Path) -> None:
        """Load results from a JSON file."""
        data = json.loads(path.read_text())
        for provider, results in data.items():
            self._results[provider] = [
                TaskResult(
                    task_id=r["task_id"],
                    category=r["category"],
                    passed=r["passed"],
                    duration_s=r.get("duration_s", 0),
                    steps=r.get("steps", 0),
                    error=r.get("error"),
                )
                for r in results
            ]
