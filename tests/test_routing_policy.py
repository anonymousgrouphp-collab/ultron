"""tests/test_routing_policy.py — Phase A5: the complexity-routing policy +
its eval harness (evals/routing.py), the trend markdown-report flag, and the
live-benchmark workflow's never-gate structure.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kernel.diagnostics.cost_tracker import CostTracker

REPO = Path(__file__).resolve().parent.parent


# ------------------------------------------------------- classifier --------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("what time is it", "simple"),
        ("open the dashboard", "simple"),
        ("play some jazz", "simple"),
        ("remind me in 10 minutes to stretch", "simple"),
        ("remember that my wifi password is hunter2", "medium"),
        ("what did I say about the bike shop", "medium"),
        ("set a reminder for tomorrow at nine", "medium"),
        ("research fusion energy and save a summary file", "complex"),
        ("run a morning briefing every day in the background", "complex"),
        # complex wins over simple markers: 'open ' + heavy intent
        ("research the fusion page and then open the dashboard", "complex"),
        # long structured command (>= 14 words) is complex by shape
        ("look up the Spoke and Pedal shop online and then summarize "
         "what they offer and write it down", "complex"),
    ],
)
def test_classify_complexity(text: str, expected: str) -> None:
    from evals.routing import classify_complexity

    assert classify_complexity(text) == expected


def test_classify_is_deterministic_and_total() -> None:
    from evals.routing import TIERS, classify_complexity

    for text in ("", "  ", "???", "a", "open the pod bay doors hal"):
        assert classify_complexity(text) in TIERS
    assert classify_complexity("open the dashboard") == \
        classify_complexity("  open  the  dashboard  ")


# ------------------------------------------------------------- routing -----


def test_route_task_default_mapping_uses_cost_tracker() -> None:
    from evals.routing import route_task

    assert route_task("play some jazz").provider == "ollama"
    assert route_task("research fusion energy and save a summary file")\
        .provider == "gemini"


def test_route_task_budget_exceeded_falls_back_to_ollama() -> None:
    from evals.routing import route_task

    tracker = CostTracker(budget_usd=0.001)
    tracker.record_usage(provider="gemini", input_tokens=1_000_000,
                         output_tokens=1_000_000)
    decision = route_task("play some jazz", tracker=tracker)
    assert decision.provider == "ollama"
    assert "budget exceeded" in decision.reason


def test_route_task_injected_mapping_wins() -> None:
    from evals.routing import route_task

    decision = route_task("play some jazz", suggest=lambda tier: "openai")
    assert decision.provider == "openai"
    assert "injected" in decision.reason


# ----------------------------------------------------------- eval harness --


def test_routing_eval_perfect_on_labeled_corpus() -> None:
    from evals.routing import ROUTING_CORPUS, eval_routing

    record = eval_routing()
    assert record["tasks"] == len(ROUTING_CORPUS) >= 15
    # The corpus and the policy live in the same repo: a policy change that
    # breaks a labeled command must fail CI (deterministic, no model calls).
    assert record["accuracy"] == 1.0, record["misses"]


def test_routing_eval_record_and_report_written(tmp_path: Path) -> None:
    import json

    from evals.routing import ROUTING_CORPUS, eval_routing, render_report

    record = eval_routing(ROUTING_CORPUS[:3])
    out = tmp_path / "routing_report.md"
    out.write_text(render_report(record), encoding="utf-8")
    text = out.read_text(encoding="utf-8")
    assert "Accuracy" in text and "| simple |" in text
    json.dumps(record)  # record is JSON-able (results artifact)


# --------------------------------------------------- trend --report-out ----


def test_trend_report_file_mode_writes_markdown(tmp_path: Path) -> None:
    """--report-file + --report-out: record an existing summary and emit the
    markdown report as a file (the A5 artifact path), fail-soft."""
    import json

    import evals.trend as trend

    summary_path = tmp_path / "summary.json"
    summary_path.write_text(json.dumps({
        "provider": "openai", "model": "gpt-x", "mode": "live",
        "score": 0.92, "passed": 46, "tasks": 50, "partial": False,
        "categories": {"files": 1.0, "memory": 0.9, "web": 0.9,
                       "coding": 0.9, "orchestration": 0.9},
    }), encoding="utf-8")
    report_out = tmp_path / "live_report.md"
    rc = trend._record_report_file(summary_path, report_out)
    assert rc == 0
    text = report_out.read_text(encoding="utf-8")
    assert "# ULTRON Live Benchmark Trend" in text
    assert "92.0%" in text or "92%" in text


# --------------------------------------------- workflow never-gate pins ----


def test_live_benchmark_workflow_is_monthly_and_never_gates() -> None:
    """Structural pins on .github/workflows/live-benchmark.yml (A5): monthly
    cron, dispatch, artifacts uploaded, and NO step can fail CI — every run
    step carries continue-on-error or is an upload (if: always()). Live
    scores land on the trend/dashboard, never the merge gate."""
    wf = (REPO / ".github" / "workflows" / "live-benchmark.yml")\
        .read_text(encoding="utf-8")
    assert 'cron: "0 5 1 * *"' in wf          # monthly, 1st @ 05:00 UTC
    assert "workflow_dispatch:" in wf
    assert "--report-out" in wf               # markdown report artifact
    assert "python evals/routing.py" in wf    # routing signal runs
    # every python step is fail-soft
    assert wf.count("continue-on-error: true") >= 3
    # no RUN STEP invokes the gated benchmark dashboard here (comments may
    # mention it — it lives in the main CI by design)
    run_steps = [ln for ln in wf.splitlines()
                 if ln.strip().startswith("run:")]
    assert all("evals/dashboard.py" not in ln for ln in run_steps)
