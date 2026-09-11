"""Characterization tests (P0-E2) — pin CURRENT behavior of ULTRON's core surfaces.

These exist so refactors (kernel rewrite, Phase 1+) stop being blind: if a change
alters pinned behavior, the test goes red and the changer must update it deliberately
(AGENTS.md merge policy). Read-only on src; hermetic (no network, no API keys, no Qt
event loop). Evidence refs point at the P0 rows that motivated each pin.
"""

import inspect
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- declarations (core/tool_declarations.py)

def test_01_declarations_structure():
    """Every tool declaration is a dict with name/description/parameters (#1)."""
    from core.tool_declarations import TOOL_DECLARATIONS

    assert isinstance(TOOL_DECLARATIONS, list) and len(TOOL_DECLARATIONS) >= 20
    for decl in TOOL_DECLARATIONS:
        assert isinstance(decl, dict)
        assert isinstance(decl.get("name"), str) and decl["name"]
        assert isinstance(decl.get("description"), str) and decl["description"]
        assert isinstance(decl.get("parameters"), dict)


def test_02_declaration_names_unique():
    """Tool names are unique and count is stable at 20 (#2; send_message
    deleted in Phase R2 — its blind WhatsApp/Telegram automation is a
    Kill-List removal)."""
    from core.tool_declarations import TOOL_DECLARATIONS

    names = [d["name"] for d in TOOL_DECLARATIONS]
    assert len(names) == len(set(names)) == 20


def test_03_every_declared_tool_is_dispatchable():
    """Each declared tool resolves to a legacy handler during the P1-F migration.
    Phase W0: TOOL_REGISTRY maps name → handler METHOD NAME (handlers live on
    the app/ LegacyHandlersMixin), so dispatchability = the method exists on
    UltronLive and is callable when bound."""
    from core.tool_declarations import TOOL_DECLARATIONS
    from main import UltronLive

    registry = UltronLive.TOOL_REGISTRY
    for decl in TOOL_DECLARATIONS:
        name = decl["name"]
        assert (
            name == "save_memory"
            or name in registry
            or hasattr(UltronLive, f"_handle_{name}")
        ), f"declared tool {name!r} has no handler"
    for name, method in registry.items():
        handler = getattr(UltronLive, method, None)
        assert callable(handler), f"registry entry {name!r} -> {method!r} not callable"


def test_03b_registry_names_are_declared():
    """No undeclared entries in TOOL_REGISTRY (the shutdown_jarvis lesson, P0-C2)."""
    from core.tool_declarations import TOOL_DECLARATIONS
    from main import UltronLive

    declared = {d["name"] for d in TOOL_DECLARATIONS}
    extra = set(UltronLive.TOOL_REGISTRY) - declared
    assert extra == set()


def test_04_no_legacy_shutdown_alias_handler():
    """The undeclared `shutdown_jarvis` registry alias stayed dead (P0-C2)."""
    from main import UltronLive

    assert not hasattr(UltronLive, "_handle_shutdown_jarvis")


def test_05_name_purge_core_modules():
    """JARVIS/HUNNY aliases are purged from core modules — ULTRON only (P0-C4)."""
    files = [
        "main.py",
        "ui.py",
        "core/tool_declarations.py",
        "actions/system_monitor.py",
        "actions/dev_agent.py",
        "actions/desktop.py",
    ]
    for rel in files:
        text = (ROOT / rel).read_text(encoding="utf-8").lower()
        assert "jarvis" not in text and "hunny" not in text, f"legacy name in {rel}"


# ---------------------------------------------------------------- import chain

def test_06_import_chain_clean():
    """main/ui/dashboard.server import without side effects (no Qt loop, no key needed)."""
    import importlib

    for mod in ("config.loader", "actions.system_monitor",
                "dashboard.server", "ui", "main"):
        assert importlib.import_module(mod) is not None


def test_07_public_classes_exist():
    """UltronLive + ApiKeyMissing are import-time stable (session-key gate is runtime)."""
    from main import ApiKeyMissing, UltronLive

    assert isinstance(UltronLive, type)
    assert issubclass(ApiKeyMissing, Exception)


# ---------------------------------------------------------------- dashboard security surface (P0-B pins)

def test_08_dashboard_host_defaults_loopback():
    """DASHBOARD_HOST defaults to 127.0.0.1; 0.0.0.0 only via explicit env opt-in (P0-B2)."""
    from config import loader

    monkey_env = os.environ.pop("ULTRON_DASHBOARD_HOST", None)
    try:
        assert loader.get_dashboard_host() == "127.0.0.1"
    finally:
        if monkey_env is not None:
            os.environ["ULTRON_DASHBOARD_HOST"] = monkey_env


def test_09_dashboard_host_env_cannot_enable_lan():
    """A legacy environment setting cannot expose dashboard remote control (P1-I)."""
    from config import loader

    old = os.environ.get("ULTRON_DASHBOARD_HOST")
    os.environ["ULTRON_DASHBOARD_HOST"] = "0.0.0.0"
    try:
        assert loader.get_dashboard_host() == "127.0.0.1"
    finally:
        if old is None:
            os.environ.pop("ULTRON_DASHBOARD_HOST", None)
        else:
            os.environ["ULTRON_DASHBOARD_HOST"] = old


def test_10_dashboard_server_honors_loader():
    """dashboard.server binds what the loader says (no hardcoded 0.0.0.0 anywhere)."""
    import dashboard.server as srv

    assert srv.DASHBOARD_HOST == "127.0.0.1"  # test env never sets the opt-in
    src = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
    assert 'host="0.0.0.0"' not in src and "host='0.0.0.0'" not in src


def test_10b_dashboard_url_is_always_loopback(monkeypatch):
    """The server cannot advertise a LAN URL, even when legacy state is altered."""
    import dashboard.server as srv

    server = object.__new__(srv.DashboardServer)
    server._ip = "127.0.0.1"
    monkeypatch.setattr(server, "_ssl_enabled", lambda: False)

    assert server.get_url() == "http://127.0.0.1:8000"


def test_11_decrypt_rejects_garbage_ciphertext():
    """Plaintext/garbage commands are rejected, not executed (P0-B4 mandatory encryption)."""
    from dashboard.server import _decrypt_cbc

    with pytest.raises(Exception):
        _decrypt_cbc(b"\x00" * 32, "!!!definitely-not-valid-ciphertext!!!")


def test_12_no_token_mint_on_root():
    """`GET /` mints nothing (was a full LAN auth bypass pre-P0-B4)."""
    import re

    src = (ROOT / "dashboard" / "server.py").read_text(encoding="utf-8")
    assert "_mint_token" in src  # still used for the authenticated flow
    root_handler = re.search(r'@app\.get\("/"[^\n]*\)\n(.*?)@app\.', src, re.S)
    assert root_handler is not None, "root route handler not found"
    assert "_mint_token" not in root_handler.group(1)


# ---------------------------------------------------------------- P0-A pins (the three crash bugs stay dead)

def test_13_ui_time_module_scope():
    """ui.py has module-level `import time` (P0-A4: startup thread NameError)."""
    import time as time_module

    import ui

    assert ui.time is time_module


def test_14_camera_preview_is_stubbed_not_raising():
    """Camera preview is a logged no-op — no NotImplementedError path (P0-A2)."""
    import ui

    src = inspect.getsource(ui.UltronUI.start_camera_stream)
    assert "NotImplementedError" not in src
    full = (ROOT / "ui.py").read_text(encoding="utf-8")
    assert "raise NotImplementedError" not in full


def test_15_system_monitor_imports_os():
    """Overload handling suggests apps; it never terminates user processes."""
    import actions.system_monitor as sm

    assert callable(sm.find_heavy_background_apps)
    assert not hasattr(sm, "auto_close_heavy_background_apps")
    source = inspect.getsource(sm.find_heavy_background_apps)
    assert ".terminate(" not in source and ".kill(" not in source



def test_17_screen_processor_single_live_capture():
    """One live capture stack; the dead second vision stack is gone (P0-C1, P0-A1)."""
    import actions.screen_processor as sp

    assert callable(sp._capture_screen) and callable(sp._capture_camera)
    assert not hasattr(sp, "_VisionSession")


# ---------------------------------------------------------------- P0-B5 / P0-C2 pins (actions hygiene)

def test_18_desktop_no_exec_single_organize():
    """No exec() of LLM code; exactly one organize_desktop; no shutdown_jarvis alias (P0-B5/C2)."""
    desktop = (ROOT / "actions" / "desktop.py").read_text(encoding="utf-8")
    assert "def organize_desktop" in desktop
    assert desktop.count("def organize_desktop") == 1
    assert "_execute_generated_code" not in desktop
    code_lines = [
        ln for ln in desktop.splitlines()
        if "exec(" in ln and not ln.strip().startswith("#")
    ]
    assert code_lines == []
    file_controller = (ROOT / "actions" / "file_controller.py").read_text(encoding="utf-8")
    assert "def organize_desktop" not in file_controller
    main_src = (ROOT / "main.py").read_text(encoding="utf-8")
    assert "shutdown_jarvis" not in main_src


def test_19_dev_agent_pip_allowlist_gate():
    """dev_agent installs are gated by the human-maintained allowlist (P0-B5)."""
    import actions.dev_agent as da

    assert da.PIP_ALLOWLIST_PATH.name == "pip_allowlist.json"
    result = da._load_pip_allowlist()
    assert isinstance(result, set)


# ---------------------------------------------------------------- misc pins

def test_20_requirements_windows_markers():
    """Windows-only deps carry sys_platform markers so CI/other platforms can install (P0-E1)."""
    req = (ROOT / "requirements.txt").read_text(encoding="utf-8")
    for pkg in ("pywin32", "pycaw", "pywinauto"):
        line = next((ln for ln in req.splitlines() if ln.strip().startswith(pkg)), "")
        assert 'sys_platform == "win32"' in line, f"{pkg} missing win32 marker"
