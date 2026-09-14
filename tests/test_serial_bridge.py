"""tests/test_serial_bridge.py — PJ-05 (research/13): MCU serial command seam.

Hermetic: pyserial is faked via sys.modules with a recording Serial class;
the boot-settle sleep is stubbed. Pins: newline-terminated sanitized sends,
persistent port (no per-send reopen), clean SerialUnavailable on driver/
port/write failures, WRITE risk + consent gating on hardware_cmd.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from kernel.home.serial_bridge import (
    SerialBridge,
    SerialUnavailable,
    build_hardware_tool,
)
from kernel.policy import Policy, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


def _mkcall(name: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=dict(args), source="test")


class _FakeSerialPort:
    """Recording serial.Serial stand-in (class attributes track opens)."""

    opens = 0
    writes: list[bytes] = []

    def __init__(self, port: str, baud: int, timeout: float = 1.0) -> None:
        self.port, self.baud = port, baud
        self.is_open = True
        _FakeSerialPort.opens += 1

    def write(self, data: bytes) -> int:
        if _FakeSerialPort.writes is None:
            raise OSError("port gone")
        _FakeSerialPort.writes.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    def close(self) -> None:
        self.is_open = False


@pytest.fixture()
def fake_serial(monkeypatch):
    mod = types.ModuleType("serial")
    mod.Serial = _FakeSerialPort
    mod.SerialException = type("SerialException", (OSError,), {})
    monkeypatch.setitem(sys.modules, "serial", mod)
    monkeypatch.setattr("kernel.home.serial_bridge.time.sleep", lambda s: None)
    _FakeSerialPort.opens = 0
    _FakeSerialPort.writes = []
    return mod


def _bridge(**kwargs: object) -> SerialBridge:
    return SerialBridge("COM10", 115200, **kwargs)


class TestSend:
    def test_send_writes_newline_terminated(self, fake_serial) -> None:
        data = _bridge().send("LIGHT_ON")
        assert data == {"sent": "LIGHT_ON", "port": "COM10", "baud": 115200}
        assert _FakeSerialPort.writes == [b"LIGHT_ON\n"]

    def test_port_opened_once_and_held(self, fake_serial) -> None:
        bridge = _bridge()
        bridge.send("LIGHT_ON")
        bridge.send("LIGHT_OFF")
        bridge.send("UNLOCK_PC")
        assert _FakeSerialPort.opens == 1  # MCU reboots on DTR — no per-send open
        assert _FakeSerialPort.writes == [b"LIGHT_ON\n", b"LIGHT_OFF\n",
                                          b"UNLOCK_PC\n"]

    def test_strips_whitespace(self, fake_serial) -> None:
        _bridge().send("  LIGHT_ON  ")
        assert _FakeSerialPort.writes == [b"LIGHT_ON\n"]

    def test_empty_command_refused(self, fake_serial) -> None:
        with pytest.raises(SerialUnavailable, match="empty"):
            _bridge().send("   ")

    def test_embedded_newline_refused(self, fake_serial) -> None:
        with pytest.raises(SerialUnavailable, match="single line"):
            _bridge().send("LIGHT_ON\nrm -rf")

    def test_overlong_command_refused(self, fake_serial) -> None:
        with pytest.raises(SerialUnavailable, match="64"):
            _bridge().send("X" * 65)

    def test_write_failure_is_clean_and_resets_port(self, fake_serial) -> None:
        bridge = _bridge()
        bridge.send("LIGHT_ON")  # establish
        _FakeSerialPort.writes = None            # next write raises OSError
        with pytest.raises(SerialUnavailable, match="write failed"):
            bridge.send("LIGHT_OFF")
        assert bridge._serial is None            # dead port dropped
        _FakeSerialPort.writes = []
        bridge.send("LIGHT_OFF")                 # reopens fresh
        assert _FakeSerialPort.opens == 2

    def test_no_port_configured_refused(self, fake_serial) -> None:
        with pytest.raises(SerialUnavailable, match="no serial port"):
            SerialBridge("").send("LIGHT_ON")

    def test_missing_driver_refused(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "serial", None)  # import -> ImportError
        with pytest.raises(SerialUnavailable, match="pyserial is not installed"):
            SerialBridge("COM10").send("LIGHT_ON")

    def test_close(self, fake_serial) -> None:
        bridge = _bridge()
        bridge.send("LIGHT_ON")
        bridge.close()
        assert bridge._serial is None


class TestTool:
    def test_registration_write_risk(self, fake_serial) -> None:
        reg = ToolRegistry()
        build_hardware_tool(reg, _bridge())
        assert reg.risks()["hardware_cmd"] is RiskClass.WRITE

    def test_tool_send(self, fake_serial) -> None:
        reg = ToolRegistry()
        build_hardware_tool(reg, _bridge())
        res = asyncio.run(reg.execute(_mkcall("hardware_cmd", command="LIGHT_ON")))
        assert res.ok and res.data["sent"] == "LIGHT_ON"

    def test_tool_unavailable_fails_clean(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "serial", None)
        reg = ToolRegistry()
        build_hardware_tool(reg, SerialBridge("COM10"))
        res = asyncio.run(reg.execute(_mkcall("hardware_cmd", command="LIGHT_ON")))
        assert not res.ok
        assert "pyserial is not installed" in (res.error or "")

    def test_policy_gates_hardware_cmd_behind_consent(self, fake_serial) -> None:
        reg = ToolRegistry()
        build_hardware_tool(reg, _bridge())
        engine = PolicyEngine(policy=Policy.default())
        result = asyncio.run(engine.run(
            _mkcall("hardware_cmd", command="UNLOCK_PC"), reg))
        assert not result.ok  # WRITE with no consent callback -> fail-safe deny
        assert "consent" in (result.error or "")
