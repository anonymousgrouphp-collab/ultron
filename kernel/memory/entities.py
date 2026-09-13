"""kernel/memory/entities.py — entity memory + pronoun resolution (§P2-A).

K9-derived (research/09 §P2-A): "Where was *he* born?" only works if the
assistant remembers who "he" is. In ULTRON's Live architecture the model
resolves in-session pronouns itself (it hears the whole conversation); the
real gap is *across* sessions and in stateless prompts (the AgentRunner's
multi-step tasks start a fresh context every run). This module provides:

- ``EntityStore`` — a small session-scoped, thread-safe store of recently
  discussed entities (name, type, gender, confidence) with K9's no-downgrade
  rule and a 10-entry LRU cap. In-session use is optional; the store also
  feeds cross-session persistence.
- ``extract_entities`` — deterministic capture of people/places/orgs from a
  user turn ("my friend Aarav", "Elon Musk founded SpaceX") without an LLM
  call. Structured extraction from tool results (K9's strict-JSON contract)
  can replace/augment this when the loop layer adopts it.
- ``resolve_pronouns`` — gender/type-matched pronoun→entity rewrite, with a
  hard no-guess rule: unresolved pronouns return the text unchanged (K9's
  "Who are you referring to?" hard-stop is left to the caller, since the Live
  model usually has the referent in its own context).
- ``remember_entities`` — cross-session persistence via the MemoryEngine: one
  fact per entity (entity="people", topic=<name>), so auto-RAG (the
  PromptAssembler) surfaces them next session.

No global state: callers own the store (session/user-scoped), per the K9
class-level-store lesson (research/09 §5.2).
"""

from __future__ import annotations

import re
import threading
import time
from dataclasses import dataclass
from typing import Any

__all__ = [
    "Entity",
    "EntityStore",
    "extract_entities",
    "remember_entities",
    "resolve_pronouns",
]

_MAX_ENTITIES = 10

_GENDER_BY_PRONOUN = {"he": "male", "him": "male", "his": "male",
                      "she": "female", "her": "female", "hers": "female"}

_PERSON_POSSESSIVE = re.compile(
    r"\b(?:my|our)\s+(?:friend|brother|sister|cousin|wife|husband|son|"
    r"daughter|mother|father|dad|mom|boss|colleague|manager|teacher|"
    r"neighbour|neighbor|roommate|partner)\s+"
    r"((?-i:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?))",  # name stays case-sensitive
    re.IGNORECASE)
_PERSON_IS = re.compile(
    r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)\s+(?:is|was)\s+(?:my|a|an|the)\b")
_ORG_IS = re.compile(
    r"\b([A-Z][A-Za-z]*(?:\s+[A-Z][A-Za-z]*)*)\s+"
    r"(?:is|was)\s+(?:a|an|the)?\s*(?:[a-z]+\s+)?"
    r"(?:company|startup|organisation|organization|agency|team|firm)\b")
_PLACE_IN = re.compile(
    r"\b(?:I|we)\s+(?:live|lived)\s+in\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)")
_CAPITALIZED_RUN = re.compile(r"\b([A-Z][a-z]{2,}(?:\s+[A-Z][a-z]{2,})+)\b")

_PLACE_WORDS = {"india", "delhi", "mumbai", "bangalore", "london", "paris",
                "tokyo", "monday", "tuesday", "wednesday", "thursday",
                "friday", "saturday", "sunday"}
_BLACKLIST = {"i", "you", "we", "it", "the", "this", "that", "ultron",
              "india", "monday", "tuesday", "wednesday", "thursday",
              "friday", "saturday", "sunday", "google", "youtube"}


@dataclass
class Entity:
    """One known entity (K9's Tier-1 record, instance-scoped)."""

    name: str
    type: str                 # person | place | org | other
    gender: str = "unknown"   # male | female | neutral | unknown
    confidence: float = 0.6
    ts: float = 0.0


class EntityStore:
    """Thread-safe, bounded, most-recent-first entity store."""

    def __init__(self, max_entities: int = _MAX_ENTITIES) -> None:
        self._max = max_entities
        self._lock = threading.Lock()
        self._entities: list[Entity] = []

    def update(self, name: str, type_: str = "person",
               gender: str = "unknown", confidence: float = 0.6) -> Entity:
        """Insert or refresh an entity. A lower-confidence re-observation is
        ignored (K9's no-downgrade rule); a same-name update keeps the better
        metadata and refreshes recency."""
        name = (name or "").strip()
        if not name:
            raise ValueError("entity name must be non-empty")
        with self._lock:
            existing = next((e for e in self._entities if e.name == name), None)
            if existing is not None and existing.confidence > confidence:
                return existing
            if existing is not None:
                self._entities.remove(existing)
            entity = Entity(name=name, type=type_.lower(),
                            gender=(gender or "unknown").lower(),
                            confidence=float(confidence), ts=time.time())
            self._entities.append(entity)
            while len(self._entities) > self._max:
                self._entities.pop(0)  # evict least-recently-updated
            return entity

    def get_last(self, *, type_: str | None = None,
                 gender: str | None = None) -> Entity | None:
        """Most recent entity matching the type/gender filter, else None."""
        with self._lock:
            for entity in reversed(self._entities):
                if type_ is not None and entity.type != type_.lower():
                    continue
                if gender is not None and entity.gender != gender.lower():
                    continue
                return entity
            return None

    def all(self) -> list[Entity]:
        with self._lock:
            return list(self._entities)

    def __len__(self) -> int:
        with self._lock:
            return len(self._entities)


def extract_entities(text: str) -> list[tuple[str, str, str, float]]:
    """Deterministic entity capture from one user turn.

    Returns (name, type, gender, confidence) tuples, best-first. Gender is
    inferred only from explicit kinship/role words — never guessed."""
    found: list[tuple[str, str, str, float]] = []
    seen: set[str] = set()

    def add(name: str, type_: str, gender: str, conf: float) -> None:
        name = name.strip().strip(",.!?")
        key = name.lower()
        if not name or key in seen or key in _BLACKLIST or len(name) < 3:
            return
        if key in _PLACE_WORDS and type_ != "place":
            return
        seen.add(key)
        found.append((name, type_, gender, conf))

    for match in _PERSON_POSSESSIVE.finditer(text or ""):
        add(match.group(1), "person", "unknown", 0.9)
    for match in _PLACE_IN.finditer(text or ""):
        add(match.group(1), "place", "unknown", 0.9)
    # org before person: "Zerodha is a listed company" would otherwise match
    # _PERSON_IS first and mislabel the entity
    for match in _ORG_IS.finditer(text or ""):
        add(match.group(1), "org", "unknown", 0.8)
    for match in _PERSON_IS.finditer(text or ""):
        add(match.group(1), "person", "unknown", 0.8)
    if not found:
        for match in _CAPITALIZED_RUN.finditer(text or ""):
            add(match.group(1), "person", "unknown", 0.5)
    return found


def resolve_pronouns(text: str, store: EntityStore,
                     ) -> tuple[str, Entity | None]:
    """Rewrite the first he/she/him/her/his/hers in ``text`` with the best
    matching entity. Returns (possibly-rewritten text, entity-or-None).

    No guess rule: an unresolved pronoun returns the text unchanged with
    None — the caller may ask for clarification, but we never fabricate a
    referent. "it/its" is intentionally NOT resolved (too ambiguous)."""
    if not text or len(store) == 0:
        return text, None
    match = re.search(r"\b(he|she|him|her|his|hers)\b", text, re.IGNORECASE)
    if match is None:
        return text, None
    pronoun = match.group(1).lower()
    gender = _GENDER_BY_PRONOUN[pronoun]
    entity = store.get_last(type_="person", gender=gender)
    if entity is None:
        return text, None
    resolved = re.sub(rf"\b{re.escape(match.group(1))}\b", entity.name,
                      text, count=1, flags=re.IGNORECASE)
    return resolved, entity


def remember_entities(store: EntityStore, memory: Any,
                      *, min_confidence: float = 0.8) -> int:
    """Persist high-confidence store entities as memory facts so auto-RAG
    surfaces them in later sessions. Deduplicated per entity name: an entity
    already in memory (same topic under entity="people") is skipped. Returns
    the number newly stored."""
    if memory is None:
        return 0
    stored = 0
    for entity in store.all():
        if entity.confidence < min_confidence:
            continue
        try:
            if _entity_already_known(memory, entity.name):
                continue
            memory.remember(
                content=f"{entity.name} is a {entity.type} the user talks about.",
                entity="people",
                topic=entity.name,
                importance=0.6,
                source_ref="entity-store",
            )
            stored += 1
        except Exception:  # noqa: BLE001 — persistence is best-effort
            continue
    return stored


def _entity_already_known(memory: Any, name: str) -> bool:
    try:
        for hit in memory.search(name, k=3):
            if getattr(hit, "entity", "") == "people" and \
                    getattr(hit, "topic", "").lower() == name.lower():
                return True
    except Exception:  # noqa: BLE001 — recall failure = store again (safe dup)
        return False
    return False
