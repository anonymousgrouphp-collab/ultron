"""Regression tests for the legacy paths that remain during the kernel migration."""

from __future__ import annotations

from pathlib import Path


def test_windows_app_launcher_never_constructs_a_shell_command(monkeypatch):
    from actions import open_app

    seen = {}

    monkeypatch.setattr(open_app.shutil, "which", lambda _: "C:/tools/safe.exe")
    monkeypatch.setattr(open_app.time, "sleep", lambda _: None)

    def popen(args, **kwargs):
        seen["args"] = args
        seen["shell"] = kwargs.get("shell")

    monkeypatch.setattr(open_app.subprocess, "Popen", popen)

    assert open_app._launch_windows("safe") is True
    assert seen == {"args": ["C:/tools/safe.exe"], "shell": False}


def test_legacy_file_processor_refuses_code_execution(tmp_path: Path):
    from actions.file_processor import _process_code

    script = tmp_path / "untrusted.py"
    script.write_text("raise RuntimeError('must not execute')", encoding="utf-8")

    result = _process_code(script, "run", {}, None)

    assert "disabled" in result.lower()
    assert "sandboxed" in result.lower()


def test_legacy_coding_agent_is_disabled():
    from actions.dev_agent import dev_agent

    result = dev_agent({"description": "write a program"})

    assert "disabled" in result.lower()
    assert "sandboxed" in result.lower()
