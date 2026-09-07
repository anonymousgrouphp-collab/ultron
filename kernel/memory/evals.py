"""kernel/memory/evals.py — P3-B: the recall eval (research/04 §9).

The Phase-3 gate is measured, not vibes: 40 LOCOMO-style questions about a
scripted multi-session corpus, run through the REAL retrieval path
(MemoryEngine.search — FTS5 ⊕ vector fused by RRF), scored by scripted
verifiers, gated in CI (research/05 §7: scripted verifiers wherever an
end-state is checkable; every question records the chunks it retrieved so a
miss is diagnosable).

Design decisions:
- **What is measured:** retrieval quality — does `memory_search` surface the
  fact(s) that answer the question within top-k? Downstream QA (LLM reading
  the chunks) is the caller's layer; the recall eval isolates OUR half.
- **Verifiers are scripted:** a question declares `expect` (keys whose facts
  must appear in top-k) and optionally `forbid` (keys that must never appear —
  the absence/negation guard). Keys travel in `source_ref` as "eval:<key>".
- **Corpus is hermetic and synthetic** (a fictional user, six monthly
  sessions, 30 facts) — no user data, no network, no live model. The optional
  BGE-M3 embedder (P1-D `make_embedder("bge-m3")`) is a drop-in upgrade path:
  the eval is embedder-agnostic because the engine is.
- **Question types** (research/04 §9): single_hop · temporal · multi_hop ·
  negation ("did I ever…?" answered by its disconfirming evidence) ·
  counts/lists (all facts making up the count must surface).
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.memory.embedders import Embedder
from kernel.memory.engine import MemoryEngine

GATE = 0.80          # Phase-3 acceptance gate (roadmap §4)
TOP_K = 8            # the recall window the loop's memory tool uses

SINGLE_HOP = "single_hop"
TEMPORAL = "temporal"
MULTI_HOP = "multi_hop"
NEGATION = "negation"
COUNTS = "counts"
QTYPES: tuple[str, ...] = (SINGLE_HOP, TEMPORAL, MULTI_HOP, NEGATION, COUNTS)

_EVAL_REF_PREFIX = "eval:"


def _month_ts(year: int, month: int, day: int = 15) -> float:
    """A stable mid-month timestamp (local-naive; ordering is all we need)."""
    return calendar.timegm((year, month, day, 12, 0, 0, 0, 0, 0))


@dataclass(frozen=True)
class EvalSession:
    """One scripted session with a fixed point in time."""

    label: str
    started_at: float


@dataclass(frozen=True)
class EvalFact:
    """One fact a judge would have extracted from a session."""

    key: str
    content: str
    entity: str = ""
    topic: str = ""
    importance: float = 0.6
    session: int = 0


@dataclass(frozen=True)
class RecallQuestion:
    """One eval question with its scripted verifier.

    `expect` — keys whose facts must be retrieved within top-k.
    `forbid` — keys that must NOT be retrieved (absence guard).
    """

    id: str
    qtype: str
    question: str
    expect: tuple[str, ...] = ()
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class EvalCorpus:
    sessions: tuple[EvalSession, ...]
    facts: tuple[EvalFact, ...]
    questions: tuple[RecallQuestion, ...]


@dataclass(frozen=True)
class QuestionResult:
    """Per-question outcome — `retrieved` keys make every miss diagnosable."""

    id: str
    qtype: str
    question: str
    correct: bool
    retrieved: tuple[str, ...]
    missing: tuple[str, ...]
    violated: tuple[str, ...]


@dataclass(frozen=True)
class RecallReport:
    """The eval score with a per-type breakdown (JSON-able via summary())."""

    results: tuple[QuestionResult, ...]
    score: float
    by_type: dict[str, float]
    k: int

    def summary(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "gate": GATE,
            "passed": self.score > GATE,
            "k": self.k,
            "total": len(self.results),
            "by_type": {t: round(v, 4) for t, v in self.by_type.items()},
            "failures": [
                {"id": r.id, "type": r.qtype, "question": r.question,
                 "retrieved": list(r.retrieved), "missing": list(r.missing),
                 "violated": list(r.violated)}
                for r in self.results if not r.correct
            ],
        }


def build_eval_corpus() -> EvalCorpus:
    """The scripted LOCOMO-style corpus: fictional user "Maya", six monthly
    sessions (January–June 2026), 33 facts, 40 questions (8 per type)."""
    sessions = (
        EvalSession("January 2026", _month_ts(2026, 1)),
        EvalSession("February 2026", _month_ts(2026, 2)),
        EvalSession("March 2026", _month_ts(2026, 3)),
        EvalSession("April 2026", _month_ts(2026, 4)),
        EvalSession("May 2026", _month_ts(2026, 5)),
        EvalSession("June 2026", _month_ts(2026, 6)),
    )
    facts = (
        # ---- January (session 0) ----
        EvalFact("home_city", "Maya lives in Portland, Oregon.",
                 entity="maya", topic="home city"),
        EvalFact("job", "Maya works as a product designer at Fernwood Studio.",
                 entity="maya", topic="work"),
        EvalFact("bike_shop_rec",
                 "Maya's friend Dana recommended the Spoke & Pedal bike shop "
                 "for tune-ups.", entity="dana", topic="bike"),
        EvalFact("diet", "Maya is allergic to shellfish and avoids all "
                 "seafood.", entity="maya", topic="diet allergy"),
        # ---- February (session 1) ----
        EvalFact("monstera", "Maya owns a Monstera plant in her living room.",
                 entity="maya", topic="plants"),
        EvalFact("pothos", "Maya owns a pothos plant on her kitchen shelf.",
                 entity="maya", topic="plants"),
        EvalFact("snake", "Maya owns a snake plant in her bedroom.",
                 entity="maya", topic="plants"),
        EvalFact("tea", "Maya switched from coffee to green tea in February.",
                 entity="maya", topic="drinks"),
        EvalFact("coffee_past", "Before February, Maya used to drink two "
                 "coffees every day.", entity="maya", topic="drinks"),
        EvalFact("dentist_appt", "Maya booked a dentist appointment with "
                 "Dr. Okafor about a cracked molar.", entity="maya",
                 topic="dentist"),
        # ---- March (session 2) ----
        EvalFact("apartment", "Maya moved to a new apartment on Alberta "
                 "Street in March.", entity="maya", topic="home"),
        EvalFact("hike_hate", "Maya tried a hike in Forest Park but hated "
                 "the bugs, so she sticks to swimming instead.",
                 entity="maya", topic="hiking exercise"),
        EvalFact("swim", "Maya swims laps twice a week at the Community "
                 "Pool.", entity="maya", topic="exercise swimming"),
        EvalFact("keyboard", "Maya bought a split ergonomic keyboard for "
                 "long design sessions.", entity="maya", topic="gear"),
        EvalFact("zoe_hiker", "Maya's friend Zoe is an avid hiker who visits "
                 "Forest Park every weekend.", entity="zoe", topic="friends"),
        # ---- April (session 3) ----
        EvalFact("dentist_fixed", "Dr. Okafor fitted Maya's cracked molar "
                 "with a crown in April.", entity="maya", topic="dentist"),
        EvalFact("bike_tuneup", "Maya booked a tune-up at Spoke & Pedal for "
                 "her commuter bike.", entity="maya", topic="bike"),
        EvalFact("sister_visit", "Maya's sister Priya visited Portland for a "
                 "week in April.", entity="priya", topic="family"),
        EvalFact("recipe_dal", "Maya learned to cook chana dal from her "
                 "sister Priya.", entity="maya", topic="cooking"),
        EvalFact("plant_light", "Maya moved the pale Monstera closer to the "
                 "balcony window.", entity="maya", topic="plants"),
        # ---- May (session 4) ----
        EvalFact("conference", "Maya presented her studio's app redesign at "
                 "the DesignWest conference in May.", entity="maya",
                 topic="work conference"),
        EvalFact("marathon_watch", "Maya set a goal to swim 40 laps nonstop "
                 "by the end of June.", entity="maya", topic="exercise goal"),
        EvalFact("pothos_cutting", "Maya propagated a cutting from her "
                 "pothos plant as a gift for Zoe.", entity="maya",
                 topic="plants"),
        EvalFact("camera", "Maya bought a used Fujifilm camera for weekend "
                 "photography.", entity="maya", topic="gear camera"),
        EvalFact("bread", "Maya started baking sourdough bread every Sunday "
                 "in May.", entity="maya", topic="cooking"),
        EvalFact("run_quit", "Maya quit jogging because running hurts her "
                 "knees.", entity="maya", topic="exercise running"),
        # ---- June (session 5) ----
        EvalFact("promotion", "Maya was promoted to senior product designer "
                 "at Fernwood Studio in June.", entity="maya", topic="work"),
        EvalFact("swim_goal_met", "Maya swam 40 laps nonstop on June 21st, "
                 "meeting her swim goal.", entity="maya",
                 topic="exercise goal"),
        EvalFact("alberta_neighbor", "Maya's neighbor on Alberta Street is a "
                 "violinist named Emil.", entity="emil", topic="neighbors"),
        EvalFact("trip", "Maya plans to visit her sister Priya in Vancouver "
                 "in August.", entity="maya", topic="travel"),
    )
    questions = (
        # ---------------- single_hop (8) ----------------
        RecallQuestion("sh1", SINGLE_HOP, "Where in Oregon does Maya live?",
                       expect=("home_city",)),
        RecallQuestion("sh2", SINGLE_HOP, "What is Maya's job at Fernwood?",
                       expect=("job",)),
        RecallQuestion("sh3", SINGLE_HOP, "Which food allergy does Maya have?",
                       expect=("diet",)),
        RecallQuestion("sh4", SINGLE_HOP, "Who recommended the Spoke & Pedal "
                       "bike shop?", expect=("bike_shop_rec",)),
        RecallQuestion("sh5", SINGLE_HOP, "What did Dr. Okafor do about the "
                       "cracked molar?", expect=("dentist_fixed",)),
        RecallQuestion("sh6", SINGLE_HOP, "What camera does Maya own?",
                       expect=("camera",)),
        RecallQuestion("sh7", SINGLE_HOP, "Who is Maya's neighbor on Alberta "
                       "Street?", expect=("alberta_neighbor",)),
        RecallQuestion("sh8", SINGLE_HOP, "What did Maya buy for long design "
                       "sessions?", expect=("keyboard",)),
        # ---------------- temporal (8) ----------------
        RecallQuestion("tm1", TEMPORAL, "What changed about Maya's home in "
                       "March?", expect=("apartment",)),
        RecallQuestion("tm2", TEMPORAL, "What did Maya switch to drinking in "
                       "February?", expect=("tea",)),
        RecallQuestion("tm3", TEMPORAL, "What did Maya present at the "
                       "DesignWest conference?", expect=("conference",)),
        RecallQuestion("tm4", TEMPORAL, "What happened at the dentist in "
                       "April?", expect=("dentist_fixed",)),
        RecallQuestion("tm5", TEMPORAL, "What did Maya learn to cook during "
                       "Priya's April visit?", expect=("recipe_dal",)),
        RecallQuestion("tm6", TEMPORAL, "What did Maya start baking every "
                       "Sunday in May?", expect=("bread",)),
        RecallQuestion("tm7", TEMPORAL, "When did Maya swim 40 laps nonstop?",
                       expect=("swim_goal_met",)),
        RecallQuestion("tm8", TEMPORAL, "Who visited Maya in Portland in "
                       "April?", expect=("sister_visit",)),
        # ---------------- multi_hop (8) ----------------
        RecallQuestion("mh1", MULTI_HOP, "Which shop services Maya's commuter "
                       "bike, and who recommended it?",
                       expect=("bike_tuneup", "bike_shop_rec")),
        RecallQuestion("mh2", MULTI_HOP, "Did Maya reach the swim goal she "
                       "set for herself?", expect=("marathon_watch",
                                                   "swim_goal_met")),
        RecallQuestion("mh3", MULTI_HOP, "Which plant did Maya propagate for "
                       "Zoe, and in which room does the original live?",
                       expect=("pothos_cutting", "pothos")),
        RecallQuestion("mh4", MULTI_HOP, "Whose recipe did Maya learn while "
                       "her sister was visiting?",
                       expect=("recipe_dal", "sister_visit")),
        RecallQuestion("mh5", MULTI_HOP, "What did Maya do about the pale "
                       "Monstera in her new apartment?",
                       expect=("plant_light", "apartment")),
        RecallQuestion("mh6", MULTI_HOP, "What seniority did Maya reach at "
                       "Fernwood Studio?", expect=("job", "promotion")),
        RecallQuestion("mh7", MULTI_HOP, "Who is Maya planning to visit in "
                       "Vancouver, and when did that person last visit "
                       "Portland?", expect=("trip", "sister_visit")),
        RecallQuestion("mh8", MULTI_HOP, "Which friend hikes Forest Park, and "
                       "what did Maya gift her from her plants?",
                       expect=("zoe_hiker", "pothos_cutting")),
        # ---------------- counts/lists (8) ----------------
        RecallQuestion("cn1", COUNTS, "How many plants does Maya own, and "
                       "what are they?", expect=("monstera", "pothos",
                                                 "snake")),
        RecallQuestion("cn2", COUNTS, "What did Maya see Dr. Okafor about, "
                       "and what did he do about it?", expect=("dentist_appt",
                                                               "dentist_fixed")),
        RecallQuestion("cn3", COUNTS, "Which kinds of exercise does Maya do: "
                       "hiking or swimming?", expect=("swim", "hike_hate")),
        RecallQuestion("cn4", COUNTS, "What did Maya learn to cook, and what "
                       "did she start baking?", expect=("recipe_dal",
                                                        "bread")),
        RecallQuestion("cn5", COUNTS, "What gear has Maya bought for design "
                       "and photography?", expect=("keyboard", "camera")),
        RecallQuestion("cn6", COUNTS, "How did Maya's work at Fernwood "
                       "change this year?", expect=("job", "promotion")),
        RecallQuestion("cn7", COUNTS, "What changed in Maya's coffee and tea "
                       "habits?", expect=("tea", "coffee_past")),
        RecallQuestion("cn8", COUNTS, "What happened to Maya's Monstera and "
                       "pothos plants this spring?", expect=("plant_light",
                                                             "pothos_cutting")),
        # ---------------- negation (8) ----------------
        RecallQuestion("ng1", NEGATION, "Did Maya ever say she likes hiking?",
                       expect=("hike_hate",)),
        RecallQuestion("ng2", NEGATION, "Does Maya still drink coffee?",
                       expect=("tea", "coffee_past")),
        RecallQuestion("ng3", NEGATION, "Does Maya eat seafood nowadays?",
                       expect=("diet",)),
        RecallQuestion("ng4", NEGATION, "Did Maya keep up with running?",
                       expect=("run_quit",)),
        RecallQuestion("ng5", NEGATION, "Did Maya buy her camera brand new?",
                       expect=("camera",)),
        RecallQuestion("ng6", NEGATION, "Was Maya just an attendee at "
                       "DesignWest?", expect=("conference",)),
        RecallQuestion("ng7", NEGATION, "Did Maya get promoted at Fernwood "
                       "this year?", expect=("promotion",)),
        RecallQuestion("ng8", NEGATION, "Did Maya fail her swimming goal?",
                       expect=("swim_goal_met",)),
    )
    return EvalCorpus(sessions=sessions, facts=facts, questions=questions)


def seed_eval_engine(
    corpus: EvalCorpus,
    path: str | Path,
    embedder: Embedder | None = None,
) -> MemoryEngine:
    """Build a MemoryEngine holding the corpus — facts carry `eval:<key>`
    source_refs and their session's timestamp as known_at."""
    engine = MemoryEngine(path, embedder=embedder)
    for fact in corpus.facts:
        engine.remember(
            fact.content,
            entity=fact.entity,
            topic=fact.topic,
            importance=fact.importance,
            source_ref=f"{_EVAL_REF_PREFIX}{fact.key}",
            known_at=corpus.sessions[fact.session].started_at,
        )
    return engine


def run_recall_eval(
    engine: MemoryEngine,
    corpus: EvalCorpus,
    *,
    k: int = TOP_K,
) -> RecallReport:
    """Ask every question through the real retrieval path and score it."""
    results: list[QuestionResult] = []
    for q in corpus.questions:
        hits = engine.search(q.question, k=k)
        retrieved = tuple(
            hit.source_ref[len(_EVAL_REF_PREFIX):]
            for hit in hits
            if hit.source_ref and hit.source_ref.startswith(_EVAL_REF_PREFIX)
        )
        missing = tuple(key for key in q.expect if key not in retrieved)
        violated = tuple(key for key in q.forbid if key in retrieved)
        results.append(QuestionResult(
            id=q.id, qtype=q.qtype, question=q.question,
            correct=not missing and not violated,
            retrieved=retrieved, missing=missing, violated=violated,
        ))
    by_type: dict[str, float] = {}
    for qtype in QTYPES:
        subset = [r for r in results if r.qtype == qtype]
        if subset:
            by_type[qtype] = sum(r.correct for r in subset) / len(subset)
    score = (sum(r.correct for r in results) / len(results)) if results else 0.0
    return RecallReport(results=tuple(results), score=score,
                        by_type=by_type, k=k)


def recall_gate_ok(report: RecallReport, gate: float = GATE) -> bool:
    """The CI gate: retrieval recall must EXCEED the roadmap bar (roadmap §4:
    ">80%") — a score exactly at the bar equals a degraded retriever (a dead
    FTS leg scores 0.80 on this corpus), so equality fails."""
    return report.score > gate


__all__ = [
    "COUNTS",
    "GATE",
    "MULTI_HOP",
    "NEGATION",
    "QTYPES",
    "RecallQuestion",
    "RecallReport",
    "QuestionResult",
    "SINGLE_HOP",
    "TEMPORAL",
    "TOP_K",
    "EvalCorpus",
    "EvalFact",
    "EvalSession",
    "build_eval_corpus",
    "recall_gate_ok",
    "run_recall_eval",
    "seed_eval_engine",
]
