"""kernel/media/tools.py — PJ-01: real media + brightness kernel tools (research/13).

MediaController existed since Phase P4 but was an audit-flagged orphan: its
`register_tools` wired NOOP handlers at READ risk — six dead declarations.
This builder is the ONE registration path for the media family:

- `media_control`  (WRITE — mutates playback/volume, consent asks)
- `brightness_get` (READ  — flows without consent; a "how bright?" question
                     must never raise a popup)
- `brightness_set` (WRITE — set/adjust, consent asks with the value visible)

All handlers return structured ToolResults; hardware/WMI failures degrade to
clean fails (raw exceptions never reach the model).
"""

from __future__ import annotations

from typing import Any

from kernel.media.controller import MediaController
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall, ToolResult

__all__ = ["build_media_tools"]

_MEDIA_VERBS = ("play_pause", "next", "previous", "volume_up", "volume_down", "mute")


def build_media_tools(registry: ToolRegistry,
                      controller: MediaController | None = None) -> None:
    """Register media + brightness tools on `registry`."""
    media = controller or MediaController()

    @registry.tool(
        name="media_control",
        description="Control system media playback: play_pause, next, previous, "
                    "volume_up, volume_down, mute (media/volume keys).",
        parameters={"type": "object", "properties": {
            "verb": {"type": "string", "enum": list(_MEDIA_VERBS)},
        }, "required": ["verb"]},
        risk=RiskClass.WRITE, timeout_s=10.0)
    def media_control(call: ToolCall) -> ToolResult:
        verb = str(call.args.get("verb") or "")
        handlers: dict[str, Any] = {
            "play_pause": media.play_pause, "next": media.next_track,
            "previous": media.previous_track, "volume_up": media.volume_up,
            "volume_down": media.volume_down, "mute": media.mute,
        }
        fn = handlers.get(verb)
        if fn is None:
            return ToolResult.fail(
                call, f"unsupported verb {verb!r} — supported: {', '.join(_MEDIA_VERBS)}",
                risk=RiskClass.WRITE)
        return ToolResult.success(call, data={"detail": fn(), "verb": verb},
                                  risk=RiskClass.WRITE)

    @registry.tool(
        name="brightness_get",
        description="Read the primary display's brightness (0-100). Read-only.",
        parameters={"type": "object", "properties": {}},
        risk=RiskClass.READ, timeout_s=10.0)
    def brightness_get(call: ToolCall) -> ToolResult:
        data = media.brightness()
        return ToolResult.success(call, data=data, risk=RiskClass.READ) \
            if data["ok"] else ToolResult.fail(call, str(data["detail"]),
                                               risk=RiskClass.READ)

    @registry.tool(
        name="brightness_set",
        description="Set display brightness: verb=set with value 0-100, or "
                    "verb=adjust with delta -100..100 (e.g. +10, -10).",
        parameters={"type": "object", "properties": {
            "verb": {"type": "string", "enum": ["set", "adjust"]},
            "value": {"type": "integer", "description": "absolute 0-100 (verb=set)"},
            "delta": {"type": "integer", "description": "relative change (verb=adjust)"},
        }, "required": ["verb"]},
        risk=RiskClass.WRITE, timeout_s=10.0)
    def brightness_set(call: ToolCall) -> ToolResult:
        verb = str(call.args.get("verb") or "")
        if verb == "set":
            raw = call.args.get("value")
            if raw is None:
                return ToolResult.fail(call, "verb=set requires value (0-100)",
                                       risk=RiskClass.WRITE)
            data = media.set_brightness(int(raw))
        elif verb == "adjust":
            raw = call.args.get("delta")
            if raw is None:
                return ToolResult.fail(call, "verb=adjust requires delta (-100..100)",
                                       risk=RiskClass.WRITE)
            data = media.adjust_brightness(int(raw))
        else:
            return ToolResult.fail(call, "verb must be set or adjust",
                                   risk=RiskClass.WRITE)
        return ToolResult.success(call, data=data, risk=RiskClass.WRITE) \
            if data["ok"] else ToolResult.fail(call, str(data["detail"]),
                                               risk=RiskClass.WRITE)
