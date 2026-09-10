"""kernel/sync/context.py — Phase S2: cross-device context sharing.

Enables sharing context between devices (phone, desktop, etc.):
1. Session continuation ("Continue what I was doing on my computer")
2. Shared memory across devices
3. Device-aware proactive suggestions
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = ["CrossDeviceContext", "DeviceState"]


@dataclass
class DeviceState:
    """State of a connected device."""

    device_id: str
    device_type: str  # phone, desktop, tablet
    last_seen: float = 0.0
    current_task: str = ""
    session_id: str | None = None


@dataclass
class CrossDeviceContext:
    """Shares context between devices.

    Parameters
    ----------
    data_dir:
        Directory to store sync state (default: .ultron/sync/).
    """

    data_dir: Path | None = None
    _devices: dict[str, DeviceState] = field(default_factory=dict)
    _current_device: str | None = None

    def __post_init__(self) -> None:
        if self.data_dir:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._load_state()

    def _load_state(self) -> None:
        """Load sync state from disk."""
        if not self.data_dir:
            return
        state_file = self.data_dir / "devices.json"
        if state_file.exists():
            try:
                data = json.loads(state_file.read_text())
                for did, info in data.items():
                    self._devices[did] = DeviceState(
                        device_id=did,
                        device_type=info.get("device_type", "unknown"),
                        last_seen=info.get("last_seen", 0),
                        current_task=info.get("current_task", ""),
                        session_id=info.get("session_id"),
                    )
            except Exception:
                pass

    def _save_state(self) -> None:
        """Save sync state to disk."""
        if not self.data_dir:
            return
        try:
            state_file = self.data_dir / "devices.json"
            data = {}
            for did, device in self._devices.items():
                data[did] = {
                    "device_type": device.device_type,
                    "last_seen": device.last_seen,
                    "current_task": device.current_task,
                    "session_id": device.session_id,
                }
            state_file.write_text(json.dumps(data, indent=2))
        except Exception:
            pass

    def register_device(self, device_id: str, device_type: str) -> DeviceState:
        """Register a new device."""
        device = DeviceState(
            device_id=device_id,
            device_type=device_type,
            last_seen=time.time(),
        )
        self._devices[device_id] = device
        self._save_state()
        return device

    def update_device(self, device_id: str, **kwargs: Any) -> None:
        """Update a device's state."""
        if device_id in self._devices:
            for key, value in kwargs.items():
                if hasattr(self._devices[device_id], key):
                    setattr(self._devices[device_id], key, value)
            self._devices[device_id].last_seen = time.time()
            self._save_state()

    def get_device(self, device_id: str) -> DeviceState | None:
        """Get a device's state."""
        return self._devices.get(device_id)

    def list_devices(self) -> list[DeviceState]:
        """List all registered devices."""
        return list(self._devices.values())

    def get_active_devices(self, max_age_s: float = 3600) -> list[DeviceState]:
        """Get devices seen within max_age_s seconds."""
        now = time.time()
        return [
            d for d in self._devices.values()
            if (now - d.last_seen) < max_age_s
        ]

    def get_other_devices(self) -> list[DeviceState]:
        """Get all devices except the current one."""
        return [
            d for d in self._devices.values()
            if d.device_id != self._current_device
        ]

    def set_current_device(self, device_id: str) -> None:
        """Set the current active device."""
        self._current_device = device_id
        if device_id in self._devices:
            self._devices[device_id].last_seen = time.time()
            self._save_state()

    def get_context_summary(self) -> str:
        """Get a summary of all device states for cross-device context."""
        if not self._devices:
            return "No devices registered."

        lines = ["Connected devices:"]
        for device in self._devices.values():
            status = "active" if (time.time() - device.last_seen) < 3600 else "inactive"
            task = f" — {device.current_task}" if device.current_task else ""
            lines.append(f"  {device.device_id} ({device.device_type}): {status}{task}")

        return "\n".join(lines)
