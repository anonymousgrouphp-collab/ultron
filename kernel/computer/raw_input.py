"""kernel/computer/raw_input.py — PJ-03: the raw desktop-input tier.

UIA verbs (act.py) reach only accessibility-aware surfaces. For everything
else (games, canvas apps, remote sessions, fullscreen kiosks) the pixel lane
needs RAW mouse input: move the cursor, click at a point, scroll a delta.
This module is the ONE SendInput seam for that lane (no pyautogui in the
kernel — the legacy actions/ layer is separate and deprecated lineage).

Safety contract (enforced by InputGateway.resolve, not here):
- raw verbs resolve ONLY when the gateway was built with raw_enabled=True
  (config `raw_input_enabled`, default off);
- coordinates are window-RELATIVE from the model, converted to absolute
  against the admitted window's rect — a point outside the rect is refused,
  so a raw act can never land on an unscoped surface;
- the admitted window is brought to the foreground and VERIFIED before the
  event fires — a click at a covered point would hit whatever is on top,
  never the window consent approved.

The ctypes boundary is isolated in _set_cursor_pos/_send_input/_get_foreground
/_set_foreground so tests fake the OS without a display.
"""

from __future__ import annotations

import ctypes
import logging
import time

from kernel.computer.observe import DesktopError

log = logging.getLogger(__name__)

__all__ = [
    "execute_plan", "foreground_handle", "window_rect",
]

# MOUSEEVENTF_* flags (winuser.h)
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_LEFTDOWN = 0x0002
_MOUSEEVENTF_LEFTUP = 0x0004
_MOUSEEVENTF_RIGHTDOWN = 0x0008
_MOUSEEVENTF_RIGHTUP = 0x0010
_MOUSEEVENTF_MIDDLEDOWN = 0x0020
_MOUSEEVENTF_MIDDLEUP = 0x0040
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000

_WHEEL_DELTA = 120
_CLICK_FLAG_PAIRS = {
    "left": (_MOUSEEVENTF_LEFTDOWN, _MOUSEEVENTF_LEFTUP),
    "right": (_MOUSEEVENTF_RIGHTDOWN, _MOUSEEVENTF_RIGHTUP),
    "middle": (_MOUSEEVENTF_MIDDLEDOWN, _MOUSEEVENTF_MIDDLEUP),
}
_RECT_FAIL = "could not read the window rectangle (window may have closed)"


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", ctypes.c_long), ("dy", ctypes.c_long),
                ("mouseData", ctypes.c_ulong), ("dwFlags", ctypes.c_ulong),
                ("time", ctypes.c_ulong), ("dwExtraInfo", ctypes.c_size_t)]


class _INPUT(ctypes.Structure):
    class _U(ctypes.Union):
        _fields_ = [("mi", _MOUSEINPUT)]

    _anonymous_ = ("u",)
    _fields_ = [("type", ctypes.c_ulong), ("u", _U)]


# -- ctypes boundary (the only OS-touching lines; tests monkeypatch these) --

def _user32() -> ctypes.WinDLL:  # pragma: no cover — thin OS boundary
    return ctypes.windll.user32


def _set_cursor_pos(x: int, y: int) -> None:  # pragma: no cover
    if not _user32().SetCursorPos(int(x), int(y)):
        raise DesktopError("SetCursorPos failed (secure desktop or no display)")


def _send_input(flags: int, wheel_delta: int = 0) -> None:  # pragma: no cover
    inp = _INPUT(type=0)  # INPUT_MOUSE
    inp.mi = _MOUSEINPUT(dwFlags=flags, mouseData=wheel_delta & 0xFFFFFFFF)
    if _user32().SendInput(1, ctypes.byref(inp), ctypes.sizeof(_INPUT)) != 1:
        raise DesktopError("SendInput failed (input may be blocked)")


def _get_foreground() -> int:  # pragma: no cover
    return int(_user32().GetForegroundWindow())


def _set_foreground(handle: int) -> None:  # pragma: no cover
    if not _user32().SetForegroundWindow(int(handle)):
        raise DesktopError("could not bring the target window to the foreground")


# -- geometry ----------------------------------------------------------------

def window_rect(handle: int) -> tuple[int, int, int, int]:
    """Absolute (left, top, right, bottom) of a top-level window handle."""
    rect = _RECT()
    if not _user32().GetWindowRect(int(handle), ctypes.byref(rect)):
        raise DesktopError(_RECT_FAIL)
    return rect.left, rect.top, rect.right, rect.bottom


def foreground_handle() -> int:
    """Handle of the current foreground window (0 when none)."""
    return _get_foreground()


def ensure_foreground(handle: int, *, settle_s: float = 0.15) -> None:
    """Raise + verify the window is foreground — raw events land on whatever
    is TOP at the point, so the admitted window must be verified on top
    first. Raises DesktopError when the focus cannot be taken."""
    _set_foreground(handle)
    time.sleep(settle_s)
    if foreground_handle() != handle:
        raise DesktopError(
            "target window did not take the foreground — refusing to send "
            "raw input that could land on another window")


# -- event senders -----------------------------------------------------------

def send_move(x: int, y: int) -> str:
    _set_cursor_pos(x, y)
    return f"cursor moved to ({x},{y})"


def send_click(x: int, y: int, *, button: str = "left", clicks: int = 1) -> str:
    if button not in _CLICK_FLAG_PAIRS:
        raise DesktopError(f"unsupported button {button!r} — left/right/middle")
    if clicks not in (1, 2):
        raise DesktopError("clicks must be 1 or 2")
    _set_cursor_pos(x, y)
    down, up = _CLICK_FLAG_PAIRS[button]
    for _ in range(clicks):
        _send_input(down)
        _send_input(up)
        time.sleep(0.05)
    return f"{button} click x{clicks} at ({x},{y})"


def send_scroll(dx_notches: int, dy_notches: int) -> str:
    if dx_notches == 0 and dy_notches == 0:
        raise DesktopError("scroll delta is zero — nothing to do")
    if dy_notches:
        _send_input(_MOUSEEVENTF_WHEEL, wheel_delta=dy_notches * _WHEEL_DELTA)
    if dx_notches:
        _send_input(_MOUSEEVENTF_HWHEEL, wheel_delta=dx_notches * _WHEEL_DELTA)
    return f"scrolled by ({dx_notches},{dy_notches}) notches"


# -- plan dispatch -----------------------------------------------------------

def execute_plan(verb: str, argument: str, handle: int,
                 *, target_type: str = "") -> dict[str, object]:
    """Execute a resolved RAW plan: verify foreground, then send the event.
    `argument` carries ABSOLUTE coordinates ("x,y") for click/move or
    "dx,dy" notches for scroll; `target_type` carries the click button."""
    ensure_foreground(handle)
    if verb in ("raw_click", "raw_move"):
        ax, ay = (int(p) for p in argument.split(","))
        if verb == "raw_move":
            detail = send_move(ax, ay)
        else:
            detail = send_click(ax, ay, button=target_type or "left")
    elif verb == "raw_scroll":
        dx, dy = (int(p) for p in argument.split(","))
        detail = send_scroll(dx, dy)
    else:  # pragma: no cover — VERBS gate covers this
        raise DesktopError(f"unsupported raw verb {verb!r}")
    log.info("raw input: %s %s on handle %s", verb, argument, handle)
    return {"verb": verb, "argument": argument, "detail": detail}
