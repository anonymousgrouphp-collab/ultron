"""Create ULTRON's supported local runtime from a complete checkout.

Setup intentionally does not download, merge, or replace source files. A missing
file means the checkout is incomplete and must be repaired through the normal
source-control workflow. The only mutable setup output is the local virtual
environment and an optional local API-key template.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import Sequence

SCRIPT_DIR = Path(__file__).resolve().parent
SUPPORTED_PYTHON = (3, 14)
REQUIRED_PROJECT_FILES = (
    "main.py",
    "ui.py",
    "requirements.txt",
    "core/prompt.txt",
    "actions/browser_control.py",
    "dashboard/server.py",
    "kernel/__init__.py",
)


def missing_project_files() -> list[str]:
    """Return the checkout files needed before an environment can be created."""
    missing = [relative for relative in REQUIRED_PROJECT_FILES
               if not (SCRIPT_DIR / relative).is_file()]
    config_dir = SCRIPT_DIR / "config"
    if not ((config_dir / "api_keys.json").is_file()
            or (config_dir / "api_keys.json.example").is_file()):
        missing.append("config/api_keys.json.example")
    return missing


def validate_python_version(version: tuple[int, int] | None = None) -> None:
    """Reject interpreter lines that ULTRON does not verify in CI."""
    current = version or sys.version_info[:2]
    if current != SUPPORTED_PYTHON:
        expected = ".".join(map(str, SUPPORTED_PYTHON))
        actual = ".".join(map(str, current))
        raise RuntimeError(f"ULTRON requires Python {expected}; found Python {actual}.")


def virtualenv_python() -> Path:
    return SCRIPT_DIR / ".venv" / "Scripts" / "python.exe"


def ensure_project_checkout() -> None:
    missing = missing_project_files()
    if missing:
        details = "\n  - ".join(missing)
        raise RuntimeError(
            "This directory is not a complete ULTRON checkout. Missing:\n"
            f"  - {details}\n"
            "Restore the checkout with Git or a verified project archive, then rerun setup."
        )


def create_virtualenv() -> Path:
    """Create the project-local environment exactly once and return its Python."""
    python = virtualenv_python()
    if python.is_file():
        return python
    print("[setup] Creating .venv with the current Python 3.14 interpreter...")
    subprocess.run([sys.executable, "-m", "venv", ".venv"], cwd=SCRIPT_DIR, check=True)
    if not python.is_file():
        raise RuntimeError("Virtual environment creation did not produce .venv/Scripts/python.exe.")
    return python


def install_requirements(python: Path) -> None:
    """Install the reviewed, pinned direct dependency set into the project venv."""
    print("[setup] Installing requirements into .venv...")
    subprocess.run(
        [str(python), "-m", "pip", "install", "--require-virtualenv", "-r", "requirements.txt"],
        cwd=SCRIPT_DIR,
        check=True,
    )
    subprocess.run([str(python), "-m", "pip", "check"], cwd=SCRIPT_DIR, check=True)


def install_playwright_browser(python: Path) -> None:
    """Install Chromium only when the user explicitly needs browser tooling."""
    print("[setup] Installing Playwright Chromium...")
    subprocess.run([str(python), "-m", "playwright", "install", "chromium"], cwd=SCRIPT_DIR, check=True)


def ensure_config_file() -> bool:
    """Copy the example config once; never modify an existing user config file."""
    config_dir = SCRIPT_DIR / "config"
    target = config_dir / "api_keys.json"
    if target.is_file():
        return False
    example = config_dir / "api_keys.json.example"
    if not example.is_file():
        raise RuntimeError("config/api_keys.json.example is required to create local config.")
    target.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    print("[setup] Created config/api_keys.json from the example. Add your Gemini API key there.")
    return True


def setup(*, with_browser: bool = False) -> Path:
    ensure_project_checkout()
    validate_python_version()
    python = create_virtualenv()
    install_requirements(python)
    if with_browser:
        install_playwright_browser(python)
    ensure_config_file()
    return python


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create ULTRON's local Python 3.14 environment.")
    parser.add_argument(
        "--with-browser",
        action="store_true",
        help="install the Playwright Chromium binary for browser tooling",
    )
    parser.add_argument(
        "--launch",
        action="store_true",
        help="launch ULTRON after a successful setup",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        python = setup(with_browser=args.with_browser)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"[setup] Failed: {exc}", file=sys.stderr)
        return 1

    print("[setup] ULTRON environment is ready.")
    print(r"[setup] Launch with START_ULTRON.bat or .venv\Scripts\python.exe main.py")
    if args.launch:
        return subprocess.run([str(python), "main.py"], cwd=SCRIPT_DIR).returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
