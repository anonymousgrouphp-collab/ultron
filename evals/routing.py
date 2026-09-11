"""evals/routing.py — Phase A5: model routing by task complexity (NSM half).

The routing POLICY and its eval harness live here — pure functions, no
product wiring. The live-product integration (routing spoken/typed commands
through this policy before gateway construction) touches main.py and lands
post-W (board: A5 row, "live-product wiring = post-W").

Policy shape: a user command is classified simple|medium|complex, and the
tier maps to a provider through kernel.diagnostics.cost_tracker.CostTracker's
existing `suggest_provider` table (one implementation — no second cost model):
    simple  → ollama (free, local)
    medium  → gemini (default quality)
    complex → gemini
    budget exceeded (any tier) → ollama (fail-safe to free)

The eval harness replays a labeled corpus of spoken-style commands, scores
the classifier's accuracy, and renders a markdown report. Deterministic by
construction; `tests/test_routing_policy.py` pins corpus accuracy at 1.0 so a
policy change that breaks a labeled command fails CI.

Usage:
    python evals/routing.py [--report-out FILE]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from kernel.diagnostics.cost_tracker import CostTracker  # noqa: E402

RESULTS_PATH = BASE / "evals" / "results" / "routing_eval.json"
TIERS = ("simple", "medium", "complex")

_COMPLEX_MARKERS = (
    # multi-step connectives / sequencing
    " and then ", " after that ", " then ", " first ", " afterwards ",
    # product-style heavy intents
    "research", "summarize", "summarise", "write a file", "save a file",
    "write a report", "briefing", "compare", "in the background",
    "every day", "every morning", "organize", "organise",
)
_SIMPLE_MARKERS = (
    "what time", "what's the time", "open ", "close ", "play ", "pause",
    "stop", "volume", "remind me in", "what is", "who is", "battery",
)


def classify_complexity(text: str) -> str:
    """Heuristic command classifier (pure, deterministic).

    complex wins over simple when both match (a 'research X and then open Y'
    command is complex regardless of its 'open ' prefix); anything else is
    medium. Word count backs the markers: long commands carry structure.
    """
    lowered = " " + re.sub(r"\s+", " ", text.strip().lower()) + " "
    has_complex = any(m in lowered for m in _COMPLEX_MARKERS)
    has_simple = any(m in lowered for m in _SIMPLE_MARKERS)
    words = len(lowered.split())
    if has_complex or words >= 14:
        return "complex"
    if has_simple and words <= 8:
        return "simple"
    return "medium"


@dataclass(frozen=True)
class RoutingDecision:
    """One routing outcome — the shape the (post-W) wiring layer will consume."""

    text: str
    complexity: str
    provider: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {"text": self.text, "complexity": self.complexity,
                "provider": self.provider, "reason": self.reason}


def route_task(
    text: str,
    *,
    tracker: CostTracker | None = None,
    suggest: Callable[[str], str] | None = None,
) -> RoutingDecision:
    """Classify + map to a provider via CostTracker.suggest_provider.

    `tracker` (preferred) routes through a live tracker so budget state
    shapes the decision (exceeded → ollama); `suggest` overrides the mapping
    entirely for tests; the default is a fresh tracker (no budget → tier
    mapping as documented in the module docstring).
    """
    complexity = classify_complexity(text)
    reason = f"tier={complexity}"
    if suggest is not None:
        provider = suggest(complexity)
        reason += " (injected mapping)"
    else:
        active = tracker if tracker is not None else CostTracker()
        provider = active.suggest_provider(complexity)
        ok, message = active.check_budget()
        if not ok:
            reason += f" (budget exceeded → free local provider: {message})"
    return RoutingDecision(text=text, complexity=complexity,
                           provider=provider, reason=reason)


# ----------------------------------------------------------------- corpus ---

# Labeled spoken-style commands (fictional, product-shaped). (text, expected)
ROUTING_CORPUS: tuple[tuple[str, str], ...] = (
    ("what time is it", "simple"),
    ("open the dashboard", "simple"),
    ("play some jazz", "simple"),
    ("remind me in 10 minutes to stretch", "simple"),
    ("what's the battery level", "simple"),
    ("stop the music", "simple"),
    ("remember that my wifi password is hunter2", "medium"),
    ("what did I say about the bike shop", "medium"),
    ("read me my notes", "medium"),
    ("what's on my calendar today", "medium"),
    ("set a reminder for tomorrow at nine", "medium"),
    ("remember that Dana's birthday is in March", "medium"),
    ("who is Dana", "simple"),
    ("research fusion energy and save a summary file", "complex"),
    ("compare the bikes page with my notes and then write a report", "complex"),
    ("research the best commuter bikes, summarize the results, then save a file to my desktop", "complex"),
    ("run a morning briefing every day in the background", "complex"),
    ("after that, write a file with the fusion summary and then organize my desktop", "complex"),
    ("look up the Spoke and Pedal shop online and then summarize what they offer and write it down", "complex"),
)


def eval_routing(
    corpus: tuple[tuple[str, str], ...] = ROUTING_CORPUS,
) -> dict[str, Any]:
    """Score the policy over the labeled corpus; returns the eval record."""
    per_tier = {tier: {"total": 0, "correct": 0} for tier in TIERS}
    misses: list[dict[str, str]] = []
    for text, expected in corpus:
        got = classify_complexity(text)
        per_tier[expected]["total"] += 1
        if got == expected:
            per_tier[expected]["correct"] += 1
        else:
            misses.append({"text": text, "expected": expected, "got": got})
    total = len(corpus)
    correct = total - len(misses)
    return {
        "tasks": total,
        "correct": correct,
        "accuracy": round(correct / total, 4) if total else 0.0,
        "per_tier": per_tier,
        "misses": misses,
        "routes": {text: route_task(text).provider
                   for text, _ in corpus},
    }


def render_report(record: dict[str, Any]) -> str:
    """Markdown report for the routing eval (artifact / dashboard input)."""
    lines = [
        "# ULTRON Model-Routing Policy Eval",
        "",
        f"- **Accuracy**: {record['correct']}/{record['tasks']} "
        f"(**{record['accuracy']:.0%}**) over the labeled corpus",
        "",
        "| Tier | Correct | Total |",
        "|---|---|---|",
    ]
    for tier in TIERS:
        t = record["per_tier"][tier]
        lines.append(f"| {tier} | {t['correct']} | {t['total']} |")
    if record["misses"]:
        lines += ["", "## Misses", ""]
        for m in record["misses"]:
            lines.append(f"- `{m['text']}` — expected **{m['expected']}**, "
                         f"got **{m['got']}**")
    lines += ["", "> Routing = classify_complexity → CostTracker."
              "suggest_provider. Live-product wiring lands post-W (A5 row).",
              ""]
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--report-out", default=None,
                        help="write the markdown report to this file")
    args = parser.parse_args()

    record = eval_routing()
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(render_report(record))
    print(f"record: {RESULTS_PATH}")
    if args.report_out:
        Path(args.report_out).write_text(render_report(record),
                                         encoding="utf-8")
        print(f"report: {args.report_out}")


if __name__ == "__main__":
    main()
