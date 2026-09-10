"""evals/trend.py — R6: live-benchmark trend recording + release-over-release view.

Runs (or reads) a LIVE suite run and appends one row to
`evals/results/live_trend.jsonl` — JSONL so the file is append-only and
inspectable line-by-line. Each row carries the git ref, provider/model,
overall score, and per-category scores; the report prints the delta vs the
previous run of the same provider and vs the provider's best-ever score.

Fail-soft by design (roadmap §4 Phase 5: live-mode scores land on the trend,
never gate CI — model variance is not a regression): a failed live run is
reported and recorded, and the exit code stays 0 unless the arguments are
invalid. The scheduled workflow uploads the trend file as an artifact; the
tracked `evals/baseline.json` (scripted mode) remains the only gate.

Usage:
    python evals/trend.py --provider openai [--suite-root DIR]
    python evals/trend.py --report-file <summary.json>
"""

from __future__ import annotations

import argparse
import asyncio
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Windows consoles default to cp1252 and cannot print the arrows/emojis in
# reports — reconfigure instead of crashing mid-run (p5-evals precedent).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

TREND_PATH = BASE / "evals" / "results" / "live_trend.jsonl"
CATEGORIES = ("files", "memory", "web", "coding", "orchestration")


def _git_ref() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        )
        return out.stdout.strip() or ""
    except Exception:  # noqa: BLE001 — no git in CI artifact copies
        return ""


def load_trend(path: Path | None = None) -> list[dict[str, Any]]:
    """Read all recorded rows. Missing/corrupt tail degrades to the rows
    that parse — the file is append-only, never rewritten."""
    path = path if path is not None else TREND_PATH
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # torn tail write — ignore, keep the rest
    return rows


def record(summary: dict[str, Any], path: Path | None = None,
           ref: str | None = None) -> dict[str, Any]:
    """Append one trend row; returns it. Never raises on I/O (fail-soft)."""
    path = path if path is not None else TREND_PATH
    row = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "ref": ref if ref is not None else _git_ref(),
        "provider": summary.get("provider", ""),
        "model": summary.get("model", ""),
        "mode": summary.get("mode", "live"),
        "score": round(float(summary.get("score", 0.0)), 4),
        "passed": summary.get("passed", 0),
        "tasks": summary.get("tasks", 0),
        "partial": bool(summary.get("partial", False)),
        "categories": {k: round(float(v), 4)
                       for k, v in summary.get("categories", {}).items()},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row) + "\n")
    except OSError as exc:  # fail-soft: report, never gate
        print(f"[trend] ⚠️ could not append to {path}: {exc}")
    return row


def render_report(row: dict[str, Any], rows: list[dict[str, Any]]) -> str:
    """Markdown-ish report: this run vs the same provider's previous run
    (release-over-release) and best-ever score."""
    provider = row.get("provider", "?")
    same_provider = [r for r in rows if r.get("provider") == provider
                     and r.get("ts") != row.get("ts")]
    previous = same_provider[-1] if same_provider else None
    best = max(same_provider, key=lambda r: float(r.get("score", 0.0)),
               default=None)

    lines = [
        "# ULTRON Live Benchmark Trend",
        "",
        f"- **Ref**: {row.get('ref', '?')}  ·  **Provider/Model**: "
        f"{provider} · {row.get('model', '?')}",
        f"- **Score**: {row.get('passed', 0)}/{row.get('tasks', 0)} "
        f"(**{float(row.get('score', 0.0)):.1%}**)"
        + (" · partial run" if row.get("partial") else ""),
        "",
        "| Subsystem | This run | vs previous | vs best |",
        "|---|---|---|---|",
    ]
    for cat in CATEGORIES:
        score = row.get("categories", {}).get(cat)
        if score is None:
            continue
        prev = float(previous.get("categories", {}).get(cat, score)) \
            if previous else score
        best_v = float(best.get("categories", {}).get(cat, score)) \
            if best else score
        lines.append(
            f"| {cat} | {score:.0%} | {score - prev:+.0%} "
            f"({prev:.0%}) | {best_v:.0%} |")
    if previous:
        delta = float(row.get("score", 0.0)) - float(previous.get("score", 0.0))
        lines.append("")
        lines.append(
            f"**Release-over-release** ({previous.get('ref', '?')} → "
            f"{row.get('ref', '?')}): {delta:+.1%} "
            f"({float(previous.get('score', 0.0)):.1%} → "
            f"{float(row.get('score', 0.0)):.1%})")
    if best and best.get("ref") != row.get("ref"):
        best_delta = float(row.get("score", 0.0)) - float(best.get("score", 0.0))
        lines.append(f"**vs best-ever** ({best.get('ref', '?')} "
                     f"{float(best.get('score', 0.0)):.1%}): "
                     f"{best_delta:+.1%}")
    if previous and delta < 0:  # noqa: F821 — delta always bound above
        lines.append("")
        lines.append("> ⚠️ down from the previous run — model variance or a "
                     "real regression; scripted `evals/dashboard.py` remains "
                     "the gate.")
    return "\n".join(lines) + "\n"


async def _run_and_record(provider: str, suite_root: Path) -> int:
    from evals.suite import run_suite

    try:
        report = await run_suite(mode="live", provider=provider, root=suite_root)
        summary = report.summary()
    except SystemExit as exc:
        # e.g. missing API key — fail-soft: report, never gate.
        print(f"[trend] ⚠️ live run skipped: {exc}")
        return 0
    except Exception as exc:  # noqa: BLE001
        print(f"[trend] ⚠️ live run failed: {type(exc).__name__}: {exc}")
        return 0

    rows = load_trend()
    row = record(summary)
    print(render_report(row, rows + [row]))
    print(f"[trend] appended to {TREND_PATH}")
    return 0


def _record_report_file(path: Path) -> int:
    try:
        summary = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"[trend] ⚠️ cannot read report file {path}: {exc}")
        return 0
    rows = load_trend()
    row = record(summary)
    print(render_report(row, rows + [row]))
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", choices=["ollama", "gemini", "openai"],
                        help="run the live suite with this provider")
    parser.add_argument("--suite-root", default=".ultron/eval/benchmark/live",
                        help="run directory for the live suite")
    parser.add_argument("--report-file", default=None,
                        help="record an existing summary JSON instead of "
                             "running the suite")
    args = parser.parse_args()

    if args.report_file:
        raise SystemExit(_record_report_file(Path(args.report_file)))
    if not args.provider:
        parser.error("need --provider (or --report-file)")
    raise SystemExit(asyncio.run(_run_and_record(
        args.provider, Path(args.suite_root))))


if __name__ == "__main__":
    main()
