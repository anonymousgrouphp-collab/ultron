"""kernel/memory/guards.py — anti-echo memory guards (§P2-B, K9-derived).

K9's hard-won lessons about episodic memory pollution, ported to ULTRON:

1. **Echo guard** — text that was just *recalled* from memory (the assistant
   saying "based on my memory, you like X") must never be stored back as a
   new fact, or memory slowly replaces its contents with echoes of itself.
2. **Narrative guard** — assistant wrapper phrases ("I will remember that: …",
   "you mentioned …") are speech-act noise, not knowledge. Strip the wrapper
   and store the payload, or block the line outright.
3. **Tech-state floor** — short first-person state updates ("I am debugging
   the audio pipeline") are the highest-value episodic signals; length-based
   importance heuristics undervalue them. A keyword+opener match lifts the
   importance to a floor of 0.65.

Pure functions, no IO, no engine dependency — the write paths (the explicit
save-memory tool, the Consolidator's judge ops) apply them at their discretion.
"""

from __future__ import annotations

import re

__all__ = [
    "TECH_STATE_FLOOR",
    "is_echo_narrative",
    "strip_store_prefix",
    "tech_state_importance",
]

TECH_STATE_FLOOR = 0.65

# ── Guard 1+2: assistant narrative / echo patterns ────────────────────────────
# Sentence-START prefixes that mark recalled-or-narrated content (K9 Fix 3).
_NARRATIVE_PREFIXES = (
    "based on", "from what i recall", "from what i remember", "according to",
    "i think", "it seems", "the logs show", "the logs mention", "i recall",
    "from memory", "i don't have", "i dont have", "i couldn't find",
    "i could not find", "i cannot find", "i have no record", "i do not recall",
    "i don't recall", "i dont recall", "i was unable", "my memory",
    "as i mentioned", "as mentioned", "you were", "you said", "you mentioned",
    "earlier you", "previously you", "previously we", "in my memory",
    "if i recall",
)

# Mid-sentence past-reference verbs — the assistant narrating the user's past
# (K9's echo-loop detector).
_PAST_REFERENCE = re.compile(
    r"\b(were\s+debugging|were\s+working|were\s+fixing|"
    r"you\s+mentioned|you\s+said|you\s+told|you\s+discussed|"
    r"earlier\s+you|previously\s+you|previously\s+worked|"
    r"based\s+on\s+(your|the)\s+(logs?|memory|context))\b",
    re.IGNORECASE,
)

# ── Wrapper stripper (K9 Fix 2): store the payload, not the speech act ───────
_STORE_PREFIX = re.compile(
    r"^(please\s+)?(i\s+will\s+remember\s+that|i'll\s+remember\s+that|"
    r"remember\s+this|remember\s+that|remember|store\s+this|note\s+this|"
    r"store|note|fact)[\s:,-]*",
    re.IGNORECASE,
)

# ── Guard 3: first-person tech-state detection (K9 Fix 1 / Fix 4) ─────────────
_TECH_STATE_OPENER = re.compile(
    r"^(i\s+am|i'm|i\s+was|i\s+have\s+been|currently|working\s+on|"
    r"debugging|fixing|building|training|implementing|refactoring|"
    r"deploying|designing|testing|running|migrating|setting\s+up|"
    r"the\s+(bug|error|crash|issue|problem|exception|dataset|model)\s+(is|was)|"
    r"(training\s+)?dataset\s+(is|was)|model\s+(is|was))\b",
    re.IGNORECASE,
)
_TECH_KEYWORD = re.compile(
    r"\b(debug|bug|crash|fix|build|train|deploy\w*|dataset|model|api|code|"
    r"script|function|class|config|test|fail|exception|latency|timeout|"
    r"database|schema|migrat\w*|endpoint|pipeline|inference|embedding|vector|"
    r"cache|server|simulation|anomaly|predict\w*|refactor\w*|corrupt\w*|"
    r"deadlock|overflow|authenticat\w*|module|library|thread|async|"
    r"container|docker|kernel|orchestrator|gateway|registry)\b",
    re.IGNORECASE,
)


def is_echo_narrative(text: str) -> bool:
    """True when the text is assistant narration/recall rather than knowledge
    (a narrative prefix or a past-reference verb). Such text must not be
    stored back into memory."""
    if not text:
        return False
    stripped = text.strip().lower()
    if any(stripped.startswith(p) for p in _NARRATIVE_PREFIXES):
        return True
    return bool(_PAST_REFERENCE.search(stripped))


def strip_store_prefix(text: str) -> str:
    """Strip speech-act wrappers ("remember that …", "fact: …") and return the
    payload — the part that is actually worth remembering."""
    cleaned = (text or "").strip()
    # strip repeatedly: "remember that store this X" collapses fully
    for _ in range(3):
        new = _STORE_PREFIX.sub("", cleaned, count=1).strip()
        if new == cleaned:
            break
        cleaned = new
    return cleaned


def tech_state_importance(content: str, importance: float) -> float:
    """First-person technical state updates get a minimum importance of
    TECH_STATE_FLOOR (0.65), never a downgrade below the given value."""
    if not content:
        return importance
    if _TECH_STATE_OPENER.search(content.strip()) and _TECH_KEYWORD.search(content):
        return max(importance, TECH_STATE_FLOOR)
    return importance
