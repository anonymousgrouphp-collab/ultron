"""kernel/home/serial_bridge.py — PJ-05: MCU serial command seam (research/13).

The surajmaru repo's hardware story (ESP32 on a COM port; `LIGHT_ON` /
`LIGHT_OFF` / `UNLOCK_PC` newline-terminated commands) rebuilt as a kernel
seam for the parked hardware stream (HA box / D13 area). The genuinely novel
capability it unlocks: an ESP32 flashed as a USB HID keyboard can type the
Windows password AT the login screen — Credential Provider isolation means
no software path can ever do that (`hardware_cmd UNLOCK_PC` is just a serial
write; the typing happens on the MCU).

Discipline:
- ONE seam: pyserial, lazily imported — absent driver → clean EngineUnavailable
  result, never a crash (same degradation contract as kernel/voice engines);
- the port is opened ONCE and held (MCUs reboot on DTR — per-send open/close
  would reset the board mid-job); the first open waits out the bootloader;
- flag-gated OFF by default (`serial_bridge_enabled` + `serial_port`);
  every send is WRITE risk → the consent gate asks;
- commands are sanitized (stripped, newline-free, length-capped) — the MCU
  firmware owns what they mean.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["SerialBridge", "SerialUnavailable"]

_MAX_CMD_LEN = 64
_BOOT_SETTLE_S = 2.0


class SerialUnavailable(RuntimeError):
    """pyserial missing or the port cannot be opened — a clean, expected state."""


class SerialBridge:
    """Persistent serial command channel to an MCU (ESP32/Arduino)."""

    def __init__(self, port: str, baud: int = 115200) -> None:
        self._port = (port or "").strip()
        self._baud = int(baud)
        self._lock = threading.Lock()
        self._serial: Any = None  # serial.Serial once opened

    @property
    def port(self) -> str:
        return self._port

    @property
    def baud(self) -> int:
        return self._baud

    def available(self) -> bool:
        """True when the pyserial driver is importable and a port is set."""
        if not self._port:
            return False
        try:
            import serial  # noqa: F401 — availability probe only
        except ImportError:
            return False
        return True

    def _ensure_open(self) -> None:
        if self._serial is not None and getattr(self._serial, "is_open", False):
            return
        try:
            import serial
        except ImportError as exc:
            raise SerialUnavailable("pyserial is not installed") from exc
        if not self._port:
            raise SerialUnavailable("no serial port configured")
        try:
            self._serial = serial.Serial(self._port, self._baud, timeout=1.0)
        except (OSError, serial.SerialException) as exc:
            self._serial = None
            raise SerialUnavailable(
                f"could not open {self._port}: {type(exc).__name__}") from exc
        time.sleep(_BOOT_SETTLE_S)  # Arduino/ESP32 reset on DTR — boot settle

    def send(self, command: str) -> dict[str, object]:
        """Send one newline-terminated command. Returns a structured result;
        raises SerialUnavailable (never raw exceptions) for expected failures."""
        cmd = (command or "").strip()
        if not cmd:
            raise SerialUnavailable("empty command")
        if "\n" in cmd or "\r" in cmd or len(cmd) > _MAX_CMD_LEN:
            raise SerialUnavailable(
                f"command must be a single line of at most {_MAX_CMD_LEN} chars")
        with self._lock:
            self._ensure_open()  # SerialUnavailable propagates clean
            try:
                self._serial.write((cmd + "\n").encode("ascii", errors="replace"))
                self._serial.flush()
            except OSError as exc:
                # SerialException subclasses OSError; a failed write leaves the
                # port dead — drop it so the next send reopens fresh
                self._serial = None
                raise SerialUnavailable(
                    f"serial write failed: {type(exc).__name__}") from exc
        log.info("serial: %r -> %s @ %d", cmd, self._port, self._baud)
        return {"sent": cmd, "port": self._port, "baud": self._baud}

    def close(self) -> None:
        with self._lock:
            if self._serial is not None:
                try:
                    self._serial.close()
                except OSError:  # pragma: no cover — best-effort cleanup
                    pass
                self._serial = None


def build_hardware_tool(registry, bridge: SerialBridge) -> None:
    """Register `hardware_cmd` (PJ-05) — WRITE risk; flag-gated by the caller
    (config `serial_bridge_enabled` + `serial_port`); default OFF."""
    from kernel.types import RiskClass, ToolCall, ToolResult

    @registry.tool(
        name="hardware_cmd",
        description="Send a control command to a connected microcontroller "
                    "over serial (e.g. LIGHT_ON, LIGHT_OFF, UNLOCK_PC — the "
                    "firmware defines the vocabulary). Requires the MCU on "
                    "the configured COM port.",
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "command word, e.g. LIGHT_ON"},
        }, "required": ["command"]},
        risk=RiskClass.WRITE, timeout_s=15.0, max_retries=0)
    def hardware_cmd(call: ToolCall) -> ToolResult:
        command = str(call.args.get("command") or "")
        try:
            data = bridge.send(command)
        except SerialUnavailable as exc:
            return ToolResult.fail(call, str(exc), risk=RiskClass.WRITE)
        return ToolResult.success(call, data=data, risk=RiskClass.WRITE)
