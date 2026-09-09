"""evals/dashboard.py — P5-A: the benchmark regression dashboard (roadmap §4
Phase 5: "regression dashboard, per-subsystem scorecards in CI"; §6: the
North-Star Metric is the 50-task success rate, which MAY NOT DROP).

Design:
- `evals/baseline.json` (tracked in git) holds the recorded best score —
  overall + per-category — for the SCRIPTED harness mode. Scripted mode
  measures the harness itself deterministically, so a drop is always a real
  regression, never model variance (live-mode scores are recorded for the
  trend but never gate CI — research/05 §7).
- `check_regression(report, baseline)` → per-category + overall deltas and
  the gate verdict: any category OR the overall score dropping below its
  baseline fails. Equal is allowed (a plateau is not a regression); the
  roadmap gate is "may not drop", not "must rise".
- `render_markdown(...)` → the human dashboard (per-subsystem scorecard) for
  release notes / the repo docs; `main()` prints it.
- `update_baseline` rewrites the tracked baseline after an intentional
  improvement (an explicit, reviewed action — never automatic).

This is eval tooling, not kernel code: it imports the suite by path and
writes only under evals/ + .ultron/.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

# Windows consoles default to a legacy code page (cp1252) that cannot print
# arrow/emoji characters used in task details — reconfigure instead of
# crashing mid-gate (found by the first branch-CI run: UnicodeEncodeError
# on '→' in a PASS line).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

BASELINE_PATH = BASE / "evals" / "baseline.json"
LIVE_TREND_PATH = BASE / ".ultron" / "eval" / "benchmark" / "live_trend.json"
CATEGORIES = ("files", "memory", "web", "coding", "orchestration")


@dataclass(frozen=True)
class RegressionVerdict:
    """Gate outcome: overall + per-category deltas vs the baseline."""

    passed: bool
    overall_before: float
    overall_after: float
    category_deltas: dict[str, tuple[float, float]]  # cat → (before, after)
    regressions: tuple[str, ...] = ()                 # human labels

    @property
    def overall_delta(self) -> float:
        return self.overall_after - self.overall_before

    def summary(self) -> dict[str, Any]:
        return {
            "passed": self.passed,
            "overall": {"before": round(self.overall_before, 4),
                        "after": round(self.overall_after, 4),
                        "delta": round(self.overall_delta, 4)},
            "categories": {
                cat: {"before": round(before, 4), "after": round(after, 4),
                      "delta": round(after - before, 4)}
                for cat, (before, after) in self.category_deltas.items()
            },
            "regressions": list(self.regressions),
        }


def load_baseline(path: Path | None = None) -> dict[str, Any]:
    path = path if path is not None else BASELINE_PATH
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def check_regression(
    summary: dict[str, Any],
    baseline: dict[str, Any],
) -> RegressionVerdict:
    """The NSM gate: overall + every category must be >= its baseline value.
    A missing baseline entry is treated as 0.0 (first recording)."""
    before_overall = float(baseline.get("score", 0.0))
    after_overall = float(summary.get("score", 0.0))
    before_cats = baseline.get("categories", {})
    after_cats = summary.get("categories", {})
    deltas: dict[str, tuple[float, float]] = {}
    regressions: list[str] = []
    for cat in CATEGORIES:
        if cat not in after_cats:
            continue  # partial run — only judged categories count
        before = float(before_cats.get(cat, 0.0))
        after = float(after_cats[cat])
        deltas[cat] = (before, after)
        if after < before:
            regressions.append(
                f"{cat}: {before:.2f} → {after:.2f} "
                f"(−{(before - after):.2f})")
    passed = True
    if after_overall < before_overall:
        passed = False
        regressions.insert(
            0, f"overall: {before_overall:.2f} → {after_overall:.2f} "
               f"(−{(before_overall - after_overall):.2f})")
    if regressions:
        passed = False
    return RegressionVerdict(
        passed=passed,
        overall_before=before_overall,
        overall_after=after_overall,
        category_deltas=deltas,
        regressions=tuple(regressions),
    )


def render_markdown(summary: dict[str, Any],
                    verdict: RegressionVerdict | None = None) -> str:
    """Per-subsystem scorecard as markdown (release notes / docs)."""
    lines = [
        "# ULTRON Benchmark Dashboard",
        "",
        f"- **Mode**: {summary.get('mode', '?')}",
    ]
    if summary.get("mode") == "live":
        lines.append(f"- **Provider/Model**: {summary.get('provider', '')} "
                     f"· {summary.get('model', '')}")
    lines += [
        f"- **Score**: {summary.get('passed', 0)}/{summary.get('tasks', 0)} "
        f"(**{float(summary.get('score', 0.0)):.1%}**)"
        + (" · partial run" if summary.get("partial") else ""),
        "",
        "| Subsystem | Score |",
        "|---|---|",
    ]
    for cat in CATEGORIES:
        score = summary.get("categories", {}).get(cat)
        if score is None:
            continue
        lines.append(f"| {cat} | {score:.0%} |")
    if verdict is not None:
        lines += ["", "## Non-regression gate",
                  "", f"**{'PASS' if verdict.passed else 'FAIL'}** — overall "
                  f"{verdict.overall_before:.2f} → {verdict.overall_after:.2f}"]
        for label in verdict.regressions:
            lines.append(f"- 🔻 {label}")
        if not verdict.regressions:
            lines.append("- no regressions vs the tracked baseline")
    failures = [r for r in summary.get("results", []) if not r.get("ok")]
    if failures:
        lines += ["", "## Failures", ""]
        for fail in failures:
            lines.append(f"- `{fail['id']}` [{fail['category']}]: "
                         f"{fail['detail']}")
    return "\n".join(lines) + "\n"


def update_baseline(summary: dict[str, Any],
                    path: Path | None = None) -> Path:
    """Rewrite the tracked baseline after an intentional improvement.
    Records only scripted-mode harness scores; live runs go to the trend
    file instead."""
    path = path if path is not None else BASELINE_PATH
    payload = {
        "mode": summary.get("mode", "scripted"),
        "score": round(float(summary.get("score", 0.0)), 4),
        "categories": {k: round(float(v), 4)
                       for k, v in summary.get("categories", {}).items()},
        "tasks": summary.get("tasks", 0),
        "passed": summary.get("passed", 0),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def record_live_trend(summary: dict[str, Any],
                      path: Path | None = None) -> Path:
    """Append a live-mode run to the trend log (append-only history; never
    the CI gate — model variance lands on the dashboard, not the build)."""
    import time as _time

    path = path if path is not None else LIVE_TREND_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    trend: list[dict[str, Any]] = []
    if path.exists():
        try:
            trend = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            trend = []  # corrupt trend log: start fresh rather than die
    trend.append({
        "ts": _time.time(),
        "provider": summary.get("provider", ""),
        "model": summary.get("model", ""),
        "score": round(float(summary.get("score", 0.0)), 4),
        "categories": {k: round(float(v), 4)
                       for k, v in summary.get("categories", {}).items()},
    })
    path.write_text(json.dumps(trend, indent=2) + "\n", encoding="utf-8")
    return path


async def _run_and_gate(baseline_path: Path | None, root: Path | None,
                        update: bool) -> int:
    from evals.suite import run_suite

    report = await run_suite(mode="scripted", root=root)
    summary = report.summary()
    baseline = load_baseline(baseline_path)
    verdict = check_regression(summary, baseline)
    print(render_markdown(summary, verdict))
    if update:
        path = update_baseline(summary, baseline_path)
        print(f"baseline updated: {path}")
        return 0
    return 0 if verdict.passed else 1


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--baseline", default=None,
                        help="baseline JSON path (default evals/baseline.json)")
    parser.add_argument("--root", default=None,
                        help="suite run directory (default .ultron/eval/benchmark)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="record this scripted run as the new baseline "
                             "(intentional, reviewed improvement)")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run_and_gate(
        Path(args.baseline) if args.baseline else None,
        Path(args.root) if args.root else None,
        args.update_baseline)))


if __name__ == "__main__":
    main()
