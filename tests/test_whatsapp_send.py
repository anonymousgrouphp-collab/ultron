"""tests/test_whatsapp_send.py — PJ-04 (research/13): WhatsApp Desktop send.

Hermetic: the pywinauto desktop window and the foreground handle are faked;
a recorder captures every type_keys burst. Pins the safety contract:

- the flow aborts clean BEFORE the next keystroke burst whenever the
  foreground is not the verified WhatsApp window (a focus steal must never
  send someone else's chat);
- the window is admitted into the gateway's allowed set (scoped acting);
- missing app / empty args are clean DesktopError fails;
- the tool is WRITE-risk and only registered when the caller opts in.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from kernel.computer import build_whatsapp_tool
from kernel.computer.observe import DesktopError, WindowInfo
from kernel.computer import whatsapp as wa
from kernel.policy import Policy, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall

WIN = WindowInfo(handle=42, title="WhatsApp", pid=7,
                 process_name="WhatsApp.exe", is_visible=True)


def _mkcall(name: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=dict(args), source="test")


class _FakeGateway:
    def __init__(self) -> None:
        self.allowed: tuple[int, str, int] | None = None

    def allow_window(self, handle: int, title: str, pid: int) -> None:
        self.allowed = (handle, title, pid)

    def _scope(self, handle: int, title: str, pid: int) -> tuple[str, int]:
        return (title, pid)


class _FakeApp:
    def __init__(self, foreground_ok: bool) -> None:
        self.typed: list[str] = []
        self.focused = False
        self._foreground_ok = foreground_ok

    def set_focus(self) -> None:
        self.focused = True

    def type_keys(self, keys: str, **kwargs: Any) -> None:
        self.typed.append(keys)


def _patch_flow(monkeypatch, app: _FakeApp, fg_handle: int,
                drift_after: int | None = None):
    """Fake the desktop window + foreground + enumeration (the real
    top_windows() is a UIA COM round-trip — deadlocked CI once, never in
    tests). When drift_after is set, the Nth foreground check onward
    reports a FOREIGN handle."""
    monkeypatch.setattr(wa, "top_windows", lambda: [WIN])
    monkeypatch.setattr(wa, "_desktop_window", lambda handle, title: app)
    state = {"n": 0}

    def _fg() -> int:
        state["n"] += 1
        if drift_after is not None and state["n"] > drift_after:
            return 999
        return fg_handle

    monkeypatch.setattr(wa, "foreground_handle", _fg)


class TestFindWindow:
    def test_missing_app_raises(self) -> None:
        with pytest.raises(DesktopError, match="not running"):
            wa.find_whatsapp_window([])

    def test_process_match_preferred_over_title(self) -> None:
        by_title = WindowInfo(handle=1, title="WhatsApp Web — Chrome", pid=1,
                              process_name="chrome.exe", is_visible=True)
        found = wa.find_whatsapp_window([by_title, WIN])
        assert found.handle == 42

    def test_title_fallback(self) -> None:
        title_only = WindowInfo(handle=2, title="WhatsApp", pid=2,
                                process_name="unknown.exe", is_visible=True)
        assert wa.find_whatsapp_window([title_only]).handle == 2

    def test_invisible_window_ignored(self) -> None:
        hidden = WindowInfo(handle=3, title="WhatsApp", pid=3,
                            process_name="WhatsApp.exe", is_visible=False)
        with pytest.raises(DesktopError, match="not running"):
            wa.find_whatsapp_window([hidden])


class TestFlow:
    def test_happy_path_sequence(self, monkeypatch) -> None:
        app = _FakeApp(foreground_ok=True)
        _patch_flow(monkeypatch, app, fg_handle=WIN.handle)
        gw = _FakeGateway()
        data = wa.send_message(gw, "Tony Stark", "Suit's ready, sir")
        assert data["sent"] is True
        assert gw.allowed == (42, "WhatsApp", 7)      # window was admitted
        assert app.focused
        assert app.typed == ["^f", "Tony Stark", "{ENTER}",
                             "Suit's ready, sir", "{ENTER}"]

    def test_focus_drift_aborts_before_sending(self, monkeypatch) -> None:
        app = _FakeApp(foreground_ok=True)
        # checks: 1 search-typing, 2 contact, 3 select, 4 message, 5 send —
        # drift after the 3rd check aborts before the MESSAGE is typed
        _patch_flow(monkeypatch, app, fg_handle=WIN.handle, drift_after=3)
        with pytest.raises(DesktopError, match="focus moved"):
            wa.send_message(_FakeGateway(), "Tony", "hi")
        assert app.typed == ["^f", "Tony", "{ENTER}"]  # nothing further typed
        assert "hi" not in app.typed                   # message never typed

    def test_empty_args_fail_clean(self, monkeypatch) -> None:
        _patch_flow(monkeypatch, _FakeApp(True), fg_handle=WIN.handle)
        with pytest.raises(DesktopError, match="required"):
            wa.send_message(_FakeGateway(), "  ", "hi")

    def test_set_focus_failure_is_clean(self, monkeypatch) -> None:
        app = _FakeApp(foreground_ok=True)

        def _boom() -> None:
            raise RuntimeError("com dead")

        monkeypatch.setattr(app, "set_focus", _boom)
        _patch_flow(monkeypatch, app, fg_handle=WIN.handle)
        with pytest.raises(DesktopError, match="could not focus"):
            wa.send_message(_FakeGateway(), "Tony", "hi")


class TestTool:
    def test_registration_write_risk(self) -> None:
        reg = ToolRegistry()
        build_whatsapp_tool(reg, _FakeGateway())
        assert reg.risks()["whatsapp_send"] is RiskClass.WRITE

    def test_tool_clean_fail_when_app_missing(self, monkeypatch) -> None:
        monkeypatch.setattr(wa, "top_windows", lambda: [])
        reg = ToolRegistry()
        build_whatsapp_tool(reg, _FakeGateway())
        res = asyncio.run(reg.execute(
            _mkcall("whatsapp_send", contact="Tony", message="hi")))
        assert not res.ok
        assert "not running" in (res.error or "")

    def test_policy_gates_whatsapp_send_behind_consent(self, monkeypatch) -> None:
        monkeypatch.setattr(wa, "top_windows", lambda: [WIN])
        monkeypatch.setattr(wa, "_desktop_window",
                            lambda handle, title: _FakeApp(True))
        monkeypatch.setattr(wa, "foreground_handle", lambda: WIN.handle)
        reg = ToolRegistry()
        build_whatsapp_tool(reg, _FakeGateway())
        engine = PolicyEngine(policy=Policy.default())
        result = asyncio.run(engine.run(
            _mkcall("whatsapp_send", contact="Tony", message="hi"), reg))
        assert not result.ok  # WRITE with no consent callback -> fail-safe deny
        assert "consent" in (result.error or "")
