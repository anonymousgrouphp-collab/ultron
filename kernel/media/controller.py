"""kernel/media/controller.py — Phase P4: media playback control.

Controls media playback on the local system:
- Play/pause/next/previous
- Volume control
- Current track info

Uses platform-specific commands (Windows: nircmd or media keys via ctypes).
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
from typing import Any

__all__ = ["MediaController", "MediaState"]


@dataclass
class MediaState:
    """Current media playback state."""

    is_playing: bool = False
    title: str = ""
    artist: str = ""
    volume: int = 50  # 0-100


class MediaController:
    """Controls media playback on Windows.

    Uses ctypes to send media key events (VK_MEDIA_PLAY_PAUSE, etc.)
    or nircmd if available.
    """

    def __init__(self) -> None:
        self._state = MediaState()

    def play_pause(self) -> str:
        """Toggle play/pause."""
        try:
            # VK_MEDIA_PLAY_PAUSE = 0xB3
            ctypes.windll.user32.keybd_event(0xB3, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xB3, 0, 2, 0)
            self._state.is_playing = not self._state.is_playing
            action = "Playing" if self._state.is_playing else "Paused"
            return f"{action}, sir."
        except Exception:
            return "Media control unavailable, sir."

    def next_track(self) -> str:
        """Skip to next track."""
        try:
            # VK_MEDIA_NEXT_TRACK = 0xB0
            ctypes.windll.user32.keybd_event(0xB0, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xB0, 0, 2, 0)
            return "Next track, sir."
        except Exception:
            return "Media control unavailable, sir."

    def previous_track(self) -> str:
        """Go to previous track."""
        try:
            # VK_MEDIA_PREV_TRACK = 0xB1
            ctypes.windll.user32.keybd_event(0xB1, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xB1, 0, 2, 0)
            return "Previous track, sir."
        except Exception:
            return "Media control unavailable, sir."

    def volume_up(self) -> str:
        """Increase volume."""
        try:
            # VK_VOLUME_UP = 0xAF
            ctypes.windll.user32.keybd_event(0xAF, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xAF, 0, 2, 0)
            self._state.volume = min(100, self._state.volume + 10)
            return f"Volume up to {self._state.volume}%, sir."
        except Exception:
            return "Volume control unavailable, sir."

    def volume_down(self) -> str:
        """Decrease volume."""
        try:
            # VK_VOLUME_DOWN = 0xAE
            ctypes.windll.user32.keybd_event(0xAE, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xAE, 0, 2, 0)
            self._state.volume = max(0, self._state.volume - 10)
            return f"Volume down to {self._state.volume}%, sir."
        except Exception:
            return "Volume control unavailable, sir."

    def mute(self) -> str:
        """Toggle mute."""
        try:
            # VK_VOLUME_MUTE = 0xAD
            ctypes.windll.user32.keybd_event(0xAD, 0, 0, 0)
            ctypes.windll.user32.keybd_event(0xAD, 0, 2, 0)
            return "Mute toggled, sir."
        except Exception:
            return "Volume control unavailable, sir."

    def get_status(self) -> MediaState:
        """Get current media state."""
        return self._state

    # -- brightness (PJ-01, research/13) -------------------------------------
    # Display brightness via screen-brightness-control (WMI/DDC-CI under the
    # hood). Structured results — {ok, detail, value} — because these are
    # tool-facing; the legacy string methods above stay for compatibility.

    def brightness(self) -> dict[str, object]:
        """Read the primary display's brightness (0-100)."""
        try:
            import screen_brightness_control as sbc
        except ImportError:
            return {"ok": False, "value": None,
                    "detail": "screen-brightness-control is not installed"}
        try:
            values = sbc.get_brightness()
        except Exception as exc:  # noqa: BLE001 — WMI/DDL failures → clean result
            return {"ok": False, "value": None,
                    "detail": f"brightness read failed: {type(exc).__name__}"}
        if not values:
            return {"ok": False, "value": None, "detail": "no brightness-capable display found"}
        return {"ok": True, "value": int(values[0]),
                "detail": f"brightness is {int(values[0])}%"}

    def set_brightness(self, value: int) -> dict[str, object]:
        """Set brightness on all displays; value clamped to 0-100."""
        try:
            import screen_brightness_control as sbc
        except ImportError:
            return {"ok": False, "value": None,
                    "detail": "screen-brightness-control is not installed"}
        clamped = max(0, min(100, int(value)))
        try:
            sbc.set_brightness(clamped)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "value": clamped,
                    "detail": f"brightness set failed: {type(exc).__name__}"}
        return {"ok": True, "value": clamped,
                "detail": f"brightness set to {clamped}%"}

    def adjust_brightness(self, delta: int) -> dict[str, object]:
        """Relative brightness change (clamped); fails clean when the current
        value cannot be read."""
        current = self.brightness()
        value = current["value"]
        if not current["ok"] or not isinstance(value, int):
            return {"ok": False, "value": None, "detail": str(current["detail"])}
        return self.set_brightness(value + int(delta))

    def register_tools(self, registry: Any) -> None:
        """Register the media/brightness tools — delegates to the ONE builder
        in kernel/media/tools.py (the old _noop_handler registration is gone;
        the audit's 'media tools absent from declarations' finding closed)."""
        from kernel.media.tools import build_media_tools

        build_media_tools(registry, self)
