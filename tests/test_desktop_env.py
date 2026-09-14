"""tests/test_desktop_env.py — PJ-01 + PJ-02 (research/13): media, brightness,
and OS power-verb kernel tools.

Hermetic: screen-brightness-control is faked via sys.modules, the power
subprocess seam (kernel.computer.power._run) is monkeypatched to record argv,
MediaController methods are stubbed where their real bodies would touch OS
APIs. Pins the safety contract: media/brightness_set/system_power are
WRITE (consent-gated), brightness_get is READ (no popup for a read), power
verbs keep the abortable grace window, failures are clean ToolResults.
"""

from __future__ import annotations

import asyncio
import sys
import types

import pytest

from kernel.computer.power import PowerError, run_power_verb
from kernel.media import MediaController, build_media_tools
from kernel.computer.tools import build_power_tools
from kernel.policy import Policy, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall


def _mkcall(name: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=dict(args), source="test")


def _registry_with_media() -> ToolRegistry:
    reg = ToolRegistry()
    build_media_tools(reg, MediaController())
    return reg


def _registry_with_power() -> ToolRegistry:
    reg = ToolRegistry()
    build_power_tools(reg)
    return reg


def _fake_sbc(get_brightness=None, set_brightness=None) -> types.ModuleType:
    mod = types.ModuleType("screen_brightness_control")
    if get_brightness is not None:
        mod.get_brightness = get_brightness
    if set_brightness is not None:
        mod.set_brightness = set_brightness
    return mod


class TestMediaTools:
    def test_registered_with_risk_classes(self) -> None:
        reg = _registry_with_media()
        assert "media_control" in reg
        assert "brightness_get" in reg
        assert "brightness_set" in reg
        risks = reg.risks()
        assert risks["media_control"] is RiskClass.WRITE
        assert risks["brightness_get"] is RiskClass.READ
        assert risks["brightness_set"] is RiskClass.WRITE

    def test_media_control_verb_dispatch(self, monkeypatch) -> None:
        calls: list[str] = []
        # stub the OS-touching keybd_event path by replacing the handler's
        # controller methods on the registered closure's controller — the
        # builder created one MediaController; reach it via a fresh build
        reg2 = ToolRegistry()
        media = MediaController()
        for m in ("play_pause", "next_track", "previous_track", "volume_up",
                  "volume_down", "mute"):
            monkeypatch.setattr(media, m, lambda m=m: calls.append(m) or f"did {m}")
        build_media_tools(reg2, media)
        res = asyncio.run(reg2.execute(_mkcall("media_control", verb="next")))
        assert res.ok and res.data["verb"] == "next"
        assert calls == ["next_track"]

    def test_media_control_unsupported_verb_fails_clean(self) -> None:
        reg = _registry_with_media()
        res = asyncio.run(reg.execute(_mkcall("media_control", verb="eject")))
        assert not res.ok
        assert "unsupported verb" in (res.error or "")

    def test_policy_gates_media_control_behind_consent(self) -> None:
        reg = _registry_with_media()
        engine = PolicyEngine(policy=Policy.default())
        result = asyncio.run(engine.run(_mkcall("media_control", verb="mute"), reg))
        assert not result.ok  # WRITE with no consent callback -> fail-safe deny
        assert "consent" in (result.error or "")

    def test_brightness_get_flows_without_consent(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "screen_brightness_control",
                            _fake_sbc(get_brightness=lambda *a, **k: [72]))
        reg = _registry_with_media()
        engine = PolicyEngine(policy=Policy.default())
        result = asyncio.run(engine.run(_mkcall("brightness_get"), reg))
        assert result.ok
        assert result.data["value"] == 72


class TestBrightness:
    def test_get(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "screen_brightness_control",
                            _fake_sbc(get_brightness=lambda *a, **k: [60, 55]))
        data = MediaController().brightness()
        assert data["ok"] and data["value"] == 60

    def test_get_no_display_fails_clean(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "screen_brightness_control",
                            _fake_sbc(get_brightness=lambda *a, **k: []))
        data = MediaController().brightness()
        assert not data["ok"]

    def test_get_without_package_fails_clean(self, monkeypatch) -> None:
        # None in sys.modules makes `import` raise ImportError — the exact
        # "package not installed" path, no display or WMI involved.
        monkeypatch.setitem(sys.modules, "screen_brightness_control", None)
        data = MediaController().brightness()
        assert not data["ok"] and "not installed" in str(data["detail"])

    def test_set_clamps(self, monkeypatch) -> None:
        seen: list[int] = []
        monkeypatch.setitem(sys.modules, "screen_brightness_control", _fake_sbc(
            set_brightness=lambda v, *a, **k: seen.append(v)))
        data = MediaController().set_brightness(150)
        assert data["ok"] and data["value"] == 100 and seen == [100]

    def test_set_negative_clamps_to_zero(self, monkeypatch) -> None:
        seen: list[int] = []
        monkeypatch.setitem(sys.modules, "screen_brightness_control", _fake_sbc(
            set_brightness=lambda v, *a, **k: seen.append(v)))
        data = MediaController().set_brightness(-5)
        assert data["ok"] and seen == [0]

    def test_adjust_reads_then_sets(self, monkeypatch) -> None:
        seen: list[int] = []
        monkeypatch.setitem(sys.modules, "screen_brightness_control", _fake_sbc(
            get_brightness=lambda *a, **k: [70],
            set_brightness=lambda v, *a, **k: seen.append(v)))
        data = MediaController().adjust_brightness(-10)
        assert data["ok"] and data["value"] == 60 and seen == [60]

    def test_adjust_without_readable_current_fails_clean(self, monkeypatch) -> None:
        monkeypatch.setitem(sys.modules, "screen_brightness_control", _fake_sbc(
            get_brightness=lambda *a, **k: (_ for _ in ()).throw(OSError("wmi"))))
        data = MediaController().adjust_brightness(10)
        assert not data["ok"]


class TestPowerVerbs:
    def test_registered_write(self) -> None:
        reg = _registry_with_power()
        assert reg.risks()["system_power"] is RiskClass.WRITE

    def test_lock_argv(self, monkeypatch) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr("kernel.computer.power._run", lambda argv: seen.append(argv))
        data = run_power_verb("lock_workstation")
        assert data["verb"] == "lock_workstation"
        assert seen == [["rundll32.exe", "user32.dll,LockWorkStation"]]

    def test_shutdown_keeps_grace(self, monkeypatch) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr("kernel.computer.power._run", lambda argv: seen.append(argv))
        data = run_power_verb("shutdown")
        assert seen == [["shutdown.exe", "/s", "/t", "5"]]
        assert "abortable" in str(data["detail"])

    def test_delay_clamped(self, monkeypatch) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr("kernel.computer.power._run", lambda argv: seen.append(argv))
        run_power_verb("restart", delay_s=1)
        run_power_verb("restart", delay_s=99_999)
        assert seen[0][3] == "5"      # below floor -> 5
        assert seen[1][3] == "600"    # above ceiling -> 600

    def test_sign_out_and_abort_argv(self, monkeypatch) -> None:
        seen: list[list[str]] = []
        monkeypatch.setattr("kernel.computer.power._run", lambda argv: seen.append(argv))
        run_power_verb("sign_out")
        run_power_verb("abort")
        assert seen == [["shutdown.exe", "/l"], ["shutdown.exe", "/a"]]

    def test_unknown_verb_raises_power_error(self) -> None:
        with pytest.raises(PowerError, match="unsupported verb"):
            run_power_verb("format_c")

    def test_command_failure_is_power_error(self, monkeypatch) -> None:
        def _boom(argv):
            raise PowerError("exit code 1190")
        monkeypatch.setattr("kernel.computer.power._run", _boom)
        with pytest.raises(PowerError, match="1190"):
            run_power_verb("abort")

    def test_tool_wraps_power_error_clean(self, monkeypatch) -> None:
        def _boom(argv):
            raise PowerError("no pending shutdown")
        monkeypatch.setattr("kernel.computer.power._run", _boom)
        reg = _registry_with_power()
        res = asyncio.run(reg.execute(_mkcall("system_power", verb="abort")))
        assert not res.ok
        assert res.error == "no pending shutdown"  # clean, no traceback text

    def test_policy_gates_power_behind_consent(self) -> None:
        reg = _registry_with_power()
        engine = PolicyEngine(policy=Policy.default())
        result = asyncio.run(engine.run(_mkcall("system_power", verb="shutdown"), reg))
        assert not result.ok
        assert "consent" in (result.error or "")
