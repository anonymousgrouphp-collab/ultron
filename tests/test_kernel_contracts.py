"""P1-A tests: kernel contracts (types + EventBus). Hermetic, stdlib-only."""

import asyncio

import pytest

from kernel import Event, EventBus, RiskClass, ToolCall, ToolResult


# ---------------------------------------------------------------- types

def test_01_event_requires_type():
    with pytest.raises(ValueError):
        Event(type="")


def test_02_toolcall_validation():
    with pytest.raises(ValueError):
        ToolCall(id="", name="x")
    with pytest.raises(ValueError):
        ToolCall(id="1", name="")
    call = ToolCall(id="1", name="open_app", args={"app": "notepad"}, source="voice")
    assert call.source == "voice"


def test_03_toolresult_ok_helper():
    call = ToolCall(id="c1", name="read_file")
    res = ToolResult.ok(call, data="hello", risk=RiskClass.READ, duration_ms=12.5,
                        artifacts=("/tmp/a.txt",))
    assert res.ok and res.data == "hello" and res.error is None
    assert res.risk is RiskClass.READ and res.duration_ms == 12.5
    assert res.artifacts == ("/tmp/a.txt",) and res.call_id == "c1"


def test_04_toolresult_fail_helper():
    call = ToolCall(id="c2", name="delete_file")
    res = ToolResult.fail(call, "file not found", risk=RiskClass.DESTRUCTIVE)
    assert res.ok is False and res.error == "file not found"
    assert res.risk is RiskClass.DESTRUCTIVE and res.data is None
    with pytest.raises(ValueError):
        ToolResult.fail(call, "")  # raw-empty errors forbidden


def test_05_risk_classes_complete():
    assert [r.value for r in RiskClass] == ["read", "write", "execute", "destructive"]


def test_06_results_are_immutable():
    call = ToolCall(id="c3", name="x")
    res = ToolResult.ok(call)
    with pytest.raises(Exception):  # FrozenInstanceError
        res.ok = False


# ---------------------------------------------------------------- bus delivery

def test_07_publish_stamps_seq_and_ts():
    bus = EventBus()
    ev = asyncio.run(bus.publish(Event(type="voice.wake")))
    assert ev.seq == 1 and ev.ts > 0
    ev2 = asyncio.run(bus.publish(Event(type="voice.wake")))
    assert ev2.seq == 2


def test_08_exact_pattern_delivery():
    bus = EventBus()
    seen = []
    bus.subscribe("tool.completed", lambda e: seen.append(e.payload))
    asyncio.run(bus.publish(Event(type="tool.completed", payload={"n": 1})))
    asyncio.run(bus.publish(Event(type="tool.started", payload={"n": 2})))
    assert seen == [{"n": 1}]


def test_09_prefix_wildcard_is_segment_based():
    bus = EventBus()
    seen = []
    bus.subscribe("tool.*", lambda e: seen.append(e.type))
    asyncio.run(bus.publish(Event(type="tool.completed")))
    asyncio.run(bus.publish(Event(type="toolbox.filled")))   # must NOT match
    asyncio.run(bus.publish(Event(type="tool")))             # bare prefix — not a match
    assert seen == ["tool.completed"]


def test_10_star_matches_everything():
    bus = EventBus()
    seen = []
    bus.subscribe("*", lambda e: seen.append(e.type))
    for t in ("voice.wake", "tool.completed", "system.alert"):
        asyncio.run(bus.publish(Event(type=t)))
    assert seen == ["voice.wake", "tool.completed", "system.alert"]


def test_11_multiple_subscribers_in_order():
    bus = EventBus()
    order = []
    bus.subscribe("job.progress", lambda e: order.append("first"))
    bus.subscribe("job.progress", lambda e: order.append("second"))
    asyncio.run(bus.publish(Event(type="job.progress")))
    assert order == ["first", "second"]


def test_12_subscriber_error_is_isolated():
    bus = EventBus()
    seen = []

    def bad(_):
        raise RuntimeError("subscriber exploded")

    bus.subscribe("system.alert", bad)
    bus.subscribe("system.alert", lambda e: seen.append(e.seq))
    stamped = asyncio.run(bus.publish(Event(type="system.alert")))
    assert seen == [stamped.seq]          # good subscriber still delivered
    assert bus.history[-1].type == "system.alert"


def test_13_unsubscribe_stops_delivery():
    bus = EventBus()
    seen = []
    sub = bus.subscribe("voice.wake", lambda e: seen.append(e.seq))
    asyncio.run(bus.publish(Event(type="voice.wake")))
    bus.unsubscribe(sub)
    asyncio.run(bus.publish(Event(type="voice.wake")))
    assert len(seen) == 1 and bus.subscriber_count == 0


def test_14_history_ring_is_bounded():
    bus = EventBus(history_size=5)
    for i in range(8):
        asyncio.run(bus.publish(Event(type=f"tool.step{i}")))
    hist = bus.history
    assert len(hist) == 5
    assert [e.seq for e in hist] == [4, 5, 6, 7, 8]


def test_15_sync_handler_supported():
    bus = EventBus()
    seen = []
    bus.subscribe("memory.saved", lambda e: seen.append(e.type))
    asyncio.run(bus.publish(Event(type="memory.saved")))
    assert seen == ["memory.saved"]


def test_16_async_handler_awaited():
    bus = EventBus()
    seen = []

    async def handler(e):
        seen.append(e.type)

    bus.subscribe("voice.wake", handler)
    asyncio.run(bus.publish(Event(type="voice.wake")))
    assert seen == ["voice.wake"]


def test_17_subscription_validation_and_cancel():
    bus = EventBus()
    with pytest.raises(ValueError):
        bus.subscribe("", lambda e: None)
    sub = bus.subscribe("tool.*", lambda e: None)
    sub.cancel()
    assert sub.active is False
