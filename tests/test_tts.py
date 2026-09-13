"""tests/test_tts.py — hermetic tests for the TTS seam (kernel/voice/tts.py).

No onnxruntime / kokoro_onnx / piper import at test time: the engines load
their backends lazily, so we exercise the pure parts (conversion, resampling,
pronunciation, config selection, sentence-chunk plumbing) with a fake engine.
"""

from pathlib import Path

import numpy as np
import pytest

from kernel.voice import tts as tts_mod
from kernel.voice.tts import (
    EngineUnavailable,
    KokoroTtsEngine,
    Pronouncer,
    TtsChunk,
    load_from_config,
    resample_linear,
    to_int16,
)


# --- pure helpers -----------------------------------------------------------

def test_to_int16_scale_and_clip() -> None:
    out = to_int16(np.array([0.5, -2.0, 0.0], dtype=np.float32), volume=1.0)
    assert out.dtype == np.int16
    assert out[0] == int(0.5 * 32767)
    assert out[1] == -32767  # clipped, not wrapped
    assert out[2] == 0


def test_to_int16_volume() -> None:
    out = to_int16(np.array([1.0], dtype=np.float32), volume=0.5)
    assert out[0] == int(0.5 * 32767)


def test_resample_length_and_passthrough() -> None:
    src = np.zeros(22050, dtype=np.int16)
    dst = resample_linear(src, 22050, 24000)
    assert dst.shape[-1] == 24000
    assert resample_linear(src, 24000, 24000) is src  # no-op returns same array
    assert resample_linear(np.array([], dtype=np.int16), 8000, 16000).size == 0


def test_resample_preserves_energy_shape() -> None:
    t = np.arange(0, 1, 1 / 8000, dtype=np.float32)
    sine = np.sin(2 * np.pi * 440 * t).astype(np.float32)
    up = resample_linear(sine, 8000, 16000)
    assert up.shape[-1] == 16000
    assert float(np.max(np.abs(up))) == pytest.approx(1.0, abs=0.05)


# --- pronunciation (A4) -----------------------------------------------------

def test_pronouncer_piper_wraps_ipa(tmp_path: Path) -> None:
    f = tmp_path / "pronounce.json"
    f.write_text('{"terms": {"ultron": {"ipa": "ˈʌl.tɹən", "say": "UL-tron"}}}')
    p = Pronouncer(f)
    assert p.apply("Hello ULTRON here", "piper") == "Hello [[ˈʌl.tɹən]] here"
    assert p.apply("Hello Ultron here", "kokoro") == "Hello UL-tron here"


def test_pronouncer_missing_file_is_noop(tmp_path: Path) -> None:
    p = Pronouncer(tmp_path / "absent.json")
    assert p.apply("Say ULTRON", "piper") == "Say ULTRON"


def test_pronouncer_partial_spec_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "pronounce.json"
    f.write_text('{"terms": {"jarvis": {"ipa": "ˈdʒɑː.vɪs"}}}')  # no "say"
    p = Pronouncer(f)
    assert p.apply("JARVIS online", "kokoro") == "JARVIS online"  # term kept


# --- engine selection (A1) --------------------------------------------------

def test_load_from_config_none_backend() -> None:
    assert load_from_config({}) is None
    assert load_from_config({"tts_backend": "none"}) is None


def test_load_from_config_unknown_backend() -> None:
    with pytest.raises(EngineUnavailable, match="unknown tts_backend"):
        load_from_config({"tts_backend": "elevenlabs"})


def _patch_fake_kokoro(monkeypatch, create_fn=None):
    """Install a FakeKokoro so engine tests never touch onnxruntime/espeak."""
    import numpy as np
    import kokoro_onnx

    class FakeKokoro:
        def __init__(self, voices_path):
            self.voices = np.load(voices_path)

        @classmethod
        def from_session(cls, session, voices_path):
            return cls(voices_path)

        def create(self, text, voice, speed=1.0, lang="en-us", **kw):
            if create_fn:
                return create_fn(text, voice, speed, lang)
            return np.zeros(24000, dtype=np.float32), 24000

    monkeypatch.setattr(kokoro_onnx, "Kokoro", FakeKokoro)
    monkeypatch.setattr(tts_mod, "_make_session", lambda model_path: object())
    return FakeKokoro


def test_kokoro_engine_rejects_bad_voice(tmp_path: Path, monkeypatch) -> None:
    """KokoroTtsEngine validates the voice against the npz before speaking."""
    import numpy as np

    npz = tmp_path / "voices.npz"
    np.savez(npz, bm_george=np.zeros((510, 256), dtype=np.float32))
    _patch_fake_kokoro(monkeypatch)
    with pytest.raises(Exception, match="voice 'nope'"):
        KokoroTtsEngine(tmp_path / "model.onnx", npz, voice="nope")


# --- sentence-chunk plumbing (A2+A1 integration) ----------------------------

def test_kokoro_engine_yields_per_sentence(tmp_path: Path, monkeypatch) -> None:
    import numpy as np

    npz = tmp_path / "voices.npz"
    np.savez(npz, bm_george=np.zeros((510, 256), dtype=np.float32))
    created: list[str] = []
    _patch_fake_kokoro(
        monkeypatch,
        create_fn=lambda text, voice, speed, lang: (
            np.full(2400, 0.25, dtype=np.float32), 24000
        ),
    )

    eng = KokoroTtsEngine(tmp_path / "model.onnx", npz, voice="bm_george")
    chunks = list(eng.synthesize("Yes sir. On it."))
    assert created == []  # stub create records via wrapper below

    # assert on chunk semantics instead of the create log (create_fn is shared)
    assert len(chunks) == 2
    assert all(isinstance(c, TtsChunk) and c.sample_rate == 24000 for c in chunks)
    assert chunks[0].audio_int16[0] == int(0.25 * 32767)
    eng.close()


def test_kokoro_engine_calls_create_per_sentence(tmp_path: Path, monkeypatch) -> None:
    import numpy as np

    npz = tmp_path / "voices.npz"
    np.savez(npz, bm_george=np.zeros((510, 256), dtype=np.float32))
    created: list[str] = []

    def create(text, voice, speed, lang, **kw):
        created.append(text)
        return np.zeros(240, dtype=np.float32), 24000

    _patch_fake_kokoro(monkeypatch, create_fn=create)
    eng = KokoroTtsEngine(tmp_path / "model.onnx", npz, voice="bm_george")
    list(eng.synthesize("Yes sir. On it."))
    assert created == ["Yes sir.", "On it."]  # one create() per sentence


def test_metrics_recorded(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(tts_mod, "METRICS_PATH", tmp_path / "metrics.jsonl")
    npz = tmp_path / "voices.npz"
    np.savez(npz, bm_george=np.zeros((510, 256), dtype=np.float32))
    _patch_fake_kokoro(monkeypatch)  # 24000 zeros @ 24000 Hz = 1s audio

    eng = KokoroTtsEngine(tmp_path / "model.onnx", npz, voice="bm_george")
    list(eng.synthesize("One line."))
    lines = (tmp_path / "metrics.jsonl").read_text().splitlines()
    assert len(lines) == 1
    import json

    rec = json.loads(lines[0])
    assert rec["backend"] == "kokoro" and rec["chars"] == len("One line.")
    assert rec["audio_s"] == 1.0


def test_session_compat_reshapes_style_and_speed() -> None:
    from kernel.voice.tts import _KokoroSessionCompat

    captured = {}

    class RealSession:
        def run(self, names, feed):
            captured.update(feed)
            return ["audio"]

    proxy = _KokoroSessionCompat(RealSession())
    proxy.run(
        None,
        {
            "input_ids": [[1, 2]],
            "style": np.zeros(256, dtype=np.float32),
            "speed": np.array([1], dtype=np.int32),
        },
    )
    assert captured["style"].shape == (1, 256)
    assert captured["speed"].dtype == np.float32
