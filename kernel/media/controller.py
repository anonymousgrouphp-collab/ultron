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

    def register_tools(self, registry: Any) -> None:
        """Register media tools with a ToolRegistry."""
        from kernel.tools import Tool, RiskClass

        def play_pause_handler() -> str:
            return self.play_pause()

        def next_handler() -> str:
            return self.next_track()

        def prev_handler() -> str:
            return self.previous_track()

        def vol_up_handler() -> str:
            return self.volume_up()

        def vol_down_handler() -> str:
            return self.volume_down()

        def mute_handler() -> str:
            return self.mute()

        registry.register(Tool(
            name="media_play_pause",
            description="Toggle media play/pause",
            parameters={"type": "object", "properties": {}},
            handler=play_pause_handler,
            risk=RiskClass.READ,
        ))
        registry.register(Tool(
            name="media_next",
            description="Skip to next media track",
            parameters={"type": "object", "properties": {}},
            handler=next_handler,
            risk=RiskClass.READ,
        ))
        registry.register(Tool(
            name="media_previous",
            description="Go to previous media track",
            parameters={"type": "object", "properties": {}},
            handler=prev_handler,
            risk=RiskClass.READ,
        ))
        registry.register(Tool(
            name="media_volume_up",
            description="Increase media volume",
            parameters={"type": "object", "properties": {}},
            handler=vol_up_handler,
            risk=RiskClass.READ,
        ))
        registry.register(Tool(
            name="media_volume_down",
            description="Decrease media volume",
            parameters={"type": "object", "properties": {}},
            handler=vol_down_handler,
            risk=RiskClass.READ,
        ))
        registry.register(Tool(
            name="media_mute",
            description="Toggle media mute",
            parameters={"type": "object", "properties": {}},
            handler=mute_handler,
            risk=RiskClass.READ,
        ))
