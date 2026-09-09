"""kernel/home/mqtt.py — P4-C: the MQTT bridge (J-16/J-17), the house's nerves.

Research/08 §3: Frigate NVR does local detection; ULTRON subscribes to its
MQTT events instead of running its own NVR. paho-mqtt is the transport
(research pick); the kernel module stays transport-agnostic — the client is
INJECTED, so tests are hermetic (no broker, no network) and a deployment can
swap paho for anything that speaks the four-call protocol:

    connect(host, port, keepalive) · subscribe(topic) · loop_start()/loop_stop()

Every message becomes a typed kernel event:
- `home.mqtt`     — every message (topic + capped payload; JSON parsed when possible)
- `home.detection`— Frigate `frigate/events` payloads distilled to
                    {camera, label, state, score, zones} for the proactive
                    engine (P4-D rules on `home.*`) and the HUD feed.

The bridge is OBSERVE-ONLY: it publishes events, it never actuates. Device
control is the HA MCP mount's job (consent-gated, kernel/home/ha.py).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Protocol

from kernel.bus import EventBus
from kernel.types import Event

log = logging.getLogger(__name__)

__all__ = ["DEFAULT_TOPICS", "FrigateEvent", "MqttBridge", "MqttClient",
           "parse_frigate_event"]

DEFAULT_TOPICS = ("frigate/events", "frigate/events/#")

_PAYLOAD_CAP = 4_000


class MqttClient(Protocol):
    """The four-call surface the bridge needs (paho-mqtt satisfies it)."""

    def connect(self, host: str, port: int, keepalive: int = 60) -> Any: ...

    def subscribe(self, topic: str, qos: int = 0) -> Any: ...

    def loop_start(self) -> Any: ...

    def loop_stop(self) -> Any: ...


@dataclass(frozen=True)
class FrigateEvent:
    """A distilled Frigate detection (J-16/J-17 event shape)."""

    camera: str
    label: str
    state: str                      # "start" | "end" | "update" | ""
    score: float
    zones: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"camera": self.camera, "label": self.label, "state": self.state,
                "score": self.score, "zones": list(self.zones)}


def parse_frigate_event(topic: str, payload: bytes | str) -> FrigateEvent | None:
    """Distill a Frigate `frigate/events` message. Returns None for messages
    that are not object-shaped JSON (heartbeat strings etc.) — never raises."""
    if "frigate" not in topic:
        return None
    try:
        data = json.loads(payload if isinstance(payload, str)
                          else payload.decode("utf-8", errors="replace"))
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    before = data.get("before") if isinstance(data.get("before"), dict) else {}
    after = data.get("after") if isinstance(data.get("after"), dict) else data
    src = after or before
    if not src:
        return None
    try:
        return FrigateEvent(
            camera=str(src.get("camera") or ""),
            label=str(src.get("label") or ""),
            state=str(data.get("type") or src.get("type") or ""),
            score=float(src.get("score") or src.get("top_score") or 0.0),
            zones=tuple(str(z) for z in (src.get("current_zones")
                                         or src.get("entered_zones") or [])),
        )
    except (TypeError, ValueError):
        return None


class MqttBridge:
    """Subscribe-only MQTT → kernel EventBus bridge.

    `loop` is the asyncio loop the EventBus lives on. Deployment: pass it —
    messages arrive on paho's network thread and are bridged with
    run_coroutine_threadsafe. Tests / loop-less use: leave None and the shim
    spins a throwaway loop per publish."""

    def __init__(self, client: MqttClient, bus: EventBus, *,
                 topics: tuple[str, ...] = DEFAULT_TOPICS,
                 host: str = "localhost", port: int = 1883,
                 loop: Any = None) -> None:
        if not topics:
            raise ValueError("MqttBridge needs at least one topic")
        self._client = client
        self._bus = bus
        self._topics = topics
        self._host = host
        self._port = port
        self._loop = loop
        self._started = False
        self.messages_seen = 0

    def start(self) -> None:
        """Connect + subscribe. Connection failures raise cleanly (the wiring
        layer decides whether a missing broker is fatal); once connected the
        bridge degrades per-message (bad payloads are skipped, not fatal)."""
        self._client.connect(self._host, self._port, keepalive=60)
        for topic in self._topics:
            self._client.subscribe(topic, qos=0)
            log.info("home: subscribed %s", topic)
        # paho contract: on_message(client, userdata, message)
        self._client.on_message = self._on_message  # type: ignore[attr-defined]
        self._client.loop_start()
        self._started = True

    def stop(self) -> None:
        if self._started:
            self._client.loop_stop()
            self._started = False

    # -- message path (called on the client's network thread) ----------------

    def _on_message(self, _client: Any, _userdata: Any, message: Any) -> None:
        topic = str(getattr(message, "topic", "") or "")
        payload = getattr(message, "payload", b"") or b""
        self.messages_seen += 1
        self.publish_now(topic, payload)

    def publish_now(self, topic: str, payload: bytes | str) -> list[Event]:
        """Publish one message's kernel events. Returns the events published
        (tests assert on these; the bus receives them through the loop shim)."""
        text: str
        try:
            text = payload if isinstance(payload, str) else payload.decode(
                "utf-8", errors="replace")
        except Exception:  # noqa: BLE001 — payload is untrusted bytes
            text = repr(payload)
        text = text[:_PAYLOAD_CAP]
        try:
            data: Any = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            data = None
        events: list[Event] = [Event(
            type="home.mqtt",
            payload={"topic": topic, "json": data is not None,
                     "payload": text if data is None else "",
                     "data": data if data is not None else None},
            source="mqtt",
        )]
        frigate = parse_frigate_event(topic, text)
        if frigate is not None:
            events.append(Event(type="home.detection",
                                payload=frigate.as_dict(), source="mqtt"))
        import asyncio

        for ev in events:
            if self._loop is not None and self._loop.is_running():
                fut = asyncio.run_coroutine_threadsafe(
                    self._bus.publish(ev), self._loop)
                fut.result(timeout=5.0)
            else:
                asyncio.run(self._bus.publish(ev))
        return events
