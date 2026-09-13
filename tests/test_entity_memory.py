"""tests/test_entity_memory.py — §P2-A entity store + pronoun resolution.

Covers the K9-derived entity semantics: no-downgrade updates, LRU eviction,
gender/type-matched pronoun resolution with a hard no-guess rule, deterministic
extraction, and cross-session persistence with dedup.
"""

from __future__ import annotations

import pytest

from kernel.memory.engine import MemoryEngine
from kernel.memory.entities import (
    EntityStore,
    extract_entities,
    remember_entities,
    resolve_pronouns,
)


# ------------------------------------------------------------------ store ---

def test_store_no_downgrade_of_confidence():
    store = EntityStore()
    store.update("Aarav", "person", "male", 0.9)
    refreshed = store.update("Aarav", "person", "male", 0.5)
    assert refreshed.confidence == 0.9  # lower-confidence re-observation ignored


def test_store_lru_eviction():
    store = EntityStore(max_entities=3)
    for name in ("A", "B", "C", "D"):
        store.update(name, "person", "unknown", 0.9)
    names = [e.name for e in store.all()]
    assert len(names) == 3
    assert "A" not in names and "D" in names  # oldest evicted


def test_store_get_last_filters():
    store = EntityStore()
    store.update("Aarav", "person", "male", 0.9)
    store.update("Zoho", "org", "unknown", 0.8)
    store.update("Meera", "person", "female", 0.9)
    assert store.get_last(type_="person", gender="male").name == "Aarav"
    assert store.get_last(type_="person", gender="female").name == "Meera"
    assert store.get_last(type_="org").name == "Zoho"
    assert store.get_last(type_="place") is None


def test_store_rejects_empty_name():
    with pytest.raises(ValueError):
        EntityStore().update("")


# ------------------------------------------------------------- extraction ---

def test_extract_kinship_person():
    hits = extract_entities("My friend Aarav is coming over tonight")
    assert hits and hits[0][0] == "Aarav" and hits[0][1] == "person"


def test_extract_place_and_org():
    assert any(h[0] == "Pune" and h[1] == "place"
               for h in extract_entities("I live in Pune now"))
    assert any(h[0] == "Zerodha" and h[1] == "org"
               for h in extract_entities("Zerodha is a brokerage company"))


def test_extract_blacklist_and_weekdays():
    assert extract_entities("I will meet him on Monday") == []
    assert extract_entities("okay sure fine") == []


# -------------------------------------------------------------- resolving ---

def test_resolve_male_pronoun():
    store = EntityStore()
    store.update("Aarav", "person", "male", 0.9)
    resolved, entity = resolve_pronouns("Where does he work?", store)
    assert resolved == "Where does Aarav work?"
    assert entity is not None and entity.name == "Aarav"


def test_resolve_female_pronoun():
    store = EntityStore()
    store.update("Meera", "person", "female", 0.9)
    store.update("Aarav", "person", "male", 0.9)
    resolved, entity = resolve_pronouns("How old is she?", store)
    assert "Meera" in resolved and "Aarav" not in resolved


def test_resolve_no_guess_returns_unchanged():
    store = EntityStore()
    store.update("Zoho", "org", "unknown", 0.9)  # no matching person
    resolved, entity = resolve_pronouns("Where does he work?", store)
    assert resolved == "Where does he work?" and entity is None


def test_resolve_empty_store_noop():
    resolved, entity = resolve_pronouns("Where does he work?", EntityStore())
    assert resolved == "Where does he work?" and entity is None


# ----------------------------------------------------------- persistence ----

def test_remember_entities_persists_and_dedups(tmp_path):
    engine = MemoryEngine(tmp_path / "memory.db")
    store = EntityStore()
    store.update("Aarav", "person", "male", 0.9)
    store.update("LowConf", "person", "unknown", 0.5)  # below floor: skipped

    assert remember_entities(store, engine) == 1
    # second run must dedup, not duplicate
    assert remember_entities(store, engine) == 0
    facts = engine.active_facts()
    assert len([f for f in facts if f["entity"] == "people"]) == 1
    assert "Aarav" in facts[-1]["content"]
    engine.close()


def test_remember_entities_none_memory():
    assert remember_entities(EntityStore(), None) == 0
