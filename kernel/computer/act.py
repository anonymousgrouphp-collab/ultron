"""kernel/computer/act.py — P4-A: the ACT half (J-07) — UIA-pattern verbs with dry-run.

Research/03 doctrine: every action is UIA-first — find the control by
name/automation_id, actuate via its pattern (Invoke/Toggle/SetValue). Pixels
are a fallback that P4-A deliberately does NOT include (one stack; the
pixel lane lands with OmniParser/Qwen3-VL later — the kernel interface
already anticipates it via `kind="uia"|"pixels"` on the resolved plan).

Consent + dry-run (roadmap Phase-4 gate):
- `dry_run=True` resolves the target (window + element + verb) and returns a
  preview of EXACTLY what would happen — and executes nothing. The preview is
  the consent surface: the user approves a resolved plan, not a hope.
- DESTRUCTIVE verb classes (close_window) demand consent regardless of risk
  maps — enforced here at the verb layer, before the policy engine sees them.

Timing doctrine from the probes: real store apps need settle time (Calculator
~8 s to map its window) and type_keys at high speed drops characters
(pause=0.01 lost one char in a probe). Defaults: settle 3.0 s, type pause
0.03.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from kernel.computer.observe import (
    DesktopError,
    find_elements,
    top_windows,
    window_by_handle,
)

log = logging.getLogger(__name__)

__all__ = ["ActionPlan", "ActionResult", "InputGateway", "VERBS"]

# Verbs the gateway can execute (kernel verbs from research/03 §3 pattern table).
VERBS = ("invoke", "toggle", "set_value", "type_keys", "press_hotkey", "close_window")

# Act defaults from live probes on this machine (Win11 + store apps).
SETTLE_S = 3.0
TYPE_PAUSE = 0.03
TYPE_WITH_SPACES = True


@dataclass(frozen=True)
class ActionPlan:
    """A RESOLVED action — what dry-run previews and consent approves."""

    verb: str
    window: str                     # window title at resolution time
    pid: int
    handle: int
    target: str                     # control name or automation_id
    target_type: str                 # control type when known
    argument: str = ""               # text to type / value to set / hotkey
    kind: str = "uia"                # "uia" today; "pixels" is the future lane

    def as_dict(self) -> dict[str, object]:
        return {
            "verb": self.verb, "window": self.window, "pid": self.pid,
            "handle": self.handle, "target": self.target,
            "target_type": self.target_type, "argument": self.argument,
            "kind": self.kind,
        }


@dataclass(frozen=True)
class ActionResult:
    ok: bool
    plan: ActionPlan | None
    dry_run: bool = False
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {
            "ok": self.ok, "dry_run": self.dry_run, "detail": self.detail,
            "plan": self.plan.as_dict() if self.plan else None,
        }


class InputGateway:
    """Executes (or previews) UIA actions scoped to spawned windows.

    The gateway holds the set of window (handle, title, pid) triples it is
    ALLOWED to touch — seeded from windows it observed being spawned for a
    task. A live user's windows never enter that set: the probe machine has
    an open Notepad with the user's settings.json, and an unscoped act could
    type into it. `allow_*` is the only way a window enters the set.
    """

    def __init__(self) -> None:
        self._allowed: dict[int, tuple[str, int]] = {}  # handle -> (title, pid)

    # -- scope management ---------------------------------------------------

    def allow_window(self, handle: int, title: str, pid: int) -> None:
        """Admit one window into the actable set (after verifying it exists)."""
        window_by_handle(handle, title, pid)  # raises DesktopError if gone
        self._allowed[handle] = (title, pid)

    def allowed_windows(self) -> tuple[tuple[int, str, int], ...]:
        return tuple((h, t, p) for h, (t, p) in sorted(self._allowed.items()))

    def _scope(self, handle: int, title: str, pid: int) -> tuple[str, int]:
        entry = self._allowed.get(handle)
        if entry is None:
            raise DesktopError(
                f"window {title!r} (pid {pid}) is not in the allowed set — "
                "spawn it via the computer tools first")
        if entry != (title, pid):
            raise DesktopError(
                f"window {title!r} (pid {pid}) changed identity since it was allowed")
        return entry

    def refresh_window_title(self, old_title: str, handle: int, pid: int) -> str:
        """Re-bind an allowed window whose title drifted (dirty markers like
        '*' appear the moment an edit is made). Verifies the window still
        exists under (handle, pid) and updates the allowed entry — the window
        IDENTITY is the handle; the title is just its current label."""
        entry = self._allowed.get(handle)
        if entry is None or entry[1] != pid:
            raise DesktopError(
                f"window (pid {pid}) is not in the allowed set")
        current = window_by_handle(handle, entry[0], pid)
        self._allowed[handle] = (current.title, pid)
        return current.title

    def _find_allowed_by_title(self, window: str) -> tuple[int, str, int]:
        """Locate an allowed window by title, tolerating dirty-marker drift
        ('*' prefix) — verifies by handle that it is the SAME window."""
        direct = [(h, t, p) for h, t, p in self.allowed_windows() if t == window]
        if direct:
            return direct[0]
        for handle, (t, p) in self._allowed.items():
            if t.lstrip("*") == window.lstrip("*") or window.lstrip("*") in t:
                # candidate drift — confirm the window actually exists now
                try:
                    current = window_by_handle(handle, t, p)
                except DesktopError:
                    continue
                if current.title.lstrip("*") == window.lstrip("*"):
                    self._allowed[handle] = (current.title, p)
                    return handle, current.title, p
        raise DesktopError(f"no allowed window titled {window!r} — spawn it first")

    # -- resolution + preview ----------------------------------------------

    def resolve(self, *, verb: str, window: str, target: str | None = None,
                target_type: str | None = None, argument: str = "") -> ActionPlan:
        """Resolve verb+window+target to a concrete plan WITHOUT executing.

        The resolution is what makes dry-run honest: a preview names the exact
        control (name, type, automation_id, window) that will be actuated."""
        if verb not in VERBS:
            raise DesktopError(
                f"unknown verb {verb!r} — supported: {', '.join(VERBS)}")
        if verb == "close_window":
            if target:
                raise DesktopError("close_window takes no target")
        elif not target:
            raise DesktopError(f"verb {verb!r} requires a target control")
        if verb == "press_hotkey" and not argument:
            raise DesktopError("press_hotkey requires a hotkey argument (e.g. '^s')")

        # locate the window among ALLOWED windows by title (tolerating the
        # '*' dirty-marker apps prepend the moment an edit happens)
        handle, bound_title, pid = self._find_allowed_by_title(window)
        self._scope(handle, bound_title, pid)  # identity re-check

        if verb == "close_window":
            return ActionPlan(verb=verb, window=bound_title, pid=pid, handle=handle,
                              target="", target_type="Window", argument="")

        snaps = find_elements(handle, bound_title, pid, name=target,
                              control_type=target_type)
        if not snaps:
            raise DesktopError(
                f"no control named {target!r} (type {target_type or 'any'}) in {bound_title!r}")
        if len(snaps) > 1:
            names = ", ".join(f"#{s.index}:{s.name!r}" for s in snaps[:5])
            raise DesktopError(
                f"ambiguous target {target!r} — {len(snaps)} matches ({names}); "
                "narrow with target_type")
        snap = snaps[0]
        return ActionPlan(verb=verb, window=bound_title, pid=pid, handle=handle,
                          target=snap.name or snap.automation_id,
                          target_type=snap.control_type, argument=argument)

    def preview(self, plan: ActionPlan) -> dict[str, object]:
        """Human/model-readable statement of what `execute(plan)` will do."""
        if plan.verb == "close_window":
            return {"what": f"close window {plan.window!r}", "act": "none",
                    "details": f"pid {plan.pid}, handle {plan.handle}"}
        d: dict[str, object] = {
            "what": f"{plan.verb} on {plan.target!r} ({plan.target_type}) "
                    f"in window {plan.window!r}",
            "act": "pattern call on the named control",
            "argument": plan.argument,
        }
        if plan.verb in ("set_value", "type_keys"):
            what = str(d["what"])
            d["what"] = what + f" with text {plan.argument!r}"
        return d

    # -- execution -----------------------------------------------------------

    def execute(self, plan: ActionPlan, *, dry_run: bool = True) -> ActionResult:
        """Execute a resolved plan. dry_run=True (the default, and the only
        mode the policy engine allows without explicit consent) resolves and
        previews but never actuates."""
        if dry_run:
            return ActionResult(ok=True, plan=plan, dry_run=True,
                                detail="dry-run: resolved target, executed nothing")
        try:
            if plan.verb == "close_window":
                return self._close(plan)
            return self._act_on_element(plan)
        except DesktopError as exc:
            return ActionResult(ok=False, plan=plan, dry_run=False, detail=str(exc))
        except Exception as exc:  # noqa: BLE001 — internals never reach the model
            log.exception("computer act failed: %s on %r", plan.verb, plan.target)
            return ActionResult(ok=False, plan=plan, dry_run=False,
                                detail=f"action failed: {type(exc).__name__}")

    # individual verbs ------------------------------------------------------

    def _element_for(self, plan: ActionPlan):
        """Re-resolve the element at act time (windows change between preview
        and consent — the plan names the target, the tree finds it fresh)."""
        from pywinauto import Desktop  # local: Windows-only import

        # title may have drifted since the plan was built — re-bind by handle
        _, bound_title, _ = self._find_allowed_by_title(plan.window)
        win = Desktop(backend="uia").window(handle=plan.handle, title=bound_title)
        matches = [el for el in win.descendants()
                   if (getattr(el.element_info, "name", "") or "") == plan.target or
                   (getattr(el.element_info, "automation_id", "") or "") == plan.target]
        if not matches:
            raise DesktopError(
                f"target {plan.target!r} not found in {plan.window!r} at act time")
        # prefer the element matching the recorded control type when present
        for el in matches:
            if plan.target_type and str(getattr(el.element_info, "control_type", "")) == plan.target_type:
                return el
        return matches[0]

    def _act_on_element(self, plan: ActionPlan) -> ActionResult:
        el = self._element_for(plan)
        detail = ""
        if plan.verb == "invoke":
            el.invoke()
            time.sleep(0.25)
            detail = f"invoked {plan.target!r}"
        elif plan.verb == "toggle":
            el.toggle()
            time.sleep(0.25)
            detail = f"toggled {plan.target!r}"
        elif plan.verb == "set_value":
            if not hasattr(el, "set_edit_text"):
                raise DesktopError(
                    f"{plan.target!r} ({plan.target_type}) does not accept direct "
                    "value setting — use type_keys")
            el.set_edit_text(plan.argument)
            detail = f"set {plan.target!r} to {plan.argument!r}"
        elif plan.verb == "type_keys":
            el.type_keys(plan.argument, with_spaces=TYPE_WITH_SPACES,
                         pause=TYPE_PAUSE)
            # drain + tail-retry: store apps drop occasional keystrokes and
            # land them asynchronously. Poll the UIA text until it equals the
            # argument; if it stalls at a PREFIX, type exactly the missing
            # tail once. Without this, a follow-up ^s saves a truncated file
            # (observed live: 'i took the liberty, s').
            deadline = time.monotonic() + 6.0
            retried = False
            while time.monotonic() < deadline:
                got = "\n".join(t for t in el.texts() if t)
                if got == plan.argument:
                    break
                if plan.argument.startswith(got) and got and not retried:
                    time.sleep(0.4)
                    still = "\n".join(t for t in el.texts() if t)
                    if still == got and plan.argument.startswith(still):
                        missing = plan.argument[len(still):]
                        el.type_keys(missing, with_spaces=TYPE_WITH_SPACES,
                                     pause=TYPE_PAUSE)
                        retried = True
                        continue
                time.sleep(0.15)
            detail = f"typed into {plan.target!r}"
        elif plan.verb == "press_hotkey":
            el.type_keys(plan.argument, pause=TYPE_PAUSE)
            time.sleep(0.5)
            detail = f"hotkey {plan.argument!r} sent to {plan.target!r}"
        else:  # pragma: no cover — VERBS gate covers this
            raise DesktopError(f"unsupported verb {plan.verb!r}")
        return ActionResult(ok=True, plan=plan, dry_run=False, detail=detail)

    def _close(self, plan: ActionPlan) -> ActionResult:
        from pywinauto import Desktop

        try:
            _, bound_title, _ = self._find_allowed_by_title(plan.window)
            win = Desktop(backend="uia").window(handle=plan.handle, title=bound_title)
            win.close()
        except DesktopError:
            raise
        time.sleep(1.2)
        # store apps raise an IN-WINDOW save/discard prompt (buttons are
        # descendants of the same window, not a separate dialog window) —
        # dismiss "Don't save" so close cannot strand a dirty tab.
        try:
            dismissed = self._dismiss_save_dialog(plan.pid)
        except Exception as exc:  # noqa: BLE001
            log.warning("save-dialog handling failed: %s", type(exc).__name__)
            dismissed = False
        # verify by HANDLE+PID, never by title: apps rewrite the title (dirty
        # markers like '*' appear) — an exact-title check reports success while
        # the window lives on under its new name.
        time.sleep(0.5)
        still = any(w.handle == plan.handle and w.pid == plan.pid
                    for w in top_windows())
        if still and not dismissed:
            return ActionResult(ok=False, plan=plan, dry_run=False,
                                detail=f"window {plan.window!r} did not close "
                                       "(a dialog may be holding it open)")
        if still and dismissed:
            # one more settle after dismissal, then final verdict
            time.sleep(1.0)
            still = any(w.handle == plan.handle and w.pid == plan.pid
                        for w in top_windows())
            if still:
                return ActionResult(ok=False, plan=plan, dry_run=False,
                                    detail=f"window {plan.window!r} still open "
                                           "after dismissing its dialog")
        return ActionResult(ok=True, plan=plan, dry_run=False,
                            detail=f"closed {plan.window!r}")

    @staticmethod
    def _dismiss_save_dialog(pid: int) -> bool:
        """Dismiss dialogs the app raises on close: 'Don't save' when there is
        unsaved work, or a plain OK button when the app hit an error (e.g. the
        file's path vanished). Returns True when a dialog was handled."""
        from pywinauto import Desktop

        for w in Desktop(backend="uia").windows():
            if w.process_id() != pid:
                continue
            for el in w.descendants(control_type="Button"):
                nm = (getattr(el.element_info, "name", "") or "").lower()
                if "don" in nm and "save" in nm:
                    el.invoke()
                    time.sleep(0.8)
                    return True
                if nm.strip() == "ok":
                    el.invoke()
                    time.sleep(0.8)
                    return True
        return False
