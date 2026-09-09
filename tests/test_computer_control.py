"""tests/test_computer_control.py — P4-A computer control (J-06/J-07).

Two layers:
- Hermetic unit tests over a FAKE desktop: verb validation, scope enforcement
  (the allowed-set), ambiguous-target refusal, dry-run honesty, DESTRUCTIVE
  close handling. These run everywhere and pin the safety contract.
- Real-desktop integration tests (win32 probe gate): spawn Calculator /
  Notepad-on-temp-file, drive them via UIA, verify with INDEPENDENT checks
  (on-disk file content, UIA display text), clean up. These prove the Phase-4
  gate's act→verify loop on the actual machine. Skipped when pywinauto or the
  desktop is unavailable (CI runners have no interactive desktop).

Safety pins (the "zero destructive actions without consent" half of the gate):
- a window NOT in the allowed set can never be resolved or actuated;
- dry-run resolves and previews but executes nothing;
- ambiguous targets fail loudly instead of clicking something;
- the policy engine gates ui_act as EXECUTE (consent required) and
  spawn_app as WRITE.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import time
from pathlib import Path

import pytest

from kernel.computer import (
    COMPUTER_TOOLS,
    DesktopError,
    InputGateway,
    UIA_AVAILABLE,
    build_computer_tools,
)
from kernel.policy import Policy, PolicyEngine
from kernel.tools import ToolRegistry
from kernel.types import RiskClass, ToolCall

# ---------------------------------------------------------------- hermetic


def _mkcall(name: str, **args: object) -> ToolCall:
    return ToolCall(id=f"c-{name}", name=name, args=dict(args), source="test")


def _registry_with_tools() -> tuple[ToolRegistry, InputGateway]:
    reg = ToolRegistry()
    gw = InputGateway()
    build_computer_tools(reg, gw)
    return reg, gw


class TestVerbsAndResolution:
    def test_unknown_verb_rejected(self) -> None:
        gw = InputGateway()
        with pytest.raises(DesktopError, match="unknown verb"):
            gw.resolve(verb="smash", window="X", target="Y")

    def test_verbs_needing_target_reject_blank(self) -> None:
        gw = InputGateway()
        with pytest.raises(DesktopError, match="requires a target"):
            gw.resolve(verb="invoke", window="X", target=None)

    def test_hotkey_requires_argument(self) -> None:
        gw = InputGateway()
        with pytest.raises(DesktopError, match="hotkey"):
            gw.resolve(verb="press_hotkey", window="X", target="t", argument="")

    def test_close_window_rejects_target(self) -> None:
        gw = InputGateway()
        with pytest.raises(DesktopError, match="no target"):
            gw.resolve(verb="close_window", window="X", target="Y")

    def test_unallowed_window_cannot_be_resolved(self) -> None:
        gw = InputGateway()
        # even a window that exists on the real desktop is refused by title
        # if it was never admitted — resolve must fail BEFORE any tree read
        with pytest.raises(DesktopError, match="no allowed window"):
            gw.resolve(verb="close_window", window="Program Manager")

    def test_allow_then_identity_change_refused(self, monkeypatch) -> None:
        """An allowed window whose (title,pid) no longer matches the set entry
        is refused — stale handles can't be replayed onto moved windows."""
        gw = InputGateway()
        gw._allowed[42] = ("Old Title", 999)  # seed the set directly
        with pytest.raises(DesktopError, match="changed identity"):
            gw._scope(42, "New Title", 999)


class TestDryRun:
    """Dry-run must be honest: resolved plan + preview, zero actuation."""

    def test_execute_dry_run_default_previews_not_acts(self, monkeypatch) -> None:
        gw = InputGateway()
        # admit a fake window without touching the real desktop
        monkeypatch.setattr(gw, "_scope", lambda *a, **k: ("X", 1))
        # fake find_elements to return one exact match
        import kernel.computer.act as act_mod

        snap = type("S", (), {"index": 0, "name": "Save", "automation_id": "b1",
                              "control_type": "Button"})()
        monkeypatch.setattr(act_mod, "find_elements",
                            lambda *a, **k: [snap])
        monkeypatch.setattr(gw, "allowed_windows",
                            lambda: ((77, "X", 1),))
        plan = gw.resolve(verb="invoke", window="X", target="Save")
        assert plan.target == "Save" and plan.target_type == "Button"
        result = gw.execute(plan, dry_run=True)
        assert result.ok and result.dry_run
        assert "executed nothing" in result.detail
        preview = gw.preview(plan)
        assert "invoke" in preview["what"] and "Save" in preview["what"]

    def test_preview_mentions_argument_for_typing(self, monkeypatch) -> None:
        gw = InputGateway()
        monkeypatch.setattr(gw, "allowed_windows", lambda: ((77, "X", 1),))
        monkeypatch.setattr(gw, "_scope", lambda *a, **k: ("X", 1))
        snap = type("S", (), {"index": 0, "name": "Editor", "automation_id": "",
                              "control_type": "Document"})()
        import kernel.computer.act as act_mod

        monkeypatch.setattr(act_mod, "find_elements", lambda *a, **k: [snap])
        plan = gw.resolve(verb="type_keys", window="X", target="Editor",
                          argument="hello sir")
        pv = gw.preview(plan)
        assert "hello sir" in pv["what"]


class TestAmbiguity:
    def test_ambiguous_target_fails_loudly(self, monkeypatch) -> None:
        gw = InputGateway()
        monkeypatch.setattr(gw, "allowed_windows", lambda: ((77, "X", 1),))
        monkeypatch.setattr(gw, "_scope", lambda *a, **k: ("X", 1))
        s1 = type("S", (), {"index": 0, "name": "OK", "automation_id": "",
                            "control_type": "Button"})()
        s2 = type("S", (), {"index": 1, "name": "OK", "automation_id": "",
                            "control_type": "Button"})()
        import kernel.computer.act as act_mod

        monkeypatch.setattr(act_mod, "find_elements",
                            lambda *a, **k: [s1, s2])
        with pytest.raises(DesktopError, match="ambiguous"):
            gw.resolve(verb="invoke", window="X", target="OK")


class TestRegistryIntegration:
    def test_tools_registered_with_risk_classes(self) -> None:
        reg, _ = _registry_with_tools()
        for name in COMPUTER_TOOLS:
            assert name in reg, name
        risks = reg.risks()
        assert risks["screen_describe"] is RiskClass.READ
        assert risks["ui_tree"] is RiskClass.READ
        assert risks["spawn_app"] is RiskClass.WRITE
        assert risks["ui_act"] is RiskClass.EXECUTE

    def test_ui_act_defaults_to_dry_run(self, monkeypatch) -> None:
        reg, gw = _registry_with_tools()
        monkeypatch.setattr(gw, "allowed_windows", lambda: ((77, "X", 1),))
        monkeypatch.setattr(gw, "_scope", lambda *a, **k: ("X", 1))
        snap = type("S", (), {"index": 0, "name": "Seven", "automation_id": "",
                              "control_type": "Button"})()
        import kernel.computer.act as act_mod

        monkeypatch.setattr(act_mod, "find_elements", lambda *a, **k: [snap])
        executed: list[object] = []
        monkeypatch.setattr(gw, "execute",
                            lambda plan, dry_run=True: executed.append(plan) or
                            type("R", (), {"ok": True, "plan": plan, "dry_run": dry_run,
                                           "detail": "x", "as_dict": lambda self: {}})())
        call = _mkcall("ui_act", verb="invoke", window_title="X", target="Seven")
        res = asyncio.run(reg.execute(call))
        assert res.ok
        assert res.data["dry_run"] is True
        assert executed == []  # dry-run path never reached execute()

    def test_policy_gates_ui_act_behind_consent(self) -> None:
        reg, _ = _registry_with_tools()
        engine = PolicyEngine(policy=Policy.default())
        call = _mkcall("ui_act", verb="invoke", window_title="X", target="Y")
        result = asyncio.run(engine.run(call, reg))
        assert not result.ok  # EXECUTE with no consent callback -> fail-safe deny
        assert "consent" in (result.error or "")

    def test_read_tools_flow_without_consent(self) -> None:
        reg, _ = _registry_with_tools()
        engine = PolicyEngine(policy=Policy.default())
        call = _mkcall("screen_describe")
        result = asyncio.run(engine.run(call, reg))
        assert result.ok
        assert isinstance(result.data, dict) and "windows" in result.data

    def test_spawn_app_missing_command_fails_clean(self) -> None:
        reg, _ = _registry_with_tools()
        call = _mkcall("spawn_app", command="definitely-not-a-real-exe-xyz.exe")
        res = asyncio.run(reg.execute(call))
        assert not res.ok
        assert "failed to launch" in (res.error or "")


# ---------------------------------------------------------- real desktop

# Opt-in: real-UIA tests spawn real windows — run locally with
# ULTRON_DESKTOP_TESTS=1 (the deployment target). CI stays hermetic (the
# Phase-2/3 precedent); the measured live evidence lives in the phase-4 gate
# run (.ultron/eval/phase4/gate_results.json) recorded on the board.
pytestmark_real = pytest.mark.skipif(
    not UIA_AVAILABLE or os.environ.get("ULTRON_DESKTOP_TESTS") != "1",
    reason="opt-in: set ULTRON_DESKTOP_TESTS=1 on an interactive desktop")


def _desktop_windows():
    from kernel.computer.observe import top_windows

    return top_windows()


@pytestmark_real
class TestRealDesktop:
    """Real UIA round-trips on spawned apps — the Phase-4 gate's act→verify
    primitive, proven on the deployment target. Each test spawns its OWN
    window (never touches the user's) and cleans up."""

    def test_spawn_and_list_calculator(self) -> None:
        reg, gw = _registry_with_tools()
        call = _mkcall("spawn_app", command="calc.exe")
        res = asyncio.run(reg.execute(call))
        assert res.ok, res.error
        info = res.data
        assert info["title"] == "Calculator"
        # the fresh window is in the allowed set
        handles = [h for h, _, _ in gw.allowed_windows()]
        assert info["handle"] in handles
        # verify by observing: tree contains Buttons
        tree = _mkcall("ui_tree", window_title="Calculator",
                       control_type="Button")
        tres = asyncio.run(reg.execute(tree))
        assert tres.ok
        assert tres.data["count"] > 5
        # cleanup: close via the gateway
        plan = gw.resolve(verb="close_window", window="Calculator")
        result = gw.execute(plan, dry_run=False)
        assert result.ok, result.detail

    def test_calculator_math_via_invoke_and_verify(self) -> None:
        """Act→verify: 8 × 7 via invoked buttons, then READ the display through
        UIA — the independent check (no pixels, no trust in the act itself)."""
        reg, gw = _registry_with_tools()
        spawn = asyncio.run(reg.execute(_mkcall("spawn_app", command="calc.exe")))
        assert spawn.ok, spawn.error
        try:
            for name in ("Clear", "Eight", "Multiply by", "Seven", "Equals"):
                plan = gw.resolve(verb="invoke", window="Calculator", target=name,
                                  target_type="Button")
                r = gw.execute(plan, dry_run=False)
                assert r.ok, f"{name}: {r.detail}"
            # verify: find the display text element
            from kernel.computer.observe import find_elements
            handle = next(h for h, t, _ in gw.allowed_windows() if t == "Calculator")
            _, (title, pid) = handle, gw._allowed[handle]
            snaps = find_elements(handle, title, pid, control_type="Text")
            display = [s for s in snaps if s.automation_id == "CalculatorResults"]
            assert display, "Calculator display not found via UIA"
            assert "56" in display[0].name  # 8*7 — verified, not assumed
        finally:
            plan = gw.resolve(verb="close_window", window="Calculator")
            gw.execute(plan, dry_run=False)

    def test_notepad_type_save_and_independent_disk_verify(self) -> None:
        """The flagship round-trip: spawn Notepad on a temp file, type via
        type_keys, save with ^s hotkey, close, then verify the ON-DISK file
        content — an independent check that can't be fooled by the UI.
        Filenames are uniquified per run: store Notepad is single-instance,
        so a lingering window with the same title would collide."""
        reg, gw = _registry_with_tools()
        with tempfile.TemporaryDirectory(prefix="ultron_p4_test_") as tmp:
            fname = f"note_{int(time.time() * 1000) % 100000}.txt"
            target = Path(tmp) / fname
            target.write_text("", encoding="utf-8")
            spawn = asyncio.run(reg.execute(_mkcall(
                "spawn_app", command="notepad.exe", args=[str(target)])))
            assert spawn.ok, spawn.error
            title = spawn.data["title"]
            try:
                # find the Document element via ui_tree
                tree = asyncio.run(reg.execute(_mkcall(
                    "ui_tree", window_title=title, control_type="Document")))
                assert tree.ok and tree.data["count"] >= 1
                doc = tree.data["elements"][0]
                # act: type (execute=false first = dry-run)
                dry = asyncio.run(reg.execute(_mkcall(
                    "ui_act", verb="type_keys", window_title=title,
                    target=doc["name"] or "Text Editor", target_type="Document",
                    argument="the butler did it")))
                assert dry.ok and dry.data["dry_run"] is True
                # act: execute (consent granted by the test harness)
                act = asyncio.run(reg.execute(_mkcall(
                    "ui_act", verb="type_keys", window_title=title,
                    target=doc["name"] or "Text Editor", target_type="Document",
                    argument="the butler did it", execute=True)))
                assert act.ok, act.error or act.data
                # save
                save = asyncio.run(reg.execute(_mkcall(
                    "ui_act", verb="press_hotkey", window_title=title,
                    target=doc["name"] or "Text Editor", target_type="Document",
                    argument="^s", execute=True)))
                assert save.ok, save.data
                time.sleep(1.0)
            finally:
                plan = gw.resolve(verb="close_window", window=title)
                gw.execute(plan, dry_run=False)
            # INDEPENDENT verifier: the bytes on disk
            on_disk = target.read_text(encoding="utf-8")
            assert on_disk == "the butler did it", repr(on_disk)
