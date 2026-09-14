"""kernel/computer/whatsapp.py — PJ-04: WhatsApp Desktop send (research/13).

The surajmaru repo proved the flow (activate WhatsApp → ^f → type contact →
Enter → type message → Enter) but typed BLIND — pyautogui into whatever had
focus, so a focus steal between steps sent someone else's chat. This port
keeps the flow and replaces the blindness with ULTRON's discipline:

- the WhatsApp window is found by process/title and admitted into the
  InputGateway allowed set (the tool is WRITE-risk → consent asks; config
  `whatsapp_send_enabled` gates registration, default OFF);
- every step drives pywinauto against THAT handle (the same window stack as
  ui_act — no second input seam);
- before EVERY keystroke burst the foreground handle is re-verified — if
  focus moved (a popup, the user, a crash), the flow aborts clean BEFORE
  anything else is typed, and never sends a half-composed message.

Residual honesty: this drives a desktop chat UI — it is inherently more
fragile than an API. It is flag-gated OFF by default and exists for the
user's own machine, where the alternative is web.whatsapp.com via the CU
web agent.
"""

from __future__ import annotations

import logging
import time

from kernel.computer.observe import DesktopError, WindowInfo, top_windows
from kernel.computer.raw_input import foreground_handle

log = logging.getLogger(__name__)

__all__ = ["find_whatsapp_window", "send_message"]

_TYPE_PAUSE = 0.04


def _is_whatsapp(win: WindowInfo) -> bool:
    name = (win.process_name or "").lower()
    title = (win.title or "").lower()
    return name.startswith("whatsapp") or "whatsapp" in title


def find_whatsapp_window(wins: list[WindowInfo]) -> WindowInfo:
    """Pick the visible WhatsApp window (process match wins over title)."""
    visible = [w for w in wins if w.is_visible and _is_whatsapp(w)]
    if not visible:
        raise DesktopError("WhatsApp Desktop is not running (no visible window)")
    process_match = [w for w in visible
                     if (w.process_name or "").lower().startswith("whatsapp")]
    return (process_match or visible)[0]


def _desktop_window(handle: int, title: str):  # pragma: no cover — OS boundary
    from pywinauto import Desktop

    return Desktop(backend="uia").window(handle=handle, title=title)


def send_message(gateway, contact: str, message: str, *,
                 wins: list[WindowInfo] | None = None) -> dict[str, object]:
    """Send `message` to `contact` via WhatsApp Desktop. Aborts clean the
    moment the foreground is not the verified window."""
    contact = (contact or "").strip()
    message = (message or "").strip()
    if not contact or not message:
        raise DesktopError("both contact and message are required")

    win = find_whatsapp_window(wins if wins is not None else top_windows())
    gateway.allow_window(win.handle, win.title, win.pid)
    # identity re-check through the gateway's own scope discipline
    gateway._scope(win.handle, win.title, win.pid)

    app = _desktop_window(win.handle, win.title)
    try:
        app.set_focus()
    except Exception as exc:  # noqa: BLE001 — focus failure is a clean abort
        raise DesktopError(
            f"could not focus the WhatsApp window: {type(exc).__name__}") from exc

    def _typed(keys: str, *, label: str) -> None:
        if foreground_handle() != win.handle:
            raise DesktopError(
                f"focus moved before {label} — aborted before anything else "
                "was typed (nothing was sent)")
        app.type_keys(keys, with_spaces=True, pause=_TYPE_PAUSE)

    _typed("^f", label="search")
    time.sleep(0.4)
    _typed(contact, label="contact name")
    time.sleep(0.6)
    _typed("{ENTER}", label="contact select")
    time.sleep(0.8)
    _typed(message, label="message")
    _typed("{ENTER}", label="send")
    time.sleep(0.5)
    log.info("whatsapp: message sent to %r via %r", contact, win.title)
    return {"sent": True, "contact": contact, "window": win.title,
            "pid": win.pid}
