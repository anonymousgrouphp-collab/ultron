"""Hermetic tests for evals/trend.py (R6 — live-benchmark trend recording).

No network, no git: record/load/render are pure file + dict operations and
the suite-run path is stubbed by feeding a report file instead.
"""

from __future__ import annotations

import json
from pathlib import Path

import evals.trend as trend


def _summary(score: float = 0.8, provider: str = "openai") -> dict:
    return {
        "mode": "live",
        "provider": provider,
        "model": "gpt-4o-mini",
        "score": score,
        "passed": 40,
        "tasks": 50,
        "partial": False,
        "categories": {"files": score, "memory": score, "web": score,
                       "coding": score, "orchestration": score},
    }


def test_record_appends_jsonl(tmp_path: Path):
    path = tmp_path / "live_trend.jsonl"
    row = trend.record(_summary(), path=path, ref="abc123")
    assert row["ref"] == "abc123"
    assert row["provider"] == "openai"
    assert row["score"] == 0.8
    assert path.exists()
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["model"] == "gpt-4o-mini"


def test_record_and_load_round_trip(tmp_path: Path):
    path = tmp_path / "live_trend.jsonl"
    trend.record(_summary(0.7), path=path, ref="a")
    trend.record(_summary(0.9), path=path, ref="b")
    rows = trend.load_trend(path)
    assert [r["ref"] for r in rows] == ["a", "b"]
    assert [r["score"] for r in rows] == [0.7, 0.9]


def test_load_tolerates_torn_tail(tmp_path: Path):
    path = tmp_path / "live_trend.jsonl"
    path.write_text(json.dumps({"ref": "a", "score": 0.7}) + "\n"
                    + '{"ref": "b", "scor', encoding="utf-8")
    rows = trend.load_trend(path)
    assert len(rows) == 1
    assert rows[0]["ref"] == "a"


def test_render_report_shows_release_over_release(tmp_path: Path):
    path = tmp_path / "live_trend.jsonl"
    trend.record(_summary(0.7), path=path, ref="old")
    rows = trend.load_trend(path)
    new_row = trend.record(_summary(0.85), path=path, ref="new")
    report = trend.render_report(new_row, rows + [new_row])
    assert "Release-over-release" in report
    assert "+15.0%" in report  # 0.85 - 0.70
    assert "old → new" in report


def test_report_file_path_is_fail_soft(tmp_path: Path, capsys):
    missing = tmp_path / "nope.json"
    rc = trend._record_report_file(missing)
    assert rc == 0  # never gates
    out = capsys.readouterr().out
    assert "⚠️" in out


def test_cli_needs_provider():
    try:
        trend.main()
    except SystemExit as exc:
        assert exc.code != 0  # argparse error
    else:
        raise AssertionError("trend.main() without --provider must exit non-zero")
