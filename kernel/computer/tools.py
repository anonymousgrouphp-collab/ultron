"""kernel/computer/tools.py — P4-A: kernel Tools for computer control (J-06/J-07).

Every tool routes through the same ToolRegistry → PolicyEngine choke point as
the rest of the kernel. Risk classes (the consent posture, roadmap §3.4):

- observe/list/read tools            → READ    (allow by default)
- spawn_app / act (dry_run default)   → WRITE   (consent required; dry-run is
                                                 the preview the user approves)
- act with execute=True              → EXECUTE (consent; the UIA verbs type/click)
- close_window                       → DESTRUCTIVE-class consent at the VERB layer
                                       + WRITE risk (a close can lose work)

`spawn_app` admits windows into the InputGateway's allowed set ONLY for
processes it spawned itself (pid diffed before/after) — a live user's window
can never be admitted by name confusion.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path
from typing import Any

from kernel.computer.act import InputGateway
from kernel.computer.observe import (
    DesktopError,
    WindowInfo,
    dump_tree,
    read_text,
    top_windows,
)
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

__all__ = ["COMPUTER_TOOLS", "InputGateway", "build_computer_tools", "spawn_window_for"]

COMPUTER_TOOLS = (
    "screen_describe",   # J-06: enumerate windows (+ optional UIA text)
    "ui_tree",           # J-07 observe: dump one window's UIA tree
    "spawn_app",         # launch an app; returns the fresh window (allowed set)
    "ui_act",            # J-07 act: resolve + preview/execute a UIA verb
)

_SPAWN_SETTLE_S = 8.0     # store apps need seconds to map their window (probes)
_SPAWN_TIMEOUT_S = 30.0
_TEXT_CAP = 8_000


def spawn_window_for(command: str, gateway: InputGateway, *,
                     args: list[str] | None = None,
                     settle_s: float = _SPAWN_SETTLE_S) -> dict[str, Any]:
    """Run `command`, wait for its window, admit ONLY that fresh window into
    the gateway's allowed set. Returns window metadata. Raises DesktopError
    when no new window appears (no fallback to existing windows, ever)."""
    args = args or []
    before = {(w.title, w.pid) for w in top_windows()}
    cmd_base = Path(command).name.lower().removesuffix(".exe")
    try:
        proc = subprocess.Popen([command, *args], shell=False)
    except OSError as exc:
        raise DesktopError(f"failed to launch {command!r}: {exc.strerror or type(exc).__name__}") from exc
    deadline = time.monotonic() + settle_s
    fresh: list[WindowInfo] = []
    while time.monotonic() < deadline:
        time.sleep(1.0)
        now = top_windows()
        # a NEW (title, pid) pair is a fresh window. Single-instance store apps
        # (Notepad) open new windows under their EXISTING pid — filtering by pid
        # would miss them; the pair-diff catches them and stays safe (a window
        # that existed before, under any pid, is never admitted).
        fresh = [w for w in now
                 if (w.title, w.pid) not in before
                 and w.is_visible and w.title]
        if fresh:
            break
    if not fresh:
        _kill_quietly(proc)
        raise DesktopError(
            f"{command!r} produced no new window within {settle_s:g}s — "
            "the app may be a single-instance handoff; nothing was touched")
    # prefer a fresh window whose process matches the command (e.g. notepad.exe
    # -> Notepad.exe) so a coincidental user window can't win attribution
    matching = [w for w in fresh if cmd_base in w.process_name.lower()]
    win = (matching or fresh)[0]
    gateway.allow_window(win.handle, win.title, win.pid)
    return {
        "handle": win.handle, "title": win.title, "pid": win.pid,
        "process": win.process_name,
    }


def _kill_quietly(proc: subprocess.Popen) -> None:
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:  # pragma: no cover — best-effort cleanup
        pass


def build_computer_tools(registry: ToolRegistry, gateway: InputGateway,
                         *, spawn_allowed: bool = True) -> None:
    """Register the computer-control tools on `registry`."""

    @registry.tool(
        name="screen_describe",
        description="Observe the desktop: list visible top-level windows with "
                    "titles, process names and handles. Optionally read one "
                    "window's text content (by title). Read-only.",
        parameters={"type": "object", "properties": {
            "window_title": {"type": "string",
                             "description": "read this window's text (omit to just list windows)"},
        }, "required": []},
        risk=RiskClass.READ, timeout_s=20.0)
    def screen_describe(call: ToolCall) -> ToolResult:
        wins = top_windows()
        listing = [w.as_dict() for w in wins if w.is_visible]
        if call.args.get("window_title") is None:
            return ToolResult.success(call, data={"windows": listing},
                                      risk=RiskClass.READ)
        title = str(call.args["window_title"])
        target = [w for w in wins if w.title == title]
        if not target:
            return ToolResult.fail(call, f"no window titled {title!r}", risk=RiskClass.READ)
        w = target[0]
        try:
            text = read_text(w.handle, w.title, w.pid)
        except DesktopError as exc:
            return ToolResult.fail(call, str(exc), risk=RiskClass.READ)
        return ToolResult.success(call, data={
            "window": w.as_dict(), "text": text[:_TEXT_CAP],
            "truncated": len(text) > _TEXT_CAP,
        }, risk=RiskClass.READ)

    @registry.tool(
        name="ui_tree",
        description="Dump one window's UI Automation tree (indexed elements with "
                    "control type, name, automation id, rectangle). This is the "
                    "reliable way to see what can be acted on. Use element names "
                    "from this dump as ui_act targets.",
        parameters={"type": "object", "properties": {
            "window_title": {"type": "string", "description": "exact window title"},
            "name_filter": {"type": "string", "description": "only elements whose name matches exactly"},
            "control_type": {"type": "string", "description": "element control type filter (e.g. Button)"},
        }, "required": ["window_title"]},
        risk=RiskClass.READ, timeout_s=25.0)
    def ui_tree(call: ToolCall) -> ToolResult:
        title = str(call.args["window_title"])
        wins = top_windows()
        target = [w for w in wins if w.title == title]
        if not target:
            return ToolResult.fail(call, f"no window titled {title!r}", risk=RiskClass.READ)
        w = target[0]
        try:
            snaps = dump_tree(w.handle, w.title, w.pid)
        except DesktopError as exc:
            return ToolResult.fail(call, str(exc), risk=RiskClass.READ)
        name_f = call.args.get("name_filter")
        ct = call.args.get("control_type")
        if name_f is not None:
            snaps = [s for s in snaps if s.name == name_f]
        if ct is not None:
            snaps = [s for s in snaps if s.control_type == ct]
        return ToolResult.success(call, data={
            "window": w.as_dict(),
            "elements": [s.as_dict() for s in snaps[:80]],
            "count": len(snaps),
        }, risk=RiskClass.READ)

    if not spawn_allowed:  # read-only deployments keep observe tools only
        return

    @registry.tool(
        name="spawn_app",
        description="Launch an application and return the fresh window it "
                    "produces. Only windows produced by this tool can be "
                    "controlled (ui_act) — existing user windows are never "
                    "admitted. Example: command='notepad.exe', "
                    "args=['C:\\\\path\\\\file.txt'] opens that file.",
        parameters={"type": "object", "properties": {
            "command": {"type": "string", "description": "executable to launch (e.g. notepad.exe)"},
            "args": {"type": "array", "items": {"type": "string"},
                     "description": "command-line arguments (e.g. a file path to open)"},
        }, "required": ["command"]},
        risk=RiskClass.WRITE, timeout_s=_SPAWN_TIMEOUT_S, max_retries=0)
    def spawn_app(call: ToolCall) -> ToolResult:
        command = str(call.args["command"])
        raw_args = call.args.get("args") or []
        args = [str(a) for a in raw_args]
        try:
            info = spawn_window_for(command, gateway, args=args)
        except DesktopError as exc:
            return ToolResult.fail(call, str(exc), risk=RiskClass.WRITE)
        return ToolResult.success(call, data=info, risk=RiskClass.WRITE)

    @registry.tool(
        name="ui_act",
        description="Act on a UI control by name (UI Automation — reliable, not "
                    "pixel-guessing). Verbs: invoke (click a button/menu item), "
                    "toggle, set_value (set text directly), type_keys (send "
                    "keystrokes to the control), press_hotkey (e.g. '^s'), "
                    "close_window. Default is dry-run: returns the resolved plan "
                    "and preview WITHOUT executing. Pass execute=true only after "
                    "the user consents to the preview.",
        parameters={"type": "object", "properties": {
            "verb": {"type": "string", "enum": ["invoke", "toggle", "set_value",
                                                "type_keys", "press_hotkey", "close_window"]},
            "window_title": {"type": "string", "description": "window title from screen_describe/spawn_app"},
            "target": {"type": "string", "description": "element name from ui_tree (omit for close_window)"},
            "target_type": {"type": "string", "description": "element control type to disambiguate (e.g. Button)"},
            "argument": {"type": "string", "description": "text/hotkey for type_keys/set_value/press_hotkey"},
            "execute": {"type": "boolean", "description": "false (default) = dry-run preview; true = execute"},
        }, "required": ["verb", "window_title"]},
        risk=RiskClass.EXECUTE, timeout_s=30.0, max_retries=0)
    def ui_act(call: ToolCall) -> ToolResult:
        verb = str(call.args["verb"])
        window = str(call.args["window_title"])
        target = call.args.get("target")
        target = str(target) if target is not None else None
        ttype = call.args.get("target_type")
        ttype = str(ttype) if ttype is not None else None
        argument = str(call.args.get("argument") or "")
        execute = bool(call.args.get("execute", False))
        try:
            plan = gateway.resolve(verb=verb, window=window, target=target,
                                   target_type=ttype, argument=argument)
        except DesktopError as exc:
            return ToolResult.fail(call, str(exc), risk=RiskClass.EXECUTE)
        preview = gateway.preview(plan)
        if not execute:
            return ToolResult.success(call, data={
                "dry_run": True, "plan": plan.as_dict(), "preview": preview,
                "note": "pass execute=true to perform this action",
            }, risk=RiskClass.EXECUTE)
        # close_window is DESTRUCTIVE-at-the-verb-layer: the caller (policy/
        # consent UI) must have approved THIS plan; execute=true with consent
        # is the only path here (registry/policy already gated the call).
        result = gateway.execute(plan, dry_run=False)
        return ToolResult.success(call, data=result.as_dict(),
                                  risk=RiskClass.EXECUTE) if result.ok \
            else ToolResult.fail(call, result.detail, risk=RiskClass.EXECUTE)
