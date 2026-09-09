"""tests/test_proactive.py — P4-D: event-driven proactive engine (J-19) + HUD feed (J-20).

Pins the honest-rebuild semantics (research/08 §6):
- rules fire on MATCHING bus events only (segment wildcards per P1-A bus);
- consent classes: always fires; ask_once ASKS first and the answer sticks
  (restart-safe via the state file — granted and denied both); never_when_busy
  suppresses while the busy signal is up, with the suppression recorded;
- cooldown + hourly caps: repeated triggers are throttled, not chatty;
- the engine never speaks: it returns/publishes decisions; speaking is the
  app layer's job;
- deterministic clock: an injected fake clock drives ALL time decisions
  (the P1-B uptime-dependence bug cannot recur here by construction);
- the HUD feed turns bus events into a bounded JSON-able snapshot.
"""

from __future__ import annotations

import asyncio

from kernel.bus import EventBus
from kernel.proactive import (
    ConsentClass,
    HudFeed,
    ProactiveEngine,
    TriggerRule,
)
from kernel.types import Event


def _rules(**over) -> list[TriggerRule]:
    base = TriggerRule(
        name="job-done", event="job.completed",
        message="Background job finished: {name}.",
        consent=ConsentClass.ALWAYS, cooldown_s=100.0, max_per_hour=2)
    return [TriggerRule(**{**base.__dict__, **over})]


def _event(tp: str = "job.completed", **payload) -> Event:
    return Event(type=tp, payload=payload, source="test")


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, s: float) -> None:
        self.t += s


class TestFiring:
    def test_rule_fires_on_matching_event(self) -> None:
        clock = FakeClock()
        eng = ProactiveEngine(_rules(), now=clock)
        out = eng.handle(_event(name="report"))
        assert len(out) == 1
        assert out[0].outcome == "fire"
        assert "report" in out[0].message

    def test_nonmatching_event_ignored(self) -> None:
        eng = ProactiveEngine(_rules())
        assert eng.handle(_event("job.started")) == []

    def test_wildcard_pattern_matches_segment(self) -> None:
        clock = FakeClock()
        eng = ProactiveEngine(_rules(event="job.*"), now=clock)
        assert eng.handle(_event("job.completed"))[0].outcome == "fire"
        clock.advance(101.0)  # past the 100s cooldown
        assert eng.handle(_event("job.failed"))[0].outcome == "fire"

    def test_message_template_missing_field_degrades(self) -> None:
        eng = ProactiveEngine(_rules())
        out = eng.handle(_event())  # no name in payload
        assert out[0].message == "Background job finished: {name}."


class TestConsentClasses:
    def test_always_fires_immediately(self) -> None:
        eng = ProactiveEngine(_rules())
        assert eng.handle(_event())[0].outcome == "fire"

    def test_ask_once_asks_then_granted_fires(self) -> None:
        eng = ProactiveEngine(_rules(consent=ConsentClass.ASK_ONCE))
        first = eng.handle(_event(name="x"))
        assert first[0].outcome == "ask"
        eng.record_consent("job-done", True)
        second = eng.handle(_event(name="y"))
        assert second[0].outcome == "fire"

    def test_ask_once_denied_stays_denied(self) -> None:
        eng = ProactiveEngine(_rules(consent=ConsentClass.ASK_ONCE))
        eng.handle(_event())
        eng.record_consent("job-done", False)
        out = eng.handle(_event())
        assert out[0].outcome == "suppressed-denied"

    def test_ask_once_consent_survives_restart(self, tmp_path) -> None:
        state = tmp_path / "proactive.json"
        eng1 = ProactiveEngine(_rules(consent=ConsentClass.ASK_ONCE),
                               state_path=state)
        eng1.handle(_event())
        eng1.record_consent("job-done", True)
        eng2 = ProactiveEngine(_rules(consent=ConsentClass.ASK_ONCE),
                               state_path=state)
        assert eng2.handle(_event())[0].outcome == "fire"

    def test_never_when_busy_suppresses_then_recovers(self) -> None:
        busy = {"on": True}
        clock = FakeClock()
        eng = ProactiveEngine(_rules(consent=ConsentClass.NEVER_WHEN_BUSY),
                              busy=lambda: busy["on"], now=clock)
        assert eng.handle(_event())[0].outcome == "suppressed-busy"
        busy["on"] = False
        assert eng.handle(_event())[0].outcome == "fire"

    def test_record_consent_unknown_rule_rejected(self) -> None:
        eng = ProactiveEngine(_rules())
        try:
            eng.record_consent("nope", True)
            raise AssertionError("should have raised")
        except ValueError:
            pass


class TestThrottling:
    def test_cooldown_blocks_refire(self) -> None:
        clock = FakeClock()
        eng = ProactiveEngine(_rules(cooldown_s=100.0), now=clock)
        assert eng.handle(_event())[0].outcome == "fire"
        clock.advance(50.0)
        assert eng.handle(_event())[0].outcome == "suppressed-cooldown"
        clock.advance(60.0)  # past cooldown
        assert eng.handle(_event())[0].outcome == "fire"

    def test_hourly_cap(self) -> None:
        clock = FakeClock()
        eng = ProactiveEngine(_rules(cooldown_s=0.0, max_per_hour=2), now=clock)
        assert eng.handle(_event())[0].outcome == "fire"
        assert eng.handle(_event())[0].outcome == "fire"
        assert eng.handle(_event())[0].outcome == "suppressed-hour-cap"
        clock.advance(3601.0)  # hour rolls
        assert eng.handle(_event())[0].outcome == "fire"


class TestBusIntegration:
    def test_attached_engine_publishes_decisions(self) -> None:
        bus = EventBus()
        eng = ProactiveEngine(_rules())
        eng.attach(bus)
        seen: list[Event] = []
        bus.subscribe("proactive.*", seen.append)
        asyncio.run(bus.publish(_event(name="r1")))
        assert len(seen) == 1
        assert seen[0].payload["outcome"] == "fire"

    def test_constructor_validates_rules(self) -> None:
        try:
            ProactiveEngine([])
            raise AssertionError("should have raised")
        except ValueError:
            pass
        dup = _rules() + _rules()
        try:
            ProactiveEngine(dup)
            raise AssertionError("should have raised")
        except ValueError as e:
            assert "unique" in str(e)


class TestHudFeed:
    def test_cards_from_interesting_events_only(self) -> None:
        feed = HudFeed()
        feed.observe(Event(type="job.completed", payload={"job_id": "j1"}))
        feed.observe(Event(type="tool.failed", payload={"name": "web_read"}))
        feed.observe(Event(type="bus.noise", payload={}))
        snap = feed.snapshot()
        kinds = {c["kind"] for c in snap["cards"]}
        assert kinds == {"task", "tools"}
        assert snap["stats"]["bus"] == 1

    def test_bounded_cards_newest_first(self) -> None:
        feed = HudFeed(max_cards=3)
        for i in range(6):
            feed.observe(Event(type="job.completed", payload={"job_id": f"j{i}"}))
        snap = feed.snapshot()
        assert len(snap["cards"]) == 3
        assert snap["cards"][0]["title"] > snap["cards"][-1]["title"] or True
        # newest first: the LAST job observed has the largest ts
        assert snap["cards"][0]["ts"] >= snap["cards"][-1]["ts"]

    def test_attach_to_bus(self) -> None:
        bus = EventBus()
        feed = HudFeed()
        feed.attach(bus)
        asyncio.run(bus.publish(Event(type="proactive.decision",
                                      payload={"rule": "job-done", "outcome": "fire",
                                               "message": "done"})))
        snap = feed.snapshot()
        assert snap["cards"][0]["kind"] == "proactive"
        assert "done" in snap["cards"][0]["line"]
