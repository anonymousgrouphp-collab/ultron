"""tests/test_memory_guards.py — §P2-B anti-echo memory guards.

Covers the three K9-derived guards (kernel/memory/guards.py) and their wiring
into the two write paths: the explicit save-memory tool and the Consolidator's
judge ADD ops.
"""

from __future__ import annotations

import pytest

from kernel.memory.engine import MemoryEngine
from kernel.memory.guards import (
    TECH_STATE_FLOOR,
    is_echo_narrative,
    strip_store_prefix,
    tech_state_importance,
)


# ------------------------------------------------------------------ echo ----

@pytest.mark.parametrize("text", [
    "Based on my memory, you like filter coffee.",
    "From what I recall you were debugging the sampler.",
    "You mentioned a trip to Manali.",
    "I don't have any record of that.",
    "As I mentioned earlier, the API rate-limits.",
])
def test_echo_narrative_blocks_assistant_chatter(text):
    assert is_echo_narrative(text)


@pytest.mark.parametrize("text", [
    "The user's flight to Delhi is at 8 AM.",
    "User prefers window seats on long flights.",
    "Deploy token for staging is abc123.",
])
def test_echo_narrative_allows_real_facts(text):
    assert not is_echo_narrative(text)


# --------------------------------------------------------------- wrappers ---

@pytest.mark.parametrize("raw, payload", [
    ("remember that my flight is at 8 AM", "my flight is at 8 AM"),
    ("Remember this: the garage code is 1234", "the garage code is 1234"),
    ("store this: I prefer dark mode", "I prefer dark mode"),
    ("fact: the server is in Mumbai", "the server is in Mumbai"),
    ("plain fact without wrapper", "plain fact without wrapper"),
])
def test_strip_store_prefix(raw, payload):
    assert strip_store_prefix(raw) == payload


# ------------------------------------------------------------- tech floor ---

@pytest.mark.parametrize("text", [
    "I am debugging the audio sampling pipeline.",
    "Working on model training code.",
    "The dataset is corrupted again.",
    "Currently refactoring the auth endpoint.",
])
def test_tech_state_floor_lifts_importance(text):
    assert tech_state_importance(text, 0.3) == TECH_STATE_FLOOR
    assert tech_state_importance(text, 0.9) == 0.9  # never a downgrade


def test_non_tech_content_keeps_importance():
    assert tech_state_importance("My favorite color is blue.", 0.4) == 0.4


# ------------------------------------------------------------- wiring -------

def test_consolidator_rejects_echo_add(tmp_path):
    """A judge ADD of assistant narration must be rejected, not stored."""
    from kernel.memory.consolidation import ConsolidationReport, Consolidator
    from kernel.memory.policy import MemoryOp

    engine = MemoryEngine(tmp_path / "memory.db")
    consolidator = Consolidator(engine, gateway=None)
    report = ConsolidationReport()
    op = MemoryOp(op="ADD",
                  content="Based on my memory, you like filter coffee.")
    consolidator._apply_op(report, op, "test")
    assert report.added == []
    assert report.rejected and "echoed" in report.rejected[0][1]
    assert engine.count() == 0
    engine.close()


def test_consolidator_add_applies_tech_floor(tmp_path):
    from kernel.memory.consolidation import ConsolidationReport, Consolidator
    from kernel.memory.policy import MemoryOp

    engine = MemoryEngine(tmp_path / "memory.db")
    consolidator = Consolidator(engine, gateway=None)
    report = ConsolidationReport()
    op = MemoryOp(op="ADD", content="I am debugging the audio pipeline.")
    consolidator._apply_op(report, op, "test")
    assert len(report.added) == 1
    fact = engine.get_fact(report.added[0])
    assert fact.importance == TECH_STATE_FLOOR
    engine.close()


def test_save_memory_handler_strips_wrapper(tmp_path):
    """The explicit save-memory tool stores the payload, not 'remember that'."""
    import asyncio

    from app.handlers import LegacyHandlersMixin

    class Host(LegacyHandlersMixin):
        def __init__(self, engine):
            self._memory = engine

    engine = MemoryEngine(tmp_path / "memory.db")
    host = Host(engine)
    result = asyncio.run(host._handle_save_memory(
        {"category": "notes", "key": "flight", "value": "remember that my flight is at 8 AM"},
        loop=None))
    assert "saved" in result.lower()
    facts = engine.active_facts()
    assert any(f["content"] == "my flight is at 8 AM" for f in facts)
    engine.close()
