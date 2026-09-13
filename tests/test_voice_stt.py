"""S1 seam tests — kernel/voice/stt.py (hermetic: faster_whisper is faked)."""

from __future__ import annotations

import sys
import types

import numpy as np
import pytest

from kernel.voice.engines import EngineUnavailable
from kernel.voice.stt import (
    FasterWhisperSttEngine,
    SttResult,
    load_from_config,
    pcm_to_float,
)


class _FakeSegment:
    def __init__(self, text, words=None):
        self.text = text
        self.words = words or []


class _FakeInfo:
    language = "en"
    language_probability = 0.87


class _FakeWhisperModel:
    instances = []
    transcribe_calls = []

    def __init__(self, **kwargs):
        self.init_kwargs = kwargs
        _FakeWhisperModel.instances.append(self)

    def transcribe(self, audio, **kwargs):
        _FakeWhisperModel.transcribe_calls.append(kwargs)
        words = None
        if kwargs.get("word_timestamps"):
            words = [
                types.SimpleNamespace(word=" hello", start=0.0, end=0.4),
                types.SimpleNamespace(word=" world", start=0.5, end=0.9),
            ]
        return iter([_FakeSegment(" hello world ", words=words)]), _FakeInfo()


@pytest.fixture()
def fake_faster_whisper(monkeypatch):
    _FakeWhisperModel.instances = []
    _FakeWhisperModel.transcribe_calls = []
    module = types.ModuleType("faster_whisper")
    module.WhisperModel = _FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", module)
    return module


def test_pcm_to_float_converts_int16():
    pcm = np.array([0, 16384, -32768], dtype=np.int16)
    out = pcm_to_float(pcm)
    assert out.dtype == np.float32
    assert out[1] == pytest.approx(0.5, abs=1e-3)
    assert out[2] == pytest.approx(-1.0, abs=1e-3)


def test_pcm_to_float_passthrough_float32():
    data = np.array([0.25, -0.25], dtype=np.float32)
    assert pcm_to_float(data) is data


def test_transcribe_joins_segments_and_reports_language(
    fake_faster_whisper, tmp_path
):
    engine = FasterWhisperSttEngine(download_root=tmp_path)
    pcm = (np.sin(np.linspace(0, 100, 8000)) * 8000).astype(np.int16)
    result = engine.transcribe(pcm, 16000)
    assert isinstance(result, SttResult)
    assert result.text == "hello world"
    assert result.language == "en"
    assert result.language_probability == pytest.approx(0.87)
    assert result.words == ()


def test_transcribe_short_audio_returns_empty_without_model_call(
    fake_faster_whisper, tmp_path
):
    engine = FasterWhisperSttEngine(download_root=tmp_path)
    result = engine.transcribe(np.zeros(100, dtype=np.int16), 16000)  # ~6 ms
    assert result.text == ""
    assert _FakeWhisperModel.transcribe_calls == []


def test_transcribe_word_timestamps_metadata(fake_faster_whisper, tmp_path):
    engine = FasterWhisperSttEngine(download_root=tmp_path, word_timestamps=True)
    pcm = (np.sin(np.linspace(0, 100, 8000)) * 8000).astype(np.int16)
    result = engine.transcribe(pcm, 16000)
    assert len(result.words) == 2
    assert result.words[0]["word"] == " hello"
    assert result.words[0]["end"] == pytest.approx(0.4)


def test_engine_unavailable_names_package(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    with pytest.raises(EngineUnavailable) as excinfo:
        FasterWhisperSttEngine(download_root=tmp_path)
    assert "faster-whisper" in str(excinfo.value)


def test_load_from_config_maps_keys(fake_faster_whisper, tmp_path):
    engine = load_from_config(
        {
            "stt_backend": "faster_whisper",
            "stt_model": "base",
            "stt_device": "cpu",
            "stt_compute_type": "int8",
            "stt_language": "en",
            "stt_beam_size": 3,
            "stt_download_root": str(tmp_path),
        }
    )
    assert isinstance(engine, FasterWhisperSttEngine)
    model = _FakeWhisperModel.instances[-1]
    assert model.init_kwargs["model_size_or_path"] == "base"
    assert model.init_kwargs["device"] == "cpu"
    assert model.init_kwargs["compute_type"] == "int8"
    assert engine.beam_size == 3
    assert engine.language == "en"


def test_load_from_config_rejects_unknown_and_disabled():
    with pytest.raises(ValueError):
        load_from_config({"stt_backend": "whisperx"})
    with pytest.raises(ValueError):
        load_from_config({})
