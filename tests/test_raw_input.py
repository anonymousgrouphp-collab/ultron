"""tests/test_raw_input.py — PJ-03 (research/13): the raw desktop-input tier.

Hermetic: no display, no SendInput. The OS boundary lives in four
kernel.computer.raw_input functions (_set_cursor_pos / _send_input /
_get_foreground / _set_foreground) plus window_rect — all monkeypatched.
Pins the safety contract:

- raw verbs resolve ONLY on a gateway built with raw_enabled=True;
- coordinates are window-relative, converted to absolute against the
  admitted window's rect, and REFUSED outside it;
- the foreground is verified before any event fires (a covered point would
  hit whatever is on top, never the approved window);
- raw_act is EXECUTE-risk (consent) and dry-run by default;
- raw_act is registered only when the caller passes raw_allowed=True.
"""

from __future__ import annotations

import asyncio

import pytest

from kernel.computer import InputGateway, build_computer_tools
from kernel.computer import raw_input as raw_mod
from kernel.computer.observe import DesktopError
from kernel.policy import Policy, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall

RECT = (100, 200, 900, 700)  # left, top, right, bottom


def _mkcall(name: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=dict(args), source="test")


def _gw(*, raw: bool = True) -> InputGateway:
    return InputGateway(raw_enabled=raw)


@pytest.fixture()
def scoped(monkeypatch):
    """A gateway whose allowed-window lookup is faked to window 'App'
    (handle 77) and whose rect is the fixed RECT above."""
    gw = _gw()
    monkeypatch.setattr(gw, "allowed_windows", lambda: ((77, "App", 1),))
    monkeypatch.setattr(gw, "_scope", lambda *a, **k: ("App", 1))
    monkeypatch.setattr(raw_mod, "window_rect", lambda handle: RECT)
    return gw


class TestResolveGating:
    def test_raw_disabled_by_default(self) -> None:
        gw = InputGateway()
        assert gw.raw_enabled is False
        with pytest.raises(DesktopError, match="raw input tier is disabled"):
            gw.resolve(verb="raw_click", window="App", argument="1,1")

    def test_raw_enabled_property(self, scoped) -> None:
        assert scoped.raw_enabled is True


class TestResolve:
    def test_click_converts_relative_to_absolute(self, scoped) -> None:
        plan = scoped.resolve(verb="raw_click", window="App", argument="50,60")
        assert plan.kind == "pixels"
        assert plan.argument == "150,260"      # 100+50, 200+60
        assert plan.target_type == "left"

    def test_click_button_via_target_type(self, scoped) -> None:
        plan = scoped.resolve(verb="raw_click", window="App", argument="0,0",
                              target_type="right")
        assert plan.target_type == "right"

    def test_click_outside_rect_refused(self, scoped) -> None:
        with pytest.raises(DesktopError, match="outside the window rect"):
            scoped.resolve(verb="raw_click", window="App", argument="5000,60")

    def test_click_bad_button_refused(self, scoped) -> None:
        with pytest.raises(DesktopError, match="unsupported button"):
            scoped.resolve(verb="raw_click", window="App", argument="1,1",
                           target_type="smash")

    def test_move_clamped_edge_inside(self, scoped) -> None:
        plan = scoped.resolve(verb="raw_move", window="App", argument="0,0")
        assert plan.argument == "100,200"

    def test_move_outside_refused(self, scoped) -> None:
        with pytest.raises(DesktopError, match="outside"):
            scoped.resolve(verb="raw_move", window="App", argument="-10,10")

    def test_scroll_keeps_delta_no_rect_check(self, scoped) -> None:
        plan = scoped.resolve(verb="raw_scroll", window="App", argument="3,-2")
        assert plan.argument == "3,-2"

    def test_zero_scroll_refused(self, scoped) -> None:
        with pytest.raises(DesktopError, match="zero"):
            scoped.resolve(verb="raw_scroll", window="App", argument="0,0")

    def test_garbage_argument_refused(self, scoped) -> None:
        with pytest.raises(DesktopError, match="integers"):
            scoped.resolve(verb="raw_click", window="App", argument="banana")

    def test_unallowed_window_refused(self, monkeypatch) -> None:
        gw = _gw()
        monkeypatch.setattr(raw_mod, "window_rect", lambda handle: RECT)
        with pytest.raises(DesktopError, match="no allowed window"):
            gw.resolve(verb="raw_click", window="Ghost", argument="1,1")


class TestPreviewAndDryRun:
    def test_preview_names_point_and_button(self, scoped) -> None:
        plan = scoped.resolve(verb="raw_click", window="App", argument="5,5",
                              target_type="right")
        preview = scoped.preview(plan)
        assert "right-click" in str(preview["what"])
        assert "SendInput" in str(preview["act"])

    def test_tool_defaults_to_dry_run(self, scoped, monkeypatch) -> None:
        fired: list[str] = []
        monkeypatch.setattr(raw_mod, "ensure_foreground", lambda h, **k: fired.append(h))
        reg = ToolRegistry()
        build_computer_tools(reg, scoped, spawn_allowed=True, raw_allowed=True)
        res = asyncio.run(reg.execute(
            _mkcall("raw_act", verb="raw_click", window_title="App", x=5, y=5)))
        assert res.ok and res.data["dry_run"] is True
        assert fired == []  # dry-run never touches the OS


class TestExecute:
    def test_click_sends_real_events(self, scoped, monkeypatch) -> None:
        order: list[tuple[str, object]] = []
        monkeypatch.setattr(raw_mod, "ensure_foreground",
                            lambda h, **k: order.append(("fg", h)))
        monkeypatch.setattr(raw_mod, "_set_cursor_pos",
                            lambda x, y: order.append(("move", (x, y))))
        monkeypatch.setattr(raw_mod, "_send_input",
                            lambda flags, wheel_delta=0: order.append(("input", flags)))
        result = scoped.execute(
            scoped.resolve(verb="raw_click", window="App", argument="7,8"),
            dry_run=False)
        assert result.ok
        assert order[0] == ("fg", 77)               # foreground verified FIRST
        assert order[1] == ("move", (107, 208))     # absolute coords
        assert [kind for kind, _ in order[2:]] == ["input"] * 2  # down+up

    def test_foreground_drift_refuses(self, scoped, monkeypatch) -> None:
        monkeypatch.setattr(raw_mod, "_set_foreground", lambda h: None)
        monkeypatch.setattr(raw_mod, "_get_foreground", lambda: 999)  # other window
        plan = scoped.resolve(verb="raw_move", window="App", argument="1,1")
        result = scoped.execute(plan, dry_run=False)
        assert not result.ok
        assert "foreground" in result.detail

    def test_scroll_sends_wheel(self, scoped, monkeypatch) -> None:
        sent: list[tuple[int, int]] = []
        monkeypatch.setattr(raw_mod, "ensure_foreground", lambda h, **k: None)
        monkeypatch.setattr(raw_mod, "_send_input",
                            lambda flags, wheel_delta=0: sent.append((flags, wheel_delta)))
        result = scoped.execute(
            scoped.resolve(verb="raw_scroll", window="App", argument="0,2"),
            dry_run=False)
        assert result.ok
        assert sent and sent[0][1] == 2 * 120  # WHEEL_DELTA per notch


class TestRegistrationAndPolicy:
    def test_raw_act_absent_without_flag(self) -> None:
        reg = ToolRegistry()
        build_computer_tools(reg, InputGateway(raw_enabled=True), raw_allowed=False)
        assert "raw_act" not in reg

    def test_raw_act_present_with_flag(self) -> None:
        reg = ToolRegistry()
        build_computer_tools(reg, InputGateway(raw_enabled=True), raw_allowed=True)
        assert "raw_act" in reg
        assert reg.risks()["raw_act"] is RiskClass.EXECUTE

    def test_policy_gates_raw_act_behind_consent(self) -> None:
        reg = ToolRegistry()
        build_computer_tools(reg, InputGateway(raw_enabled=True), raw_allowed=True)
        engine = PolicyEngine(policy=Policy.default())
        result = asyncio.run(engine.run(
            _mkcall("raw_act", verb="raw_click", window_title="App", x=1, y=1), reg))
        assert not result.ok  # EXECUTE with no consent callback -> fail-safe deny
        assert "consent" in (result.error or "")
