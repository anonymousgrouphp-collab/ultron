"""Regression tests for the supported Windows bootstrap contract."""

from __future__ import annotations

from pathlib import Path

import pytest

import ULTRON_SETUP as setup


def test_required_project_files_match_the_current_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "SCRIPT_DIR", tmp_path)
    for relative_path in setup.REQUIRED_PROJECT_FILES:
        path = tmp_path / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("present", encoding="utf-8")

    (tmp_path / "config").mkdir(exist_ok=True)
    (tmp_path / "config" / "api_keys.json.example").write_text("{}", encoding="utf-8")

    assert setup.missing_project_files() == []
    assert "core/tts.py" not in setup.REQUIRED_PROJECT_FILES


def test_missing_project_files_reports_each_missing_path(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "SCRIPT_DIR", tmp_path)

    missing = setup.missing_project_files()

    assert "main.py" in missing
    assert "core/tts.py" not in missing


def test_python_314_is_the_only_supported_bootstrap_runtime():
    # User order 2026-09-11: the runtime shifted from 3.13 to 3.14.
    setup.validate_python_version((3, 14))

    with pytest.raises(RuntimeError, match="Python 3.14"):
        setup.validate_python_version((3, 13))


def test_setup_config_copies_example_without_overwriting_user_file(tmp_path, monkeypatch):
    monkeypatch.setattr(setup, "SCRIPT_DIR", tmp_path)
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    example = config_dir / "api_keys.json.example"
    target = config_dir / "api_keys.json"
    example.write_text('{"gemini_api_key": "YOUR_GEMINI_API_KEY_HERE"}', encoding="utf-8")

    assert setup.ensure_config_file() is True
    assert target.read_text(encoding="utf-8") == example.read_text(encoding="utf-8")

    target.write_text('{"gemini_api_key": "user-value"}', encoding="utf-8")
    assert setup.ensure_config_file() is False
    assert "user-value" in target.read_text(encoding="utf-8")


def test_setup_has_no_self_updating_source_download():
    source = Path(setup.__file__).read_text(encoding="utf-8")

    assert "urlretrieve" not in source
    assert "extractall" not in source
    assert "git clone" not in source


def test_dashboard_does_not_download_or_redirect_script_dependencies():
    source = (Path(__file__).parents[1] / "dashboard" / "server.py").read_text(encoding="utf-8")

    assert "urlretrieve" not in source
    assert "cdnjs.cloudflare.com" not in source
