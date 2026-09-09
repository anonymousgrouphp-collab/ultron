"""tests/test_benchmark_suite.py — P5-A: CI pins for the 50-task benchmark.

The Phase-5 acceptance is the roadmap's NSM ("50-task success rate may not
drop"), so the pins prove three things:
1. CORPUS INTEGRITY — exactly 50 tasks, 5 categories × 10, unique ids,
   every LoopTask carries a scripted model + a verifier; no category
   silently shrinks (a suite that loses tasks would inflate its score).
2. SCRIPTED FLOOR — the suite, run hermetically through the REAL
   AgentLoop → PolicyEngine → ToolRegistry stack, scores 1.0. A competent
   model is expected to score 1.0 on this harness; anything less is
   harness friction or a real regression (this is also the
   non-regression gate's recorded baseline value).
3. DYNAMIC RANGE — the gate detects regressions: a crippled suite
   (consent removed → WRITE/EXECUTE fail closed) must score well below
   1.0 and FAIL check_regression against the baseline; a dropped
   baseline category must also fail. A gate that passes everything is
   theater (research/05 §7).

Plus dashboard pins: markdown rendering, verdict JSON, baseline update,
and the live-trend append.
"""

from __future__ import annotations

import json

import pytest

from evals.dashboard import (
    check_regression,
    load_baseline,
    render_markdown,
    update_baseline,
)
from evals.suite import (
    CATEGORIES,
    TASKS_PER_CATEGORY,
    Bench,
    HarnessTask,
    LoopTask,
    build_suite,
    run_suite,
)

# ------------------------------------------------------- corpus integrity --


def test_suite_has_exactly_50_tasks() -> None:
    tasks = build_suite()
    assert len(tasks) == 50


def test_five_categories_ten_each_unique_ids() -> None:
    tasks = build_suite()
    ids = [t.id for t in tasks]
    assert len(set(ids)) == 50, "duplicate task ids"
    for category in CATEGORIES:
        subset = [t for t in tasks if t.category == category]
        assert len(subset) == TASKS_PER_CATEGORY, (
            f"category {category!r} has {len(subset)} tasks, "
            f"expected {TASKS_PER_CATEGORY}")


def test_every_task_id_is_short_and_stable() -> None:
    # ids follow the f1…f10/m…/w…/c…/o… convention — scorecards and the
    # baseline reference them, so the shape is contract
    tasks = build_suite()
    prefixes = {"files": "f", "memory": "m", "web": "w", "coding": "c",
                "orchestration": "o"}
    for task in tasks:
        prefix = prefixes[task.category]
        assert task.id.startswith(prefix), f"{task.id} vs {prefix}"
        suffix = task.id[len(prefix):]
        assert suffix.isdigit() and 1 <= int(suffix) <= 10, (
            f"malformed task id {task.id!r}")


def test_every_loop_task_has_script_and_verifier() -> None:
    tasks = build_suite()
    loop_tasks = [t for t in tasks if isinstance(t, LoopTask)]
    # 39 model-driven tasks (w10 + o1…o10 are harness-driven)
    assert len(loop_tasks) == 39
    for task in loop_tasks:
        assert task.prompt.strip(), f"{task.id} has an empty prompt"
        assert task.script, f"{task.id} has no scripted turns"
        assert callable(task.verify), f"{task.id} has no verifier"
        for turn in task.script:
            assert turn.text or turn.tool_calls, (
                f"{task.id} has an empty scripted turn")


def test_harness_tasks_are_async_callables() -> None:
    harness = [t for t in build_suite() if isinstance(t, HarnessTask)]
    # 11 harness-driven: w10 consent gate + the 10 orchestration checks
    assert len(harness) == 11
    for task in harness:
        assert callable(task.run) and task.title.strip()


# --------------------------------------------------------- scripted floor --


@pytest.fixture()
def bench_root(tmp_path):
    yield tmp_path


def test_scripted_suite_scores_full_marks(bench_root) -> None:
    """THE floor: the harness runs 50/50 hermetically. This is the value the
    tracked baseline pins (evals/baseline.json) — the regression gate fails
    on any drop from it."""
    report = __import__("asyncio").run(
        run_suite(mode="scripted", root=bench_root))
    failures = [r for r in report.results if not r.ok]
    assert report.score == 1.0, (
        f"scripted suite regressed: {[(r.id, r.detail) for r in failures]}")


def test_tracked_baseline_matches_full_marks() -> None:
    """evals/baseline.json is the recorded gate: it must equal the scripted
    floor (1.0 overall and per category). If an intentional change moves the
    floor, update the baseline IN THE SAME COMMIT with --update-baseline."""
    baseline = load_baseline()
    assert baseline.get("mode") == "scripted"
    assert baseline.get("score") == 1.0
    for category in CATEGORIES:
        assert baseline.get("categories", {}).get(category) == 1.0


# -------------------------------------------------------- dynamic range ----


def test_gate_detects_crippled_suite(bench_root) -> None:
    """consent_none=True → WRITE/EXECUTE fail closed (the policy choke
    point's fail-safe), so the note/coding tasks must collapse: the gate
    must FAIL. A non-regression gate that passes a crippled harness is
    theater."""
    import asyncio

    report = asyncio.run(run_suite(mode="scripted", root=bench_root / "cripple",
                                    consent_none=True))
    assert report.score < 0.9, (
        f"crippled suite scored {report.score:.2f} — the suite cannot "
        "detect a broken consent path, so it gates nothing")
    assert report.category_score("files") < 1.0
    assert report.category_score("coding") < 1.0
    baseline = load_baseline()
    verdict = check_regression(report.summary(), baseline)
    assert not verdict.passed
    assert verdict.overall_delta < 0


def test_gate_detects_single_category_regression() -> None:
    """A memory-only drop must fail the gate even when the overall score
    looks healthy (per-subsystem scorecards are the point)."""
    summary = {"score": 1.0, "categories": {
        "files": 1.0, "memory": 0.5, "web": 1.0, "coding": 1.0,
        "orchestration": 1.0}}
    baseline = {"score": 1.0, "categories": {
        "files": 1.0, "memory": 1.0, "web": 1.0, "coding": 1.0,
        "orchestration": 1.0}}
    verdict = check_regression(summary, baseline)
    assert not verdict.passed
    assert "memory" in " ".join(verdict.regressions)


def test_gate_allows_plateau_and_partial_runs() -> None:
    """Equal-to-baseline passes (a plateau is not a drop); a partial run is
    judged only on the categories it ran."""
    baseline = {"score": 1.0, "categories": {c: 1.0 for c in CATEGORIES}}
    plateau = {"score": 1.0, "categories": {
        "files": 1.0, "memory": 1.0, "web": 1.0, "coding": 1.0,
        "orchestration": 1.0}}
    assert check_regression(plateau, baseline).passed
    partial = {"score": 1.0, "categories": {"files": 1.0}}
    assert check_regression(partial, baseline).passed
    better = {"score": 1.0, "categories": {
        "files": 1.0, "memory": 1.0, "web": 1.0, "coding": 1.0,
        "orchestration": 1.0}}
    assert check_regression(better, baseline).passed


# ----------------------------------------------------------- dashboard -----


def test_dashboard_markdown_and_verdict_json(bench_root) -> None:
    import asyncio

    report = asyncio.run(run_suite(mode="scripted",
                                   root=bench_root / "dash",
                                   categories=["files"]))
    summary = report.summary()
    text = render_markdown(summary, None)
    assert "# ULTRON Benchmark Dashboard" in text
    assert "| files | 100% |" in text
    verdict = check_regression(summary, {"score": 1.0, "categories": {
        "files": 1.0}})
    assert verdict.passed
    assert verdict.summary()["overall"]["after"] == 1.0


def test_baseline_update_roundtrip(bench_root) -> None:
    summary = {"mode": "scripted", "score": 0.98, "tasks": 50, "passed": 49,
               "categories": {"files": 1.0, "memory": 0.9, "web": 1.0,
                              "coding": 1.0, "orchestration": 1.0}}
    path = bench_root / "new_baseline.json"
    update_baseline(summary, path)
    recorded = json.loads(path.read_text(encoding="utf-8"))
    assert recorded["score"] == 0.98
    # the recorded (lower) baseline now passes a 0.98 run but fails 1.0→? no:
    # equal passes, only DROPS fail
    verdict = check_regression(summary, recorded)
    assert verdict.passed


def test_live_trend_append_only(bench_root) -> None:
    import time

    from evals.dashboard import record_live_trend

    path = bench_root / "trend.json"
    # fixture model name: recorded trend data, never a runtime model choice
    first = {"mode": "live", "provider": "ollama", "model": "fixture-model",
             "score": 0.9, "categories": {"files": 1.0}}
    record_live_trend(first, path)
    time.sleep(0.01)  # distinct ts
    second = {"mode": "live", "provider": "ollama", "model": "fixture-model",
              "score": 0.92, "categories": {"files": 1.0}}
    record_live_trend(second, path)
    trend = json.loads(path.read_text(encoding="utf-8"))
    assert [t["score"] for t in trend] == [0.9, 0.92]
    assert trend[1]["model"] == "fixture-model"


# --------------------------------------------------------- bench helpers ---


def test_bench_fixtures_reproducible(bench_root) -> None:
    """Two Bench instances over the same root produce identical fixtures —
    task-independence relies on it (the phase1-gate lesson)."""
    a = Bench(bench_root / "a")
    try:
        assert a.note("poem") == "one two three four"
        assert a.note("protected") == "do not delete"
        assert a.note("numbers") == "5 7 10"
        assert a.ws_file("data.txt") == "the quick brown fox"
        b = Bench(bench_root / "b")
        try:
            assert b.note("poem") == a.note("poem")
            assert b.ws_file("data.txt") == a.ws_file("data.txt")
            page = b.fetch("https://p5.local/fusion")
            assert page[0] == 200 and "net gain" in page[1]
            assert b.first_url("FUSION energy") == "https://p5.local/fusion"
        finally:
            b.engine.close()
    finally:
        a.engine.close()
