"""P0-D1 — hermetic tests for config/loader.py.

The real config/api_keys.json is never touched: every test redirects the
loader to a tmp_path via the ``base_dir`` fixture. Run from the repo root:
    python -m pytest tests/test_config_loader.py -v
"""
import json
import threading

import pytest

from config import loader


@pytest.fixture
def base_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(loader, "get_base_dir", lambda: tmp_path)
    return tmp_path


def test_config_path_layout(base_dir):
    assert loader.config_path() == base_dir / "config" / "api_keys.json"


def test_load_missing_file_returns_empty(base_dir):
    assert loader.load_config() == {}


def _seed_raw_config(base_dir, text: str) -> None:
    """Write a raw file into the (not yet existing) config dir."""
    p = loader.config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_load_corrupt_json_returns_empty(base_dir):
    _seed_raw_config(base_dir, "{not json")
    assert loader.load_config() == {}


def test_load_non_dict_json_returns_empty(base_dir):
    _seed_raw_config(base_dir, "[1, 2, 3]")
    assert loader.load_config() == {}


def test_save_and_load_roundtrip(base_dir):
    cfg = {"gemini_api_key": "k", "assistant_name": "ULTRON", "n": 42}
    loader.save_config(cfg)
    assert loader.load_config() == cfg
    assert not list(loader.config_path().parent.glob("*.tmp"))


def test_save_creates_missing_config_dir(base_dir):
    loader.save_config({"a": 1})
    assert loader.config_path().exists()


def test_save_rejects_non_dict(base_dir):
    with pytest.raises(TypeError):
        loader.save_config(["nope"])


def test_save_config_key_persists_and_preserves_existing(base_dir):
    loader.save_config({"keep": "me"})
    loader.save_config_key("camera_index", 2)
    assert loader.load_config() == {"keep": "me", "camera_index": 2}


def test_get_api_key_missing(base_dir):
    assert loader.get_api_key() is None


def test_get_api_key_placeholder(base_dir):
    loader.save_config({"gemini_api_key": loader.PLACEHOLDER_KEY})
    assert loader.get_api_key() is None


def test_get_api_key_strips_whitespace(base_dir):
    loader.save_config({"gemini_api_key": "  abc123  "})
    assert loader.get_api_key() == "abc123"


def test_get_api_key_arbitrary_key_names(base_dir):
    loader.save_config({"groq_api_key": "gsk_x", "openai_api_key": 123})
    assert loader.get_api_key("groq_api_key") == "gsk_x"
    assert loader.get_api_key("openai_api_key") is None   # non-string → None
    assert loader.get_api_key("never_set") is None


def test_concurrent_save_config_key_keeps_all_writes(base_dir):
    errors: list[Exception] = []

    def worker(t: int) -> None:
        try:
            for i in range(25):
                loader.save_config_key(f"k{t}_{i}", i)
        except Exception as e:   # pragma: no cover — surfaced via the assert
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    for th in threads:
        th.start()
    for th in threads:
        th.join()

    assert errors == []
    cfg = loader.load_config()
    assert len(cfg) == 200
    assert cfg["k3_24"] == 24


def test_file_on_disk_is_valid_json_after_save(base_dir):
    loader.save_config({"a": 1})
    raw = json.loads(loader.config_path().read_text(encoding="utf-8"))
    assert raw == {"a": 1}
