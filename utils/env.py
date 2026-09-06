"""OS detection helpers + config access for ULTRON.

Since P0-D2 the config implementation lives in `config/loader.py` (the single
source of truth); the functions below are thin delegations kept for the many
existing `actions/*` call sites. New code should import `config.loader`
directly.
"""
import platform
from functools import lru_cache

from config import loader


def get_base_dir():
    return loader.get_base_dir()

@lru_cache(maxsize=1)
def get_os() -> str:
    return platform.system()

def is_windows() -> bool:
    return get_os() == "Windows"

def is_mac() -> bool:
    return get_os() == "Darwin"

def is_linux() -> bool:
    return get_os() == "Linux"

def load_config() -> dict:
    return loader.load_config()

def get_api_key(service_name: str) -> str:
    return loader.load_config().get(service_name, "")

def save_config_key(key: str, value) -> None:
    loader.save_config_key(key, value)
