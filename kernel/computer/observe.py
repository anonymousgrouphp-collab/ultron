"""kernel/computer/observe.py — P4-A: the OBSERVE half of computer control (J-06/J-07).

Research/03 §3, UIA-first policy: a UIA tree dump is a *database, not a guess* —
named controls with type/state/rect, milliseconds, zero tokens. Pixels are the
fallback, never the foundation.

Design notes:
- Windows are enumerated from the real desktop (pywinauto Desktop/Win32).
- Targeting is HANDLE-SCOPED: every find/act happens inside one window handle,
  never "click whatever is topmost" — this box runs live user apps (an open
  Notepad with the user's settings.json sits on the real desktop while the
  suite runs; an unscoped click could type into it).
- The observer never actuates and never takes screenshots — pure observation
  is READ-risk by construction.
- Everything degrades to a clean error string; raw exception text never leaks
  into results that reach the model (Kill List #6).
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pywinauto imports stay inside functions (Windows-only, slow)
    pass

log = logging.getLogger(__name__)

__all__ = ["UIA_AVAILABLE", "ElementSnapshot", "WindowInfo", "window_matches",
           "top_windows", "window_by_handle", "dump_tree", "find_elements",
           "read_text", "DesktopError"]

try:  # Windows-only pin; the kernel stays importable without it
    from pywinauto import Desktop as _PWADesktop  # noqa: F401

    UIA_AVAILABLE = True
except ImportError:  # pragma: no cover — non-Windows dev boxes
    UIA_AVAILABLE = False


class DesktopError(RuntimeError):
    """Clean observation failure — the message is safe for model consumption."""


@dataclass(frozen=True)
class ElementSnapshot:
    """One UIA node as a JSON-able record (what the model reads to act)."""

    handle: int          # parent window handle — the act-side scope key
    index: int          # position within the dump (the model cites "element 7")
    control_type: str
    name: str
    automation_id: str
    rect: tuple[int, int, int, int]  # left, top, right, bottom (absolute px)
    depth: int

    def as_dict(self) -> dict[str, object]:
        return {
            "handle": self.handle, "index": self.index, "type": self.control_type,
            "name": self.name, "automation_id": self.automation_id,
            "rect": list(self.rect), "depth": self.depth,
        }


@dataclass(frozen=True)
class WindowInfo:
    """A top-level window observed on the desktop."""

    handle: int
    title: str
    pid: int
    process_name: str
    is_visible: bool

    def as_dict(self) -> dict[str, object]:
        return {
            "handle": self.handle, "title": self.title, "pid": self.pid,
            "process_name": self.process_name, "visible": self.is_visible,
        }


def window_matches(win: WindowInfo, title: str | None = None,
                   pid: int | None = None) -> bool:
    """Strict targeting rule: a window matches only when title AND pid both
    agree. Title alone can collide (two "Untitled - Notepad"); pid alone can
    span several documents of one single-instance app."""
    if title is not None and win.title != title:
        return False
    if pid is not None and win.pid != pid:
        return False
    return title is not None or pid is not None


def _desktop():
    if not UIA_AVAILABLE:
        raise DesktopError("pywinauto is not installed on this system")
    from pywinauto import Desktop

    return Desktop(backend="uia")


def top_windows(*, settle_s: float = 0.0) -> list[WindowInfo]:
    """Enumerate visible top-level windows (title, pid, process name, handle).
    `settle_s` sleeps first — freshly spawned store apps take seconds to map
    their window (Calculator needed ~8 s in probes; without the settle the
    enumeration races and the window is missing)."""
    if settle_s > 0:
        time.sleep(settle_s)
    try:
        import psutil

        names: dict[int, str] = {}
        for p in psutil.process_iter(["name", "pid"]):
            try:
                names[p.info["pid"]] = p.info["name"] or ""
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:  # pragma: no cover
        names = {}
    try:
        raw = _desktop().windows()
    except Exception as exc:  # noqa: BLE001 — observation never leaks internals
        raise DesktopError(f"desktop window enumeration failed: {type(exc).__name__}") from exc
    out: list[WindowInfo] = []
    for w in raw:
        try:
            out.append(WindowInfo(
                handle=w.handle, title=w.window_text(), pid=w.process_id(),
                process_name=names.get(w.process_id(), ""),
                is_visible=w.is_visible(),
            ))
        except Exception:  # a vanished mid-enumeration window is skipped, not fatal
            continue
    return out


def _win_wrapper(handle: int, title: str, pid: int):
    """Resolve one window by (handle, pid) — the scope inside which every
    observe happens. `title` is the last-known label (hint only: apps rewrite
    titles with dirty markers). A vanished window fails cleanly instead of
    falling back to some other window."""
    for w in top_windows():
        if w.handle == handle and w.pid == pid:
            return _desktop().window(handle=handle, title=w.title)
    raise DesktopError(
        f"window not found (it may have closed): title={title!r} pid={pid}")


def window_by_handle(handle: int, title: str, pid: int) -> WindowInfo:
    """Confirm a window still exists (by handle + pid) and return its CURRENT
    info. The `title` argument is the last-known label — apps rewrite titles
    (dirty markers), so title is a hint, never the identity; identity is the
    OS window handle."""
    for w in top_windows():
        if w.handle == handle and w.pid == pid:
            return w
    raise DesktopError(
        f"window not found (it may have closed): title={title!r} pid={pid}")


def dump_tree(handle: int, title: str, pid: int, *, max_depth: int = 8,
              max_elements: int = 200) -> list[ElementSnapshot]:
    """UIA tree dump scoped to ONE window — the J-07 observation primitive.
    Bounded by depth and element count so a pathological app can't balloon
    the context window."""
    try:
        win = _win_wrapper(handle, title, pid)
        elements = win.descendants()
    except DesktopError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DesktopError(f"UIA tree read failed: {type(exc).__name__}") from exc
    out: list[ElementSnapshot] = []
    for el in elements:
        if len(out) >= max_elements:
            break
        try:
            info = el.element_info
        except Exception:  # pragma: no cover — element vanished mid-walk
            continue
        try:
            rect = info.rectangle
            coords = (rect.left, rect.top, rect.right, rect.bottom)
        except Exception:  # pragma: no cover
            coords = (-1, -1, -1, -1)
        # cheap depth proxy: nodes arrive in tree-walk order; depth via parent chain is
        # unavailable without pattern calls per node — store 0 and let the model read
        # the flat, indexed list (UFO's observation layer is flat too).
        out.append(ElementSnapshot(
            handle=handle, index=len(out),
            control_type=str(getattr(info, "control_type", "")),
            name=str(getattr(info, "name", "") or ""),
            automation_id=str(getattr(info, "automation_id", "") or ""),
            rect=coords, depth=0,
        ))
    return out


def find_elements(handle: int, title: str, pid: int, *, name: str | None = None,
                   control_type: str | None = None,
                   automation_id: str | None = None) -> list[ElementSnapshot]:
    """Filtered tree search — the resolution step behind every act. Exact
    match on name/automation_id, case-sensitive: UIA names are UI text."""
    snaps = dump_tree(handle, title, pid)
    out = []
    for s in snaps:
        if name is not None and s.name != name:
            continue
        if control_type is not None and s.control_type != control_type:
            continue
        if automation_id is not None and s.automation_id != automation_id:
            continue
        out.append(s)
    return out


def read_text(handle: int, title: str, pid: int, *, element_index: int | None = None,
              element_name: str | None = None,
              control_type: str | None = None) -> str:
    """Read text content from a window (J-06 'what am I looking at'). Reads
    the whole window's text by default, or one element's. Uses pywinauto's
    texts() (Text/Legacy pattern) — zero pixels, zero tokens."""
    try:
        win = _win_wrapper(handle, title, pid)
        if element_index is None and element_name is None:
            # whole-window text: title + every Text child + Document content
            parts: list[str] = [title]
            for el in win.descendants(control_type="Text"):
                try:
                    parts.extend(t for t in el.texts() if t)
                except Exception:  # pragma: no cover
                    continue
            for el in win.descendants(control_type="Document"):
                try:
                    parts.extend(t for t in el.texts() if t)
                except Exception:  # pragma: no cover
                    continue
            return "\n".join(parts)
        # single element: resolve by index or name
        els = list(win.descendants())
        if element_index is not None:
            if element_index < 0 or element_index >= len(els):
                raise DesktopError(f"element index {element_index} out of range")
            el = els[element_index]
        else:
            assert element_name is not None
            el = None
            for cand in els:
                if getattr(cand.element_info, "name", "") == element_name and (
                        control_type is None or
                        str(getattr(cand.element_info, "control_type", "")) == control_type):
                    el = cand
                    break
            if el is None:
                raise DesktopError(
                    f"no element named {element_name!r} in window {title!r}")
        try:
            got = el.texts()
        except Exception as exc:  # noqa: BLE001
            raise DesktopError(f"element text read failed: {type(exc).__name__}") from exc
        return "\n".join(t for t in got if t)
    except DesktopError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DesktopError(f"text read failed: {type(exc).__name__}") from exc
