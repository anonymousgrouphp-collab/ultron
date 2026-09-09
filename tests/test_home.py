"""tests/test_home.py — P4-C: HA MCP mount (J-14) + MQTT/Frigate bridge (J-16/J-17).

Hermetic throughout:
- HA mount: in-process FastMCP fixture standing in for the HA MCP-Assist
  server (P2-B's stdio/in-proc pattern) — validates URL/token gating, the
  structural risk map (discovery READ / control EXECUTE / locks-alarms
  DESTRUCTIVE), and that unconfigured = never mounts (the P2-D gate shape);
- MQTT bridge: a fake client (recorded connect/subscribe calls, injected
  messages) — no broker; Frigate payload distillation pinned including the
  malformed-message degradation;
- proactive-engine integration: a `home.detection` event drives a P4-D rule
  end-to-end (the mansion → butler loop at unit scale).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest
from fastmcp import FastMCP

from kernel import RiskClass, ToolCall
from kernel.bus import EventBus
from kernel.home import MqttBridge, ha_risk_overrides, parse_frigate_event
from kernel.home.ha import mount_home_assistant
from kernel.policy import Policy, PolicyEngine
from kernel.proactive import ConsentClass, ProactiveEngine, TriggerRule
from kernel.tools import ToolRegistry


# ------------------------------------------------------------------ HA risk map

class TestHaRiskMap:
    def test_discovery_tools_are_read(self) -> None:
        m = ha_risk_overrides(["get_state", "list_entities", "camera_snapshot",
                               "weather_forecast"])
        assert m["get_state"] is RiskClass.READ
        assert m["list_entities"] is RiskClass.READ
        assert m["camera_snapshot"] is RiskClass.READ
        assert m["weather_forecast"] is RiskClass.READ

    def test_control_tools_are_execute(self) -> None:
        m = ha_risk_overrides(["turn_on", "turn_off", "toggle", "call_service",
                               "set_temperature", "play_media"])
        assert all(v is RiskClass.EXECUTE for v in m.values())

    def test_safety_critical_escalates_over_control(self) -> None:
        m = ha_risk_overrides(["lock_front", "unlock_back", "alarm_control",
                               "garage_door_open", "arm_away"])
        assert all(v is RiskClass.DESTRUCTIVE for v in m.values())

    def test_unmapped_names_absent(self) -> None:
        m = ha_risk_overrides(["turn_on"])
        assert "turn_on" in m and set(m) == {"turn_on"}


# ------------------------------------------------------------------ HA mount

def _fixture_ha_server() -> FastMCP:
    """In-process stand-in for HA MCP-Assist (the P2-B in-proc pattern)."""
    mcp = FastMCP("fixture-ha")

    @mcp.tool
    def get_state(entity_id: str) -> str:  # noqa: ANN001 — FastMCP parses sig
        """get one entity's state"""
        return f"{entity_id}=on"

    @mcp.tool
    def turn_on(entity_id: str) -> str:  # noqa: ANN001
        """turn an entity on"""
        return f"turned on {entity_id}"

    @mcp.tool
    def lock_front_door() -> str:
        """lock the front door"""
        return "locked"
    return mcp


def _make_ha_available(monkeypatch, url: str) -> None:
    """Redirect mount_home_assistant's HTTP transport to the in-proc server."""
    import kernel.home.ha as ha_mod

    server = _fixture_ha_server()

    async def fake_mount_http(target, *, prefix, label, headers=None,
                              timeout_s=30.0, default_risk=None,
                              risk_overrides=None, include=None):
        from kernel.mcp_client import mount_inproc

        return await mount_inproc(server, prefix=prefix, label=label,
                                  default_risk=default_risk or RiskClass.EXECUTE,
                                  risk_overrides=risk_overrides or {},
                                  timeout_s=timeout_s)
    monkeypatch.setattr(ha_mod, "mount_http", fake_mount_http)


class TestHaMount:
    def test_unconfigured_never_mounts(self) -> None:
        async def go():
            reg = ToolRegistry()
            with pytest.raises(ValueError, match="not configured"):
                await mount_home_assistant(None, reg)
            assert reg.names() == ()
        asyncio.run(go())

    def test_config_validation(self) -> None:
        async def go():
            reg = ToolRegistry()
            with pytest.raises(ValueError, match="url"):
                await mount_home_assistant({"token": "x"}, reg)
            with pytest.raises(ValueError, match="token"):
                await mount_home_assistant({"url": "http://ha.local:8123"}, reg)
            with pytest.raises(ValueError, match="http"):
                await mount_home_assistant({"url": "ftp://x", "token": "t"}, reg)
        asyncio.run(go())

    def test_mount_applies_structural_risk_map(self, monkeypatch) -> None:
        _make_ha_available(monkeypatch, "http://ha.local:8123")

        async def go():
            reg = ToolRegistry()
            mount = await mount_home_assistant(
                {"url": "http://ha.local:8123", "token": "secret-token"}, reg)
            try:
                risks = reg.risks()
                assert risks["ha_get_state"] is RiskClass.READ
                assert risks["ha_turn_on"] is RiskClass.EXECUTE
                assert risks["ha_lock_front_door"] is RiskClass.DESTRUCTIVE
                # the default policy DENIES the door lock outright
                engine = PolicyEngine(policy=Policy.default())
                call = ToolCall(id="c1", name="ha_lock_front_door", args={},
                                source="test")
                res = await engine.run(call, reg)
                assert not res.ok and "denied by policy" in (res.error or "")
                # discovery flows without consent
                call_read = ToolCall(id="c2", name="ha_get_state",
                                    args={"entity_id": "light.desk"}, source="test")
                res_read = await engine.run(call_read, reg)
                assert res_read.ok and res_read.data == "light.desk=on"
            finally:
                await mount.stop()
        asyncio.run(go())

    def test_control_requires_consent(self, monkeypatch) -> None:
        _make_ha_available(monkeypatch, "http://ha.local:8123")

        async def go():
            reg = ToolRegistry()
            mount = await mount_home_assistant(
                {"url": "http://ha.local:8123", "token": "t"}, reg)
            try:
                engine = PolicyEngine(policy=Policy.default())
                call = ToolCall(id="c3", name="ha_turn_on",
                                args={"entity_id": "light.desk"}, source="test")
                denied = await engine.run(call, reg)      # no consent -> deny
                assert not denied.ok and "consent" in (denied.error or "")

                async def allow(c, r):  # noqa: ANN001
                    return True
                ok = await engine.run(call, reg, consent=allow)
                assert ok.ok and ok.data == "turned on light.desk"
            finally:
                await mount.stop()
        asyncio.run(go())


# ------------------------------------------------------------------ MQTT bridge

class FakeMqttClient:
    """Records calls; on_message is set by the bridge (paho contract)."""

    def __init__(self) -> None:
        self.connected: list[tuple[str, int]] = []
        self.subscribed: list[str] = []
        self.started = False
        self.on_message: Any = None

    def connect(self, host: str, port: int, keepalive: int = 60) -> None:
        self.connected.append((host, port))

    def subscribe(self, topic: str, qos: int = 0) -> None:
        self.subscribed.append(topic)

    def loop_start(self) -> None:
        self.started = True

    def loop_stop(self) -> None:
        self.started = False


class FakeMessage:
    def __init__(self, topic: str, payload: bytes) -> None:
        self.topic = topic
        self.payload = payload


FRIGATE_START = json.dumps({
    "before": {"camera": "front_door", "label": "person", "current_zones": [],
               "score": 0.0},
    "after": {"camera": "front_door", "label": "person",
              "current_zones": ["porch"], "score": 0.88},
    "type": "start",
})


class TestFrigateParsing:
    def test_start_event_distilled(self) -> None:
        ev = parse_frigate_event("frigate/events", FRIGATE_START)
        assert ev is not None
        assert ev.camera == "front_door"
        assert ev.label == "person"
        assert ev.state == "start"
        assert ev.score == pytest.approx(0.88)
        assert ev.zones == ("porch",)

    def test_end_event_takes_before_state(self) -> None:
        payload = json.dumps({
            "before": {"camera": "porch", "label": "package",
                       "current_zones": ["porch"], "score": 0.7},
            "after": {}, "type": "end"})
        ev = parse_frigate_event("frigate/events", payload)
        assert ev is not None
        assert ev.label == "package" and ev.state == "end"
        assert ev.score == pytest.approx(0.7)

    def test_non_frigate_topic_ignored(self) -> None:
        assert parse_frigate_event("home/other", FRIGATE_START) is None

    def test_garbage_payload_never_raises(self) -> None:
        assert parse_frigate_event("frigate/events", "not json") is None
        assert parse_frigate_event("frigate/events", b"\xff\xfe") is None
        assert parse_frigate_event("frigate/events", "[1,2,3]") is None


class TestMqttBridge:
    def test_start_connects_and_subscribes(self) -> None:
        bus = EventBus()
        client = FakeMqttClient()
        bridge = MqttBridge(client, bus, host="mqtt.local", port=1884)
        bridge.start()
        try:
            assert client.connected == [("mqtt.local", 1884)]
            assert len(client.subscribed) == len(bridge._topics)
            assert all(t.startswith("frigate") for t in client.subscribed)
            assert client.started
        finally:
            bridge.stop()
            assert not client.started

    def test_empty_topics_rejected(self) -> None:
        with pytest.raises(ValueError):
            MqttBridge(FakeMqttClient(), EventBus(), topics=())

    def test_message_bridges_to_bus(self) -> None:
        bus = EventBus()
        seen: list[Any] = []
        bus.subscribe("home.*", seen.append)
        bridge = MqttBridge(FakeMqttClient(), bus)
        events = bridge.publish_now("home/switch", '{"state": "on"}')
        assert len(events) == 1
        assert events[0].type == "home.mqtt"
        assert events[0].payload["json"] is True
        assert events[0].payload["data"] == {"state": "on"}
        assert len(seen) == 1

    def test_frigate_message_emits_detection(self) -> None:
        bus = EventBus()
        seen: list[Any] = []
        bus.subscribe("home.detection", seen.append)
        bridge = MqttBridge(FakeMqttClient(), bus)
        events = bridge.publish_now("frigate/events", FRIGATE_START)
        assert [e.type for e in events] == ["home.mqtt", "home.detection"]
        det = events[1].payload
        assert det["label"] == "person" and det["camera"] == "front_door"
        assert seen and seen[0].payload["zones"] == ["porch"]

    def test_on_message_callback_counts(self) -> None:
        bus = EventBus()
        bridge = MqttBridge(FakeMqttClient(), bus)
        bridge._on_message(None, None, FakeMessage("frigate/events",
                                                   FRIGATE_START.encode()))
        assert bridge.messages_seen == 1

    def test_payload_cap(self) -> None:
        bus = EventBus()
        bridge = MqttBridge(FakeMqttClient(), bus)
        big = "x" * 10_000
        events = bridge.publish_now("home/big", big)
        assert len(events[0].payload["payload"]) == 4_000


# --------------------------------------------------- mansion → butler loop

class TestProactiveIntegration:
    def test_detection_drives_proactive_rule(self) -> None:
        """The J-17 loop at unit scale: person detected at the front door →
        the P4-D engine fires (ask-once suppressed, then granted fires)."""
        bus = EventBus()
        engine = ProactiveEngine([
            TriggerRule(
                name="door-person", event="home.detection",
                message="Sir, someone is at the {camera} ({label}).",
                consent=ConsentClass.ALWAYS, cooldown_s=60.0, max_per_hour=3),
        ])
        engine.attach(bus)
        emissions: list[Any] = []
        bus.subscribe("proactive.decision",
                      lambda e: emissions.append(e.payload))
        bridge = MqttBridge(FakeMqttClient(), bus)
        bridge.publish_now("frigate/events", FRIGATE_START)
        assert emissions
        first = emissions[0]
        assert first["outcome"] == "fire"
        assert "front_door" in first["message"] and "person" in first["message"]

    def test_second_detection_within_cooldown_suppressed(self) -> None:
        bus = EventBus()
        engine = ProactiveEngine([
            TriggerRule(name="door-person", event="home.detection",
                        message="someone at the door",
                        consent=ConsentClass.ALWAYS, cooldown_s=600.0),
        ])
        engine.attach(bus)
        outcomes: list[str] = []
        bus.subscribe("proactive.decision",
                      lambda e: outcomes.append(e.payload["outcome"]))
        bridge = MqttBridge(FakeMqttClient(), bus)
        bridge.publish_now("frigate/events", FRIGATE_START)
        bridge.publish_now("frigate/events", FRIGATE_START)
        assert outcomes == ["fire", "suppressed-cooldown"]
