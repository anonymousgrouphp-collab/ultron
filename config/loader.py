"""
config/loader.py — ULTRON's single source of truth for configuration (P0-D1).

One module, one file: ``config/api_keys.json``. In P0-D2 the parallel access
paths are migrated onto this module and deleted:
  • main.py                 (_get_api_key + inline json.loads in _build_config)
  • ui.py                   (_read_full_config / _save_full_config / _needs_api_key)
  • utils/env.py            (load_config / get_api_key / save_config_key)
  • memory/config_manager.py (load_api_keys + typed getters)
  • config/__init__.py      (get_config — legacy relict)

Design rules (why it looks like this):
  • stdlib-only and self-contained — the bottom layer; everything else may
    import this module, never the reverse.
  • reads are lock-free and always fresh. No cache: the file is tiny, and the
    old paths had three different cache policies (lru_cache, manual dict, none)
    that could serve stale values after external edits.
  • writes take a lock and are atomic (temp file + os.replace) with a short
    retry — on Windows os.replace fails while another thread holds the
    destination open for reading.
  • one missing-key policy: get_api_key returns None for absent, empty, or
    placeholder values; callers decide whether that raises.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

__all__ = [
    "PLACEHOLDER_KEY",
    "get_base_dir",
    "config_path",
    "load_config",
    "save_config",
    "save_config_key",
    "get_api_key",
    "get_dashboard_host",
]

PLACEHOLDER_KEY = "YOUR_GEMINI_API_KEY_HERE"

_WRITE_RETRIES    = 3
_WRITE_RETRY_WAIT = 0.05  # seconds

_write_lock = threading.RLock()


def get_base_dir() -> Path:
    """Repo root, or the .exe's folder when frozen. Tests may monkeypatch this."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def config_path() -> Path:
    return get_base_dir() / "config" / "api_keys.json"


def load_config() -> dict:
    """Fresh read of the config dict. {} on missing/corrupt/non-dict — never raises."""
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):   # ValueError covers JSONDecodeError/UnicodeDecodeError
        return {}
    return data if isinstance(data, dict) else {}


def save_config(cfg: dict) -> None:
    """Atomically replace the whole config file (creates the folder if needed)."""
    if not isinstance(cfg, dict):
        raise TypeError(f"config must be a dict, got {type(cfg).__name__}")
    with _write_lock:
        _atomic_write(config_path(), json.dumps(cfg, indent=4, ensure_ascii=False))


def save_config_key(key: str, value: Any) -> None:
    """Locked read-modify-write of a single key."""
    with _write_lock:
        cfg = load_config()
        cfg[key] = value
        _atomic_write(config_path(), json.dumps(cfg, indent=4, ensure_ascii=False))


def get_api_key(key_name: str = "gemini_api_key") -> str | None:
    """The stored key, or None when missing, empty, or the placeholder value."""
    raw = load_config().get(key_name)
    if not isinstance(raw, str):
        return None
    raw = raw.strip()
    return raw if raw and raw != PLACEHOLDER_KEY else None


def get_dashboard_host() -> str:
    """Dashboard bind address: 127.0.0.1 by default; LAN exposure (0.0.0.0)
    only via explicit ULTRON_DASHBOARD_HOST opt-in (P0-B2 policy, P0-D2 home)."""
    host = os.environ.get("ULTRON_DASHBOARD_HOST", "").strip()
    return host or "127.0.0.1"


def _atomic_write(target: Path, text: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    last_err: Exception | None = None
    for _ in range(_WRITE_RETRIES):
        fd, tmp_name = tempfile.mkstemp(
            dir=target.parent, prefix=target.name + ".", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(tmp_name, target)
            return
        except PermissionError as e:   # Windows: a concurrent reader held the target
            last_err = e
            _silent_unlink(tmp_name)
            time.sleep(_WRITE_RETRY_WAIT)
        except BaseException:
            _silent_unlink(tmp_name)
            raise
    assert last_err is not None
    raise last_err


def _silent_unlink(path: str) -> None:
    try:
        os.unlink(path)
    except OSError:
        pass
