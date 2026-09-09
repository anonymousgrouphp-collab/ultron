"""evals/suite.py — P5-A: the 50-task benchmark suite (roadmap §4 Phase 5,
research/05 §7). The North-Star Metric harness.

Categories (per-subsystem scorecards, 10 tasks each):
    files         — note tools through the kernel (create/read/list/copy, sums,
                    overwrite, missing-file recovery, DESTRUCTIVE refusal)
    memory        — multi-step recall over a seeded MemoryEngine (single-hop,
                    multi-hop, counts, negation, memory→file pipelines)
    web           — research over an injectable canned web corpus (titles,
                    pipelines, 404/invalid-URL recovery, consent gate)
    coding        — P2-E workspace tools incl. write→run→observe→fix loops
    orchestration — P2-C queue/orchestrator/subagent subsystem checks

Two runners, one corpus + one verifier set:
    --scripted        every model turn is a canned Response popped by a
                      task-local fake gateway — the REAL AgentLoop →
                      PolicyEngine → ToolRegistry stack still executes every
                      tool call, so this mode deterministically measures the
                      HARNESS (loop wiring, policy gating, tools, verifiers)
                      in hermetic CI. A competent model scores 1.0; anything
                      less is harness friction.
    --provider ollama|gemini   live model via the P1-C gateway (operator or
                      periodic run; model variance means this lands on the
                      dashboard, not the CI gate).

Scripted verifiers wherever a task has a checkable end-state (research/05 §7);
the regression gate itself lives in evals/dashboard.py against the tracked
evals/baseline.json ("the 50-task success rate may not drop" — roadmap NSM).
This is an eval script, not kernel code: config/loader is imported here by
design (live mode only), exactly like evals/phase1_gate.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import shutil
import sys
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any
BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from kernel.gateway import Message, Response  # noqa: E402
from kernel.loop import AgentLoop, LoopResult  # noqa: E402
from kernel.memory import (  # noqa: E402
    HashingEmbedder,
    MemoryEngine,
    register_memory_tools,
)
from kernel.orchestrator import JobQueue, Orchestrator  # noqa: E402
from kernel.policy import AuditLog, PolicyEngine  # noqa: E402
from kernel.tools import ToolRegistry  # noqa: E402
from kernel.types import RiskClass, ToolCall, ToolResult  # noqa: E402

CATEGORIES = ("files", "memory", "web", "coding", "orchestration")
TASKS_PER_CATEGORY = 10
MAX_STEPS = 12

DEFAULT_ROOT = BASE / ".ultron" / "eval" / "benchmark"

# ----------------------------------------------------------------- model ---

_NEGATIVE_HINTS = (
    "not exist", "no note", "no such", "does not exist", "doesn't exist",
    "not found", "couldn't", "could not", "failed", "invalid", "not valid",
    "refused", "denied", "not allowed", "disabled", "unavailable",
    "escape", "escapes", "jailed", "error",
)


def _lower(text: str) -> str:
    return text.lower()


def _mentions(text: str, *needles: str) -> bool:
    lowered = _lower(text)
    return all(n.lower() in lowered for n in needles)


def _mentions_absence(text: str, *needles: str) -> bool:
    """The answer acknowledges something is missing/refused — the honest
    failure-reporting pin (never exact wording)."""
    lowered = _lower(text)
    return _mentions(text, *needles) and any(h in lowered
                                             for h in _NEGATIVE_HINTS)


@dataclass
class TaskScriptedGateway:
    """Fake Completer for one task: pops canned turns in order. A malformed
    script (too few turns) surfaces as a task failure, never a silent pass."""

    turns: list[Response]

    async def complete(self, messages, tools=(), response_schema=None):
        return self.turns.pop(0)


def _call_turn(*calls: tuple[str, dict[str, Any]]) -> Response:
    return Response(
        text="",
        tool_calls=tuple(
            ToolCall(id=f"script-{i}-{name}", name=name, args=args,
                     source="model")
            for i, (name, args) in enumerate(calls, 1)
        ),
        finish="tool_calls" if calls else "stop",
    )


def _text_turn(text: str) -> Response:
    return Response(text=text, finish="stop")


# ------------------------------------------------------------------ bench ---


class Bench:
    """Run-scoped resources: one notes sandbox, one seeded memory engine, one
    coding workspace, the canned web corpus. Fixtures reset what a task needs
    so every task is independently re-runnable (the phase1-gate lesson)."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.notes = root / "notes"
        self.workspace = root / "workspace"
        self.engine = MemoryEngine(root / "memory.sqlite3",
                                    embedder=HashingEmbedder())
        self.fetch_log: list[str] = []
        self.pages: dict[str, tuple[int, str]] = {
            "https://p5.local/fusion": (200, "<html><head><title>Fusion "
             "energy: net gain achieved</title></head><body><script>x()"
             "</script><p>Researchers report a sustained net energy gain."
             "</p></body></html>"),
            "https://p5.local/bikes": (200, "<html><head><title>Commuter "
             "bikes of 2026</title></head><body><p>The Spoke &amp; Pedal "
             "shop services commuter bikes weekly.</p></body></html>"),
            "https://p5.local/plants": (200, "<html><head><title>House "
             "plants for dim rooms</title></head><body><p>Pothos and snake "
             "plants tolerate low light.</p></body></html>"),
            "https://p5.local/missing": (404, "<html><body>gone</body></html>"),
        }
        self.search_map = {
            "fusion": "https://p5.local/fusion",
            "bike": "https://p5.local/bikes",
            "plant": "https://p5.local/plants",
        }
        self.seed()

    # -- fixtures -----------------------------------------------------------

    def seed(self) -> None:
        self.notes.mkdir(parents=True, exist_ok=True)
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.reset_notes()
        self.reset_workspace()
        for content, entity, key in (
            ("The user's favorite editor is Neovim", "preferences",
             "fav_editor"),
            ("Project ULTRON is a personal agent harness with a kernel "
             "architecture", "projects", "project"),
            ("The user lives in Portland, Oregon", "home", "city"),
            ("Dana recommended the Spoke & Pedal bike shop for tune-ups",
             "friends", "bike_shop"),
            ("The user is allergic to shellfish and avoids seafood",
             "health", "diet"),
            ("The user bought a used Fujifilm camera for weekend photography",
             "gear", "camera"),
            ("The user works as a product designer at Fernwood Studio",
             "work", "job"),
            ("The user was promoted to senior product designer in June",
             "work", "promotion"),
            ("The user switched from coffee to green tea in February",
             "drinks", "tea"),
            ("The user swims laps twice a week at the Community Pool",
             "exercise", "swim"),
        ):
            self.engine.remember(content, entity=entity, topic=key,
                                 source_ref=f"eval:P5:{key}")

    def reset_notes(self) -> None:
        for old in self.notes.glob("*.txt"):
            old.unlink()
        (self.notes / "poem.txt").write_text("one two three four",
                                             encoding="utf-8")
        (self.notes / "protected.txt").write_text("do not delete",
                                                   encoding="utf-8")
        (self.notes / "numbers.txt").write_text("5 7 10", encoding="utf-8")
        (self.notes / "mixed.txt").write_text("alpha beta gamma delta",
                                              encoding="utf-8")

    def reset_workspace(self) -> None:
        if self.workspace.exists():
            shutil.rmtree(self.workspace)
        self.workspace.mkdir(parents=True, exist_ok=True)
        (self.workspace / "data.txt").write_text("the quick brown fox",
                                                 encoding="utf-8")
        (self.workspace / "notes.md").write_text("# workspace\nseed",
                                                 encoding="utf-8")

    # -- helpers ------------------------------------------------------------

    def note(self, name: str) -> str:
        path = self.notes / f"{name}.txt"
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def has_note(self, name: str, *needles: str) -> bool:
        content = _lower(self.note(name))
        return bool(content) and all(n.lower() in content for n in needles)

    def ws_file(self, name: str) -> str:
        path = self.workspace / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    def fetch(self, url: str) -> tuple[int, str]:
        self.fetch_log.append(url)
        page = self.pages.get(url)
        if page is None:
            return 404, "<html><body>not found</body></html>"
        return page

    def first_url(self, query: str) -> str:
        lowered = _lower(query)
        for keyword, url in self.search_map.items():
            if keyword in lowered:
                return url
        return "https://p5.local/fusion"


# ------------------------------------------------------------------ task ---


@dataclass(frozen=True)
class LoopTask:
    """A model-driven task: prompt + canned scripted turns + scripted
    verifier over the end-state (bench) and the run (LoopResult)."""

    id: str
    category: str
    prompt: str
    script: tuple[Response, ...]
    verify: Callable[[Bench, LoopResult], tuple[bool, str]]


@dataclass(frozen=True)
class HarnessTask:
    """A subsystem-driven task: runs the kernel directly (no model turn),
    scored identically — the orchestration scorecard measures the
    orchestrator, not a model's way with words."""

    id: str
    category: str
    title: str
    run: Callable[[Bench], Awaitable[tuple[bool, str]]]


Task = LoopTask | HarnessTask


@dataclass(frozen=True)
class TaskResult:
    id: str
    category: str
    ok: bool
    detail: str
    finish: str
    steps: int

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "category": self.category, "ok": self.ok,
                "detail": self.detail, "finish": self.finish,
                "steps": self.steps}


@dataclass(frozen=True)
class SuiteReport:
    mode: str
    provider: str
    model: str
    results: tuple[TaskResult, ...]
    partial: bool = False

    @property
    def score(self) -> float:
        return (sum(r.ok for r in self.results) / len(self.results)
                if self.results else 0.0)

    def category_score(self, category: str) -> float:
        subset = [r for r in self.results if r.category == category]
        return (sum(r.ok for r in subset) / len(subset)) if subset else 0.0

    def by_category(self) -> dict[str, float]:
        return {c: round(self.category_score(c), 4) for c in CATEGORIES
                if any(r.category == c for r in self.results)}

    def summary(self) -> dict[str, Any]:
        return {
            "mode": self.mode, "provider": self.provider, "model": self.model,
            "tasks": len(self.results),
            "passed": sum(r.ok for r in self.results),
            "score": round(self.score, 4),
            "partial": self.partial,
            "categories": self.by_category(),
            "results": [r.to_dict() for r in self.results],
        }


# --------------------------------------------------------------- corpus ----


def _files_tasks() -> list[LoopTask]:
    def t(tid: str, prompt: str, script: tuple[Response, ...],
          verify: Callable[[Bench, LoopResult], tuple[bool, str]]
          ) -> LoopTask:
        return LoopTask(tid, "files", prompt, script, verify)

    return [
        t("f1", "Create a note named 'alpha' with the text 'hello world', "
                "then read it back and report its content.",
          (_call_turn(("write_note", {"name": "alpha",
                                      "text": "hello world"})),
           _call_turn(("read_note", {"name": "alpha"})),
           _text_turn("The alpha note contains: hello world")),
          lambda b, r: (b.has_note("alpha", "hello world"),
                        "alpha note exists with the text")),
        t("f2", "Create two notes: 'x' containing 'red' and 'y' containing "
                "'blue'. Then list all notes and report what you created.",
          (_call_turn(("write_note", {"name": "x", "text": "red"}),
                      ("write_note", {"name": "y", "text": "blue"})),
           _call_turn(("list_notes", {})),
           _text_turn("I created note x with red and note y with blue.")),
          lambda b, r: (b.has_note("x", "red") and b.has_note("y", "blue"),
                        "both notes exist")),
        t("f3", "Read the note 'poem', then create a new note 'poem_copy' "
                "containing exactly the same text you read.",
          (_call_turn(("read_note", {"name": "poem"})),
           _call_turn(("write_note", {"name": "poem_copy",
                                      "text": "one two three four"})),
           _text_turn("I copied the poem into poem_copy.")),
          lambda b, r: (b.has_note("poem_copy", "one two three four"),
                        "copy carries the content")),
        t("f4", "Read the note 'numbers' and tell me the sum of the numbers "
                "it contains.",
          (_call_turn(("read_note", {"name": "numbers"})),
           _text_turn("The numbers are 5, 7 and 10; their sum is 22.")),
          lambda b, r: ("22" in r.text, "sum 22 reported")),
        t("f5", "Write a note 'todo' containing exactly these three items: "
                "'buy milk', 'walk dog', 'read book'. Then read it back to "
                "confirm all three are in it.",
          (_call_turn(("write_note", {"name": "todo",
                                      "text": "buy milk\nwalk dog\n"
                                              "read book"})),
           _call_turn(("read_note", {"name": "todo"})),
           _text_turn("All three items are in the todo note.")),
          lambda b, r: (b.has_note("todo", "buy milk", "walk dog", "read book"),
                        "todo has all three items")),
        t("f6", "Try to delete the note named 'protected' with delete_note. "
                "Whatever happens, report whether the deletion was allowed.",
          (_call_turn(("delete_note", {"name": "protected"})),
           _text_turn("The deletion was not allowed — the note is "
                      "protected by policy.")),
          lambda b, r: (b.note("protected") != ""
                        and _mentions_absence(r.text, "protected", "not"),
                        "protected note intact; refusal reported")),
        t("f7", "Create three notes: 'n1' with '5', 'n2' with '7', 'n3' with "
                "'10'. Read all three and tell me their sum.",
          (_call_turn(("write_note", {"name": "n1", "text": "5"}),
                      ("write_note", {"name": "n2", "text": "7"}),
                      ("write_note", {"name": "n3", "text": "10"})),
           _call_turn(("read_note", {"name": "n1"}),
                      ("read_note", {"name": "n2"}),
                      ("read_note", {"name": "n3"})),
           _text_turn("5 + 7 + 10 = 22.")),
          lambda b, r: (b.has_note("n1", "5") and b.has_note("n2", "7")
                        and b.has_note("n3", "10") and "22" in r.text,
                        "notes exist and sum 22 reported")),
        t("f8", "Read the note 'mixed' and tell me exactly how many words it "
                "contains.",
          (_call_turn(("read_note", {"name": "mixed"})),
           _text_turn("The mixed note contains 4 words.")),
          lambda b, r: ("4" in r.text, "word count 4 reported")),
        t("f9", "Create a note 'a' containing 'draft'. Then overwrite the "
                "same note 'a' with the text 'final version', read it back, "
                "and report the final content.",
          (_call_turn(("write_note", {"name": "a", "text": "draft"})),
           _call_turn(("write_note", {"name": "a",
                                      "text": "final version"})),
           _call_turn(("read_note", {"name": "a"})),
           _text_turn("The final content of note a is: final version.")),
          lambda b, r: (b.has_note("a", "final version")
                        and "draft" not in _lower(b.note("a")),
                        "note overwritten with the final text")),
        t("f10", "Read the note named 'ghost' and report what you find.",
          (_call_turn(("read_note", {"name": "ghost"})),
           _text_turn("There is no note named ghost — it does not exist.")),
          lambda b, r: (_mentions_absence(r.text, "ghost"),
                        "missing note reported honestly")),
    ]


def _memory_tasks() -> list[LoopTask]:
    def t(tid: str, prompt: str, script: tuple[Response, ...],
          verify: Callable[[Bench, LoopResult], tuple[bool, str]]
          ) -> LoopTask:
        return LoopTask(tid, "memory", prompt, script, verify)

    return [
        t("m1", "Search your long-term memory (memory_search) for the user's "
                "favorite editor and tell me which editor it is.",
          (_call_turn(("memory_search", {"query": "favorite editor"})),
           _text_turn("The user's favorite editor is Neovim.")),
          lambda b, r: ("neovim" in _lower(r.text), "answer recalls neovim")),
        t("m2", "Search your memory for facts about the ULTRON project, then "
                "write a note 'findings' containing what you found.",
          (_call_turn(("memory_search", {"query": "ULTRON project"})),
           _call_turn(("write_note", {"name": "findings",
                                      "text": "Project ULTRON is a personal "
                                              "agent harness"})),
           _text_turn("I wrote the ULTRON findings to the findings note.")),
          lambda b, r: (b.has_note("findings", "ultron"),
                        "findings note carries recalled memory")),
        t("m3", "Use memory_page with entity 'work' and tell me how many "
                "facts exist about work.",
          (_call_turn(("memory_page", {"entity": "work", "limit": 20})),
           _text_turn("There are 2 facts about work.")),
          lambda b, r: ("2" in r.text, "work fact count 2 reported")),
        t("m4", "Search your memory for 'underwater basket weaving' and "
                "report what you find.",
          (_call_turn(("memory_search",
                       {"query": "underwater basket weaving"})),
           _text_turn("I found nothing about that in memory.")),
          lambda b, r: (_mentions_absence(r.text, "basket")
                        or "nothing" in _lower(r.text),
                        "empty search reported honestly")),
        t("m5", "Which shop was recommended for bike tune-ups, and who "
                "recommended it? Use memory_search.",
          (_call_turn(("memory_search",
                       {"query": "bike shop tune-up recommendation"})),
           _text_turn("Dana recommended the Spoke & Pedal bike shop.")),
          lambda b, r: (_mentions(r.text, "spoke", "dana"),
                        "both shop and recommender recalled")),
        t("m6", "Search memory for the user's camera, then write a note "
                "'gear_note' listing the camera.",
          (_call_turn(("memory_search", {"query": "user camera"})),
           _call_turn(("write_note", {"name": "gear_note",
                                      "text": "The user has a Fujifilm "
                                              "camera"})),
           _text_turn("The gear note lists the Fujifilm camera.")),
          lambda b, r: (b.has_note("gear_note", "fujifilm"),
                        "camera recalled and written to a note")),
        t("m7", "Use memory_search with k=1 for the user's favorite editor "
                "and report the single top fact.",
          (_call_turn(("memory_search", {"query": "favorite editor",
                                         "k": 1})),
           _text_turn("The top fact: the favorite editor is Neovim.")),
          lambda b, r: ("neovim" in _lower(r.text), "k=1 search answered")),
        t("m8", "Search memory for the user's job and any promotion, and "
                "tell me their current seniority.",
          (_call_turn(("memory_search", {"query": "job promotion work"})),
           _text_turn("The user is a senior product designer at Fernwood "
                      "Studio after a June promotion.")),
          lambda b, r: ("senior" in _lower(r.text),
                        "promotion recall combines into seniority")),
        t("m9", "Search memory for the user's exercise routine, then write "
                "a note 'routine' summarizing it.",
          (_call_turn(("memory_search", {"query": "exercise routine"})),
           _call_turn(("write_note", {"name": "routine",
                                      "text": "The user swims laps twice "
                                              "a week"})),
           _text_turn("The routine note says the user swims twice a week.")),
          lambda b, r: (b.has_note("routine", "swim"),
                        "exercise recall written to a note")),
        t("m10", "Does the user still drink coffee? Search memory and "
                 "answer.",
          (_call_turn(("memory_search", {"query": "coffee drink"})),
           _text_turn("No — the user switched from coffee to green tea in "
                      "February.")),
          lambda b, r: ("green tea" in _lower(r.text),
                        "negation answered via the tea fact")),
    ]


def _web_tasks() -> list[Task]:
    def t(tid: str, prompt: str, script: tuple[Response, ...],
          verify: Callable[[Bench, LoopResult], tuple[bool, str]]
          ) -> LoopTask:
        return LoopTask(tid, "web", prompt, script, verify)

    loop_tasks: list[Task] = [
        t("w1", "Fetch https://p5.local/fusion with web_read and report the "
                "page title.",
          (_call_turn(("web_read", {"url": "https://p5.local/fusion"})),
           _text_turn("The page title is 'Fusion energy: net gain "
                      "achieved'.")),
          lambda b, r: ("net gain" in _lower(r.text), "title reported")),
        t("w2", "Fetch https://p5.local/bikes and https://p5.local/plants "
                "with web_read and report both page titles.",
          (_call_turn(("web_read", {"url": "https://p5.local/bikes"}),
                      ("web_read", {"url": "https://p5.local/plants"})),
           _text_turn("The titles are 'Commuter bikes of 2026' and 'House "
                      "plants for dim rooms'.")),
          lambda b, r: ("bike" in _lower(r.text)
                        and "plant" in _lower(r.text),
                        "both titles reported")),
        t("w3", "Fetch the fusion page, then write a note 'fusion_note' with "
                "the key claim from the page.",
          (_call_turn(("web_read", {"url": "https://p5.local/fusion"})),
           _call_turn(("write_note", {"name": "fusion_note",
                                      "text": "Researchers report a "
                                              "sustained net energy gain"})),
           _text_turn("I saved the net-gain claim to fusion_note.")),
          lambda b, r: (b.has_note("fusion_note", "net energy gain"),
                        "web content landed in a note")),
        t("w4", "Use web_search for 'fusion energy', read the first result "
                "with web_read, and report the page title.",
          (_call_turn(("web_search", {"query": "fusion energy"})),
           _call_turn(("web_read", {"url": "https://p5.local/fusion"})),
           _text_turn("The first result's title is 'Fusion energy: net gain "
                      "achieved'.")),
          lambda b, r: ("net gain" in _lower(r.text),
                        "search→read pipeline reported")),
        t("w5", "Fetch https://p5.local/missing with web_read and report "
                "what happened.",
          (_call_turn(("web_read", {"url": "https://p5.local/missing"})),
           _text_turn("The fetch failed with HTTP 404 — the page could not "
                      "be retrieved.")),
          lambda b, r: ("404" in r.text
                        or _mentions_absence(r.text, "page"),
                        "404 reported honestly")),
        t("w6", "Fetch the plants page and tell me which plants tolerate "
                "low light.",
          (_call_turn(("web_read", {"url": "https://p5.local/plants"})),
           _text_turn("Pothos and snake plants tolerate low light.")),
          lambda b, r: ("pothos" in _lower(r.text), "plants named")),
        t("w7", "Research house plants: web_search 'house plants', web_read "
                "the first result, then write a note 'research_note' with "
                "what you learned.",
          (_call_turn(("web_search", {"query": "house plants"})),
           _call_turn(("web_read", {"url": "https://p5.local/plants"})),
           _call_turn(("write_note", {"name": "research_note",
                                      "text": "Pothos and snake plants "
                                              "tolerate low light"})),
           _text_turn("The research note records the low-light plants.")),
          lambda b, r: (b.has_note("research_note", "pothos"),
                        "search→read→write pipeline produced a note")),
        t("w8", "Fetch the fusion page twice and confirm the title is the "
                "same both times.",
          (_call_turn(("web_read", {"url": "https://p5.local/fusion"}),
                      ("web_read", {"url": "https://p5.local/fusion"})),
           _text_turn("Both fetches returned the same title: 'Fusion "
                      "energy: net gain achieved'.")),
          lambda b, r: ("net gain" in _lower(r.text)
                        and len([c for c in r.tool_calls
                                 if c.name == "web_read"]) == 2,
                        "two fetches, stable title")),
        t("w9", "Fetch ftp://p5.local/files with web_read and report what "
                "happened.",
          (_call_turn(("web_read", {"url": "ftp://p5.local/files"})),
           _text_turn("web_read refused the URL — only http(s) addresses "
                      "are valid, so the fetch did not happen.")),
          lambda b, r: (_mentions_absence(r.text, "url"),
                        "invalid URL reported honestly")),
    ]
    loop_tasks.append(HarnessTask(
        "w10", "web",
        "consent gate: web_read refuses when research is disabled, "
        "executes when consented",
        _web_consent_gate_check))
    return loop_tasks


async def _web_consent_gate_check(bench: Bench) -> tuple[bool, str]:
    """The consent seam (P2-D): disabled → clean refusal; enabled → the
    canned corpus flows. Fail-closed is the pin that matters."""
    from kernel.research import build_research_tools

    async def yes(call: ToolCall, risk: RiskClass) -> bool:
        return True

    async def run_case(enabled: bool) -> ToolResult:
        reg = ToolRegistry()
        build_research_tools(reg, fetch=bench.fetch, enabled=enabled)
        policy = PolicyEngine()
        call = ToolCall(id=f"w10-{enabled}", name="web_read",
                        args={"url": "https://p5.local/fusion"},
                        source="bench")
        return await policy.run(call, reg, consent=yes)

    refused = await run_case(False)
    allowed = await run_case(True)
    ok = (not refused.ok and "disabled" in (refused.error or "")
          and allowed.ok
          and "net gain" in str(allowed.data).lower())
    return ok, (f"refusal={refused.ok} error={refused.error!r}; "
                f"enabled ok={allowed.ok}")


def _coding_tasks() -> list[LoopTask]:
    python = sys.executable

    def t(tid: str, prompt: str, script: tuple[Response, ...],
          verify: Callable[[Bench, LoopResult], tuple[bool, str]]
          ) -> LoopTask:
        return LoopTask(tid, "coding", prompt, script, verify)

    return [
        t("c1", "Use list_files in the coding workspace and tell me how many "
                "files there are.",
          (_call_turn(("list_files", {})),
           _text_turn("The workspace has 2 files: data.txt and notes.md.")),
          lambda b, r: ("2" in r.text, "file count 2 reported")),
        t("c2", "Read 'data.txt' in the coding workspace and report its "
                "content.",
          (_call_turn(("read_file", {"path": "data.txt"})),
           _text_turn("data.txt contains: the quick brown fox.")),
          lambda b, r: ("fox" in _lower(r.text), "file content reported")),
        t("c3", "Write 'out.txt' in the workspace with the text 'benchmark "
                "marker'.",
          (_call_turn(("write_file", {"path": "out.txt",
                                      "text": "benchmark marker"})),
           _text_turn("I wrote out.txt with the benchmark marker.")),
          lambda b, r: ("benchmark marker" in b.ws_file("out.txt"),
                        "marker file written")),
        t("c4", "Write 'round.txt' with 'persisted payload', then read it "
                "back and report what it contains.",
          (_call_turn(("write_file", {"path": "round.txt",
                                      "text": "persisted payload"})),
           _call_turn(("read_file", {"path": "round.txt"})),
           _text_turn("round.txt contains: persisted payload.")),
          lambda b, r: ("persisted payload" in b.ws_file("round.txt"),
                        "write→read roundtrip verified")),
        t("c5", "Write 'calc.py' that prints 6*7, run it with run_command, "
                "and report the output.",
          (_call_turn(("write_file", {"path": "calc.py",
                                      "text": "print(6*7)"})),
           _call_turn(("run_command", {"argv": [python, "calc.py"]})),
           _text_turn("The program printed 42.")),
          lambda b, r: ("6*7" in b.ws_file("calc.py")
                        and "42" in r.text,
                        "script written, run, output reported")),
        t("c6", "Write 'fixme.py' containing 'print(6/0)', run it, observe "
                "the error, fix the file to 'print(6*7)', run it again, and "
                "report the final output.",
          (_call_turn(("write_file", {"path": "fixme.py",
                                      "text": "print(6/0)"})),
           _call_turn(("run_command", {"argv": [python, "fixme.py"]})),
           _call_turn(("write_file", {"path": "fixme.py",
                                      "text": "print(6*7)"})),
           _call_turn(("run_command", {"argv": [python, "fixme.py"]})),
           _text_turn("The fixed program prints 42.")),
          lambda b, r: ("6*7" in b.ws_file("fixme.py") and "42" in r.text,
                        "write→run→observe→fix→run loop closed")),
        t("c7", "Run this with run_command: a python one-liner printing "
                "2+2, and report the output.",
          (_call_turn(("run_command",
                       {"argv": [python, "-c", "print(2+2)"]})),
           _text_turn("The output is 4.")),
          lambda b, r: ("4" in r.text, "one-liner output reported")),
        t("c8", "Try to install a package with run_command "
                "([python, -m, pip, install, requests]) and report what "
                "happens.",
          (_call_turn(("run_command",
                       {"argv": [python, "-m", "pip", "install",
                                 "requests"]})),
           _text_turn("Package installation is disabled in the coding "
                      "sandbox — the install did not run.")),
          lambda b, r: (_mentions_absence(r.text, "install"),
                        "pip-install refusal reported")),
        t("c9", "Try to read '../escape.txt' with read_file and report what "
                "happens.",
          (_call_turn(("read_file", {"path": "../escape.txt"})),
           _text_turn("The read was refused — the path escapes the coding "
                      "workspace jail.")),
          lambda b, r: (_mentions_absence(r.text, "escape"),
                        "jail-escape refusal reported")),
        t("c10", "Write 'a.txt' with 'first' and 'b.txt' with 'second' in "
                 "the workspace, then list the files and report both.",
          (_call_turn(("write_file", {"path": "a.txt", "text": "first"}),
                      ("write_file", {"path": "b.txt", "text": "second"})),
           _call_turn(("list_files", {})),
           _text_turn("Both a.txt and b.txt are now in the workspace.")),
          lambda b, r: (b.ws_file("a.txt") == "first"
                        and b.ws_file("b.txt") == "second",
                        "both files written")),
    ]


def _orchestration_tasks() -> list[HarnessTask]:
    """Direct subsystem checks over the REAL JobQueue + Orchestrator (P2-C);
    each builds its own throwaway queue so tasks are independent."""
    from kernel.bus import EventBus
    from kernel.orchestrator import scoped_registry, spawn_subagent
    from kernel.orchestrator.plans import steps_from_payload

    def _fresh_queue(path: Path) -> JobQueue:
        """A guaranteed-empty queue DB per run (re-run independence): a stale
        file from an earlier run would leak jobs into this one."""
        for suffix in ("", "-wal", "-shm"):
            stale = Path(str(path) + suffix)
            stale.unlink(missing_ok=True)
        return JobQueue(path)

    def reg_with_tick() -> tuple[ToolRegistry, list[str]]:
        reg = ToolRegistry()
        log: list[str] = []

        @reg.tool(name="tick", description="Append a marker.",
                  parameters={"type": "object",
                              "properties": {"mark": {"type": "string"}},
                              "required": ["mark"]},
                  risk=RiskClass.WRITE)
        def tick(call):
            log.append(str(call.args.get("mark", "")))
            return {"appended": str(call.args.get("mark", ""))}

        @reg.tool(name="tock", description="A second marker tool.",
                  parameters={"type": "object",
                              "properties": {"mark": {"type": "string"}},
                              "required": ["mark"]},
                  risk=RiskClass.WRITE)
        def tock(call):
            log.append(f"tock:{call.args.get('mark', '')}")
            return {"appended": str(call.args.get("mark", ""))}

        @reg.tool(name="produce", description="Return a value.",
                  parameters={"type": "object",
                              "properties": {"value": {"type": "integer"}},
                              "required": ["value"]},
                  risk=RiskClass.READ)
        def produce(call):
            return {"value": int(call.args.get("value", 0))}

        @reg.tool(name="boom", description="Always fails (expected).",
                  parameters={"type": "object", "properties": {}},
                  risk=RiskClass.READ)
        def boom(call):
            return ToolResult.fail(call, "boom: expected failure")

        return reg, log

    async def run_plan(bench: Bench, tag: str, plan: list[dict[str, Any]],
                       *, gateway=None, kinds: dict | None = None,
                       max_attempts: int = 1) -> tuple[Any, Any, list[str]]:
        reg, log = reg_with_tick()
        queue = _fresh_queue(bench.root / f"queue-{tag}.db")
        policy = PolicyEngine()

        async def yes(call: ToolCall, risk: RiskClass) -> bool:
            return True

        orch = Orchestrator(queue, reg, policy, gateway=gateway,
                            consent=yes, source=f"bench-{tag}")
        if kinds:
            for kind, handler in kinds.items():
                orch.register_kind(kind, handler)
        jid = queue.enqueue("plan", {"plan": plan}, title=tag,
                            max_attempts=max_attempts)
        await orch.run_worker(worker=f"w-{tag}", max_jobs=1)
        return queue.get(jid), queue, log

    async def o1(bench: Bench) -> tuple[bool, str]:
        job, _, log = await run_plan(bench, "o1", [
            {"id": "s1", "kind": "tool", "spec": {"name": "tick",
                                                  "args": {"mark": "a"}}},
            {"id": "s2", "kind": "tool", "spec": {"name": "tick",
                                                  "args": {"mark": "b"}}},
            {"id": "s3", "kind": "tool", "spec": {"name": "tick",
                                                  "args": {"mark": "c"}}},
        ])
        return (job is not None and job.status == "done" and log == ["a", "b", "c"],
                f"status={job.status if job else None} log={log}")

    async def o2(bench: Bench) -> tuple[bool, str]:
        job, _, log = await run_plan(bench, "o2", [
            {"id": "s1", "kind": "tool", "spec": {"name": "produce",
                                                  "args": {"value": 7}}},
            {"id": "s2", "kind": "tool", "spec": {"name": "tick",
                                                  "args": {"mark":
                                                           "{s1.value}"}}},
        ])
        return (job is not None and job.status == "done" and log == ["7"],
                f"status={job.status if job else None} log={log}")

    async def o3(bench: Bench) -> tuple[bool, str]:
        job, _, _ = await run_plan(bench, "o3", [
            {"id": "s1", "kind": "tool", "spec": {"name": "boom",
                                                  "args": {}}},
        ])
        ok = (job is not None and job.status == "failed"
              and job.attempts == 1 and "expected failure" in (job.error or ""))
        return ok, (f"status={job.status if job else None} "
                    f"attempts={job.attempts if job else None}")

    async def o4(bench: Bench) -> tuple[bool, str]:
        seen: list[str] = []

        async def note_kind(step, outputs, ctx):
            seen.append(str(step.spec.get("text", "")))
            return {"noted": True}

        job, _, _ = await run_plan(bench, "o4", [
            {"id": "s1", "kind": "note", "spec": {"text": "custom-kind"}},
        ], kinds={"note": note_kind})
        return (job is not None and job.status == "done" and seen == ["custom-kind"],
                f"status={job.status if job else None} seen={seen}")

    async def o5(bench: Bench) -> tuple[bool, str]:
        reg, _ = reg_with_tick()
        queue = _fresh_queue(bench.root / "queue-o5.db")
        bus = EventBus()
        order: list[str] = []
        bus.subscribe("job.started",
                       lambda e: order.append(str(e.payload.get("job"))))
        policy = PolicyEngine()

        async def yes(call: ToolCall, risk: RiskClass) -> bool:
            return True

        orch = Orchestrator(queue, reg, policy, bus=bus, consent=yes)
        low = queue.enqueue("plan", {"plan": [
            {"id": "s", "kind": "tool", "spec": {"name": "produce",
                                                 "args": {"value": 1}}}]},
            title="low", priority=0)
        high = queue.enqueue("plan", {"plan": [
            {"id": "s", "kind": "tool", "spec": {"name": "produce",
                                                 "args": {"value": 2}}}]},
            title="high", priority=5)
        await orch.run_worker(worker="w-o5", max_jobs=2)
        return (order == [high, low],
                f"start order={order} expected=[{high}, {low}]")

    async def o6(bench: Bench) -> tuple[bool, str]:
        reg, _ = reg_with_tick()
        queue = _fresh_queue(bench.root / "queue-o6.db")
        policy = PolicyEngine()
        jid = queue.enqueue("plan", {"plan": [
            {"id": "s", "kind": "tool", "spec": {"name": "produce",
                                                 "args": {"value": 1}}}]})
        queue.cancel(jid)
        orch = Orchestrator(queue, reg, policy)
        processed = await orch.run_worker(worker="w-o6", max_jobs=1)
        job = queue.get(jid)
        return (processed == 0 and job is not None
                and job.status == "canceled",
                f"processed={processed} status={job.status if job else None}")

    async def o7(bench: Bench) -> tuple[bool, str]:
        reg, log = reg_with_tick()
        queue = _fresh_queue(bench.root / "queue-o7.db")
        policy = PolicyEngine()

        async def yes(call: ToolCall, risk: RiskClass) -> bool:
            return True

        jid = queue.enqueue("plan", {"plan": [
            {"id": "s1", "kind": "tool", "spec": {"name": "tick",
                                                  "args": {"mark": "first"}}},
            {"id": "s2", "kind": "tool", "spec": {"name": "tick",
                                                  "args": {"mark": "second"}}},
        ]})
        # simulate a crash mid-job: claim, checkpoint s1 done, requeue — the
        # fabricated checkpoint means s1's handler never ran here; the
        # resume must then run ONLY s2 (once each, no re-runs)
        job = queue.claim("w-crash", 60.0)
        ok_claim = job is not None
        if ok_claim:
            queue.checkpoint(jid, {"done": ["s1"],
                                   "outputs": {"s1": {"appended": "first"}}},
                             "w-crash")
            queue.fail(jid, "simulated crash", worker="w-crash", retry=True)
        orch = Orchestrator(queue, reg, policy, consent=yes)
        await orch.run_worker(worker="w-o7", max_jobs=1)
        final = queue.get(jid)
        return (ok_claim and final is not None and final.status == "done"
                and log == ["second"] and final.attempts == 2,
                f"attempts={final.attempts if final else None} log={log}")

    async def o8(bench: Bench) -> tuple[bool, str]:
        gateway = TaskScriptedGateway([
            _call_turn(("tick", {"mark": "agent"})),
            _text_turn("ticked"),
        ])
        job, _, _ = await run_plan(bench, "o8", [
            {"id": "s1", "kind": "agent",
             "spec": {"instruction": "Call the tick tool with mark 'agent', "
                                     "then stop.",
                       "tools": ["tick"], "max_steps": 4}},
        ], gateway=gateway)
        outputs = (job.result or {}).get("outputs", {}) if job else {}
        agent = outputs.get("s1", {})
        return (job is not None and job.status == "done"
                and agent.get("finish") == "stop"
                and len(agent.get("tool_calls", [])) == 1,
                f"status={job.status if job else None} agent={agent}")

    async def o9(bench: Bench) -> tuple[bool, str]:
        reg, _ = reg_with_tick()
        scoped = scoped_registry(reg, ["tick"])
        names = {d["name"] for d in scoped.declarations()}
        # the job payload carries the allowlist too
        queue = _fresh_queue(bench.root / "queue-o9.db")
        jid = spawn_subagent(queue, instruction="do the tick",
                             tools=["tick"], title="o9-scoped")
        job = queue.get(jid)
        spec_tools = None
        if job is not None:
            steps = steps_from_payload(job.payload)
            spec_tools = steps[0].spec.get("tools")
        return (names == {"tick"} and spec_tools == ["tick"],
                f"declarations={sorted(names)} payload_tools={spec_tools}")

    async def o10(bench: Bench) -> tuple[bool, str]:
        async def crash_kind(step, outputs, ctx):
            raise RuntimeError("kaboom")

        reg, _ = reg_with_tick()
        queue = _fresh_queue(bench.root / "queue-o10.db")
        policy = PolicyEngine()
        orch = Orchestrator(queue, reg, policy, source="bench-o10")
        orch.register_kind("crash", crash_kind)
        jid = queue.enqueue("plan", {"plan": [
            {"id": "s1", "kind": "crash", "spec": {}},
        ]}, max_attempts=2)
        # both attempts: run_worker processes one job per claim, so drain the
        # requeued job with a second claim
        await orch.run_worker(worker="w-o10a", max_jobs=1)
        await orch.run_worker(worker="w-o10b", max_jobs=1)
        job = queue.get(jid)
        return (job is not None and job.status == "failed"
                and job.attempts == 2
                and "kaboom" in (job.error or ""),
                f"status={job.status if job else None} "
                f"attempts={job.attempts if job else None}")

    return [
        HarnessTask("o1", "orchestration", "3-step plan runs in order", o1),
        HarnessTask("o2", "orchestration", "{step.output} template feeds the "
                    "next step", o2),
        HarnessTask("o3", "orchestration", "expected tool failure fails the "
                    "job without retry", o3),
        HarnessTask("o4", "orchestration", "custom step kinds are "
                    "registrable", o4),
        HarnessTask("o5", "orchestration", "priority ordering: high-priority "
                    "job claimed first", o5),
        HarnessTask("o6", "orchestration", "canceled queued job is never "
                    "claimed", o6),
        HarnessTask("o7", "orchestration", "crash mid-job: fresh worker "
                    "resumes from the checkpoint, each step exactly once", o7),
        HarnessTask("o8", "orchestration", "agent step drives a scoped "
                    "subagent loop with a trace", o8),
        HarnessTask("o9", "orchestration", "scoped registry: allowlisted "
                    "tools only", o9),
        HarnessTask("o10", "orchestration", "crashing handler retries to "
                    "max_attempts then fails", o10),
    ]


def build_suite() -> list[Task]:
    tasks: list[Task] = []
    tasks += _files_tasks()
    tasks += _memory_tasks()
    tasks += _web_tasks()
    tasks += _coding_tasks()
    tasks += _orchestration_tasks()
    return tasks


# --------------------------------------------------------------- runner ----


def make_registry(bench: Bench) -> ToolRegistry:
    """One registry for all loop tasks: notes, memory, web, coding — the
    model-facing surface of four subsystems, one policy choke point."""
    from kernel.coding import build_coding_tools
    from kernel.research import build_research_tools

    reg = ToolRegistry()

    @reg.tool(
        name="write_note",
        description="Create or overwrite a note in the sandbox.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}, "text": {"type": "string"}},
            "required": ["name", "text"]},
        risk=RiskClass.WRITE)
    def write_note(call):
        safe = "".join(c for c in str(call.args.get("name", ""))
                       if c.isalnum() or c in "-_")
        (bench.notes / f"{safe}.txt").write_text(
            str(call.args.get("text", "")), encoding="utf-8")
        return "note saved"

    @reg.tool(
        name="read_note",
        description="Read the full text of a sandbox note by name.",
        parameters={"type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"]},
        risk=RiskClass.READ)
    def read_note(call):
        path = bench.notes / f"{str(call.args.get('name', ''))}.txt"
        if not path.exists():
            return ToolResult.fail(call, f"note {path.stem!r} does not exist")
        return path.read_text(encoding="utf-8")

    @reg.tool(
        name="list_notes",
        description="List the names of all sandbox notes.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=RiskClass.READ)
    def list_notes(call):
        return sorted(p.stem for p in bench.notes.glob("*.txt"))

    @reg.tool(
        name="delete_note",
        description="Delete a sandbox note permanently by name.",
        parameters={"type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"]},
        risk=RiskClass.DESTRUCTIVE)
    def delete_note(call):
        path = bench.notes / f"{str(call.args.get('name', ''))}.txt"
        path.unlink(missing_ok=True)
        return "deleted"

    register_memory_tools(reg, bench.engine)
    build_research_tools(reg, fetch=bench.fetch, enabled=True)

    @reg.tool(
        name="web_search",
        description="Search the (canned) benchmark web corpus; returns the "
                    "first result URL for the query.",
        parameters={"type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"]},
        risk=RiskClass.READ)
    def web_search(call):
        return {"first_url": bench.first_url(str(call.args.get("query", ""))),
                "query": str(call.args.get("query", ""))}

    build_coding_tools(reg, bench.workspace, allow_run=True)
    return reg


async def _auto_consent(call: ToolCall, risk: RiskClass) -> bool:
    return True  # running the suite IS the consent (phase1-gate doctrine)


async def run_suite(
    *,
    mode: str = "scripted",
    provider: str = "ollama",
    root: Path | None = None,
    categories: Sequence[str] | None = None,
    task_ids: Sequence[str] | None = None,
    consent_none: bool = False,
    max_steps: int = MAX_STEPS,
) -> SuiteReport:
    """Run the benchmark. `root` is injectable (tests use tmp_path; the CLI
    defaults to .ultron/eval/benchmark). consent_none=True is the dynamic-
    range cripple: WRITE/EXECUTE fail closed, so note-writing tasks must
    fail — proving the suite detects regressions."""
    if mode not in ("scripted", "live"):
        raise ValueError(f"unknown mode {mode!r}")
    root = root if root is not None else DEFAULT_ROOT
    root.mkdir(parents=True, exist_ok=True)
    bench = Bench(root)

    registry = make_registry(bench)
    audit = AuditLog(root / "audit.sqlite3")
    policy = PolicyEngine(audit=audit)

    model = "scripted"
    gateway: Any = None
    if mode == "live":
        from config.loader import get_api_key, load_config
        from evals.phase1_gate import PacedGateway
        from kernel.gateway import GatewaySettings, GeminiAdapter, \
            OllamaAdapter, Provider

        settings = GatewaySettings.from_config(
            {**load_config(), "llm_provider": provider, "llm_timeout_s": 300})
        if settings.provider is Provider.GEMINI:
            key = get_api_key("gemini_api_key")
            if not key:
                raise SystemExit("no gemini_api_key — cannot run live mode")
            adapter: Any = GeminiAdapter(settings, api_key=key)
        else:
            adapter = OllamaAdapter(settings)
        gateway = PacedGateway(adapter)
        model = adapter.model
        print("warming model ...", flush=True)
        await gateway.complete(
            [Message(role="user", text="Reply with the single word: ready")])
        print("model warm.", flush=True)

    consent = None if consent_none else _auto_consent
    loop = AgentLoop(gateway if gateway is not None else object(),
                     policy, registry, max_steps=max_steps,
                     consent=consent, source="p5-bench")

    selected = [t for t in build_suite()
                if (not categories or t.category in categories)
                and (not task_ids or t.id in task_ids)]
    results: list[TaskResult] = []
    for task in selected:
        start = time.monotonic()
        if isinstance(task, LoopTask):
            # scripted mode: fresh fake gateway per task (independent turns);
            # live mode: the shared paced gateway answers every task.
            if mode == "scripted":
                loop = AgentLoop(TaskScriptedGateway(list(task.script)),
                                 policy, registry, max_steps=max_steps,
                                 consent=consent, source="p5-bench")
            if task.category == "files":
                bench.reset_notes()
            if task.category == "coding":
                bench.reset_workspace()
            try:
                result = await loop.run([Message(role="user",
                                                 text=task.prompt)])
                ok, detail = task.verify(bench, result)
                finish, steps = result.finish, result.steps
            except Exception as exc:  # noqa: BLE001 — record, never die
                ok, detail, finish, steps = False, \
                    f"harness error: {type(exc).__name__}: {exc}", "error", 0
        else:
            try:
                ok, detail = await task.run(bench)
                finish, steps = "harness", 0
            except Exception as exc:  # noqa: BLE001 — record, never die
                ok, detail, finish, steps = False, \
                    f"harness error: {type(exc).__name__}: {exc}", "error", 0
        took = time.monotonic() - start
        print(f"{task.id:>4} [{task.category:<13}] "
              f"{'PASS' if ok else 'FAIL'} ({steps} steps, {finish}, "
              f"{took:.1f}s) — {detail}", flush=True)
        results.append(TaskResult(id=task.id, category=task.category,
                                  ok=ok, detail=detail, finish=finish,
                                  steps=steps))

    report = SuiteReport(mode=mode, provider=provider if mode == "live"
                         else "", model=model, results=tuple(results),
                         partial=bool(categories or task_ids))
    out = root / "results.json"
    out.write_text(json.dumps(report.summary(), indent=2), encoding="utf-8")
    print(f"score: {report.score:.3f} over {len(results)} tasks "
          f"(results: {out})")
    for cat, score in report.by_category().items():
        print(f"  {cat:<13} {score:.2f}")
    engine_closed = False
    try:
        bench.engine.close()
        engine_closed = True
    finally:
        audit.close()
    del engine_closed  # close attempted; failures would have raised above
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--provider", default=None,
                        choices=["ollama", "gemini"],
                        help="live model via the gateway (omit for the "
                             "hermetic scripted runner)")
    parser.add_argument("--categories", default="",
                        help="comma-separated category filter")
    parser.add_argument("--tasks", default="",
                        help="comma-separated task ids to run")
    parser.add_argument("--out", default=str(DEFAULT_ROOT),
                        help="run directory for results/audit/dbs")
    args = parser.parse_args()
    categories = [c for c in args.categories.split(",") if c.strip()]
    task_ids = [t for t in args.tasks.split(",") if t.strip()]
    mode = "live" if args.provider else "scripted"
    report = asyncio.run(run_suite(
        mode=mode, provider=args.provider or "ollama",
        root=Path(args.out), categories=categories, task_ids=task_ids))
    raise SystemExit(0 if report.score == 1.0 or report.partial else 1)


if __name__ == "__main__":
    main()
