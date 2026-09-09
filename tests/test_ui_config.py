"""Phase R4 pins: the engine-settings UI writes exactly what the kernel
gateway reads. Offscreen Qt (no display, no event loop); config access is
monkeypatched — these tests never touch the real config file.
"""

from __future__ import annotations

import os
from pathlib import Path

os.environ["QT_QPA_PLATFORM"] = "offscreen"

import pytest  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def qapp():
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture()
def config_store(monkeypatch):
    import ui
    store = {
        "assistant_name": "ULTRON",
        "morning_brief_enabled": True,
        "gemini_api_key": "test-key",
    }
    monkeypatch.setattr(ui, "_read_full_config", lambda: dict(store))
    saved: dict = {}

    def save(cfg: dict) -> None:
        saved.clear()
        saved.update(cfg)

    monkeypatch.setattr(ui, "_save_full_config", save)
    return store, saved


def test_needs_api_key(monkeypatch):
    import ui
    cases = [
        ({"llm_provider": "ollama"}, False),
        ({"llm_provider": "gemini"}, True),
        ({"llm_provider": "gemini", "gemini_api_key": "k"}, False),
        ({"llm_provider": "openai"}, True),
        ({"llm_provider": "openai", "openai_api_key": "k"}, False),
        ({"llm_provider": "groq", "openai_api_key": "k"}, False),
        ({}, True),  # default gemini, no key
    ]
    for cfg, expected in cases:
        monkeypatch.setattr(ui, "_read_full_config", lambda cfg=cfg: cfg)
        assert ui._needs_api_key() is expected, cfg


def _save_for(qapp, config_store, prov, *, url="http://x/v1", model="m1",
              key="k"):
    import ui
    store, saved = config_store
    store["llm_provider"] = prov
    dlg = ui.EngineSettingsDialog()
    dlg.provider_combo.setCurrentIndex(dlg.provider_combo.findData(prov))
    dlg.url_input.setText(url)
    dlg.model_input.setText(model)
    dlg.api_key_input.setText(key)
    dlg._on_save()
    return saved


def test_save_gemini_writes_gateway_keys(qapp, config_store):
    from kernel.gateway import GatewaySettings
    saved = _save_for(qapp, config_store, "gemini")
    assert saved["llm_provider"] == "gemini"
    assert saved["gemini_model"] == "m1"
    assert saved["gemini_api_key"] == "k"
    for stale in ("llm_url", "llm_model", "ollama_base_url", "ollama_model",
                  "openai_base_url", "openai_model", "openai_api_key"):
        assert stale not in saved, stale
    settings = GatewaySettings.from_config(saved)
    assert settings.provider.value == "gemini"
    assert settings.gemini_model == "m1"


def test_save_ollama_writes_gateway_keys(qapp, config_store):
    from kernel.gateway import GatewaySettings
    saved = _save_for(qapp, config_store, "ollama",
                      url="http://localhost:11434", model="qwen3:8b")
    assert saved["llm_provider"] == "ollama"
    assert saved["ollama_base_url"] == "http://localhost:11434"
    assert saved["ollama_model"] == "qwen3:8b"
    assert "openai_base_url" not in saved and "gemini_model" not in saved
    settings = GatewaySettings.from_config(saved)
    assert settings.provider.value == "ollama"
    assert settings.ollama_base_url == "http://localhost:11434"
    assert settings.ollama_model == "qwen3:8b"


def test_save_openai_writes_gateway_keys(qapp, config_store):
    from kernel.gateway import GatewaySettings
    saved = _save_for(qapp, config_store, "openai",
                      url="http://localhost:1234/v1", model="local-model")
    assert saved["llm_provider"] == "openai"
    assert saved["openai_base_url"] == "http://localhost:1234/v1"
    assert saved["openai_model"] == "local-model"
    assert saved["openai_api_key"] == "k"
    settings = GatewaySettings.from_config(saved)
    assert settings.provider.value == "openai"
    assert settings.openai_base_url == "http://localhost:1234/v1"


def test_save_groq_maps_to_openai_provider(qapp, config_store):
    from kernel.gateway import GatewaySettings
    saved = _save_for(qapp, config_store, "groq",
                      url="https://api.groq.com/openai/v1",
                      model="llama-3.3-70b-versatile")
    assert saved["llm_provider"] == "openai"  # groq is an OpenAI-compatible endpoint
    assert saved["openai_base_url"] == "https://api.groq.com/openai/v1"
    assert saved["openai_model"] == "llama-3.3-70b-versatile"
    settings = GatewaySettings.from_config(saved)
    assert settings.provider.value == "openai"
    assert settings.openai_base_url == "https://api.groq.com/openai/v1"


def test_ui_has_no_hardcoded_model_strings():
    src = (ROOT / "ui.py").read_text(encoding="utf-8")
    for banned in ("gemini-2.5-flash-native-audio-preview",
                   "gemini-3.6-flash", "gemini-3.5", "qwen2.5:7b",
                   "llama3.2:3b"):
        assert banned not in src, banned


def test_ui_defaults_are_gateway_constants():
    import ui
    from kernel.gateway import (
        DEFAULT_GEMINI_MODEL,
        DEFAULT_OLLAMA_MODEL,
        DEFAULT_OLLAMA_URL,
        DEFAULT_OPENAI_MODEL,
    )
    assert ui.DEFAULT_GEMINI_MODEL == DEFAULT_GEMINI_MODEL
    assert ui.DEFAULT_OLLAMA_MODEL == DEFAULT_OLLAMA_MODEL
    assert ui.DEFAULT_OLLAMA_URL == DEFAULT_OLLAMA_URL
    assert ui.DEFAULT_OPENAI_MODEL == DEFAULT_OPENAI_MODEL
