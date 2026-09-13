"""tests/test_bus_hardening.py — §P3 bus timeout guard (K9-derived).

A hung async subscriber must not freeze the publisher: it gets cancelled and
logged, and later subscribers still receive the event. Style follows the repo
convention: asyncio.run() inside sync tests (no pytest-asyncio plugin).
"""

from __future__ import annotations

import asyncio

import pytest

from kernel.bus import EventBus
from kernel.types import Event


def test_hung_subscriber_times_out_and_does_not_block():
    bus = EventBus(handler_timeout_s=0.05)

    async def hung(event):
        await asyncio.sleep(30)

    delivered: list[str] = []

    def quick(event):
        delivered.append(event.type)

    bus.subscribe("*", hung)   # subscribes first — would block delivery
    bus.subscribe("*", quick)  # must still fire after the timeout

    async def scenario():
        return await asyncio.wait_for(
            bus.publish(Event(type="test.tick")), timeout=2.0)

    stamped = asyncio.run(scenario())
    assert stamped.seq >= 1
    assert delivered == ["test.tick"]


def test_timeout_none_is_unbounded_legacy():
    assert EventBus(handler_timeout_s=None)._handler_timeout_s is None


def test_invalid_timeout_rejected():
    with pytest.raises(ValueError):
        EventBus(handler_timeout_s=0)


def test_serial_order_and_isolation_preserved():
    """Existing contract intact: subscription order + crash isolation."""
    bus = EventBus(handler_timeout_s=1.0)
    order: list[str] = []

    def first(event):
        order.append("first")
        raise RuntimeError("boom")  # isolation: must not stop `second`

    def second(event):
        order.append("second")

    bus.subscribe("*", first)
    bus.subscribe("*", second)
    asyncio.run(bus.publish(Event(type="a.b")))
    assert order == ["first", "second"]
