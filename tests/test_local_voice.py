"""S1 offline-loop tests — app/local_voice.py (hermetic: fake engine/VAD/host)."""

from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import numpy as np
import pytest

from app.local_voice import LocalVoiceConfig, LocalVoiceLoop
from kernel.voice.stt import SttResult


BLOCK = 512


class FakeClock:
    """Deterministic timeline: one tick per processed block (32 ms)."""

    def __init__(self, start=1000.0):
        self.t = start

    def __call__(self) -> float:
        return self.t

    def tick(self, dt=0.032):
        self.t += dt


class FakeVad:
    """Pops scripted probabilities per chunk."""

    def __init__(self, values):
        self.values = deque(values)

    def speech_probability(self, chunk):
        return self.values.popleft() if self.values else 0.0


class FakeEngine:
    engine_name = "fake"

    def __init__(self, texts=None):
        self.texts = list(texts or ["hello there"])
        self.calls = []

    def transcribe(self, pcm, sample_rate=16000):
        self.calls.append(np.asarray(pcm))
        text = self.texts.pop(0) if self.texts else ""
        return SttResult(text=text, language="en", language_probability=0.9)


def make_host(muted=False, gate=None):
    host = SimpleNamespace(ui=SimpleNamespace(muted=muted))
    host.gate_mic_frame = (
        (lambda frame: gate(frame)) if gate else (lambda frame: frame)
    )
    host.identify_speaker = lambda audio: None
    return host


def make_loop(vad_values, texts=None, **cfg_overrides):
    cfg = LocalVoiceConfig(
        enable_partials=cfg_overrides.pop("enable_partials", False),
        post_speech_silence=cfg_overrides.pop("post_speech_silence", 0.3),
        silence_confirmation=cfg_overrides.pop("silence_confirmation", 0.05),
        min_recording_seconds=cfg_overrides.pop("min_recording_seconds", 0.2),
        preroll_seconds=cfg_overrides.pop("preroll_seconds", 0.5),
        partial_min_new_seconds=0.01,
        **cfg_overrides,
    )
    texts_seen = []
    partials = []
    host = make_host()
    loop = LocalVoiceLoop(
        host=host,
        engine=FakeEngine(texts),
        vad=FakeVad(vad_values),
        on_text=texts_seen.append,
        on_partial=partials.append,
        config=cfg,
    )
    loop._clock = FakeClock()
    return loop, texts_seen, partials, host


def _block(level=100):
    return np.full(BLOCK, level, dtype=np.int16)


def feed(loop, count):
    for _ in range(count):
        loop._process_block(_block())
        loop._clock.tick()


def test_silence_then_speech_then_silence_yields_one_utterance():
    loop, texts, partials, _ = make_loop(
        [0.1] * 10 + [0.9] * 12 + [0.1] * 38
    )
    feed(loop, 10)          # silence — pre-roll fills
    feed(loop, 12)          # speech
    assert loop._recording
    feed(loop, 14)          # confirmed silence → finalize
    assert texts == ["hello there"]
    assert partials == []
    assert not loop._recording
    # Decoded audio covers the whole utterance (plus kept pre-roll).
    assert loop._engine.calls[0].size >= 12 * BLOCK


def test_silence_after_finalize_refills_preroll():
    loop, texts, _, _ = make_loop([0.1] * 10)
    feed(loop, 10)
    assert not loop._recording
    assert len(loop._preroll) == 10


def test_short_noise_fragment_finalizes_but_stays_silent():
    # A 96 ms VAD blip plus long silence DOES finalize once the recording
    # outlives min_recording_seconds (RealtimeSTT semantics) — but the
    # engine hears noise and returns an empty transcript, so nothing is
    # spoken and nothing reaches the router.
    loop, texts, _, _ = make_loop(
        [0.1] * 6 + [0.9] * 3 + [0.1] * 30,
        texts=[""],
        min_recording_seconds=0.5,
    )
    feed(loop, 6 + 3 + 30)
    assert texts == []
    assert not loop._recording


def test_callback_respects_mute_and_gate():
    loop, _, _, _ = make_loop([])
    loop._on_audio(_block(), None, None, None)
    assert loop._queue.qsize() == 1

    loop._host.ui.muted = True
    loop._on_audio(_block(), None, None, None)
    assert loop._queue.qsize() == 1  # unchanged

    loop._host.ui.muted = False
    loop._host.gate_mic_frame = lambda frame: None  # EchoGate gated
    loop._on_audio(_block(), None, None, None)
    assert loop._queue.qsize() == 1


def test_partial_lane_publishes_stable_display_once():
    loop, texts, partials, _ = make_loop(
        [0.1] * 2 + [0.9] * 10,
        enable_partials=True,
        texts=["hello world", "hello world"],
    )
    feed(loop, 2 + 10)
    assert loop._recording

    frames = loop._frames
    sample_count = sum(b.size for b in frames)
    loop._run_partial(loop._recording_id, 1, sample_count, list(frames), "timer")
    assert partials == ["hello world"]
    # Identical re-decode must not re-publish (stabilizer dedup).
    loop._run_partial(
        loop._recording_id, 2, sample_count + BLOCK, list(frames), "timer"
    )
    assert partials == ["hello world"]


def test_partial_from_stale_recording_is_dropped():
    loop, _, partials, _ = make_loop([0.9], texts=["stale text"])
    feed(loop, 1)
    stale_id = loop._recording_id + 5  # a later recording
    loop._run_partial(stale_id, 1, BLOCK, [_block()], "timer")
    assert partials == []


def test_finalize_resets_state_and_keeps_loop_reusable():
    loop, texts, _, _ = make_loop(
        [0.9] * 10 + [0.1] * 30 + [0.9] * 10 + [0.1] * 30, texts=["one", "two"]
    )
    feed(loop, 10 + 30 + 10 + 30)
    assert texts == ["one", "two"]
    assert loop._recording_id == 2


def test_voice_stack_arms_and_syncs_local_voice(monkeypatch):
    from types import SimpleNamespace as NS

    from app.voice_stack import VoiceStackMixin
    import kernel.voice.stt as stt_mod
    import kernel.voice.engines as engines_mod
    import app.local_voice as local_voice_mod

    created = {}

    class FakeLoop:
        def __init__(self, host, engine, vad, on_text, on_partial, config):
            created.update(host=host, on_text=on_text, config=config)
            self.running = False

        def start(self):
            self.running = True

        def stop(self):
            self.running = False

    monkeypatch.setattr(
        stt_mod, "load_from_config", lambda cfg: object(), raising=True
    )
    monkeypatch.setattr(
        engines_mod, "load_silero_vad", lambda: object(), raising=True
    )
    monkeypatch.setattr(local_voice_mod, "LocalVoiceLoop", FakeLoop)

    class Host(VoiceStackMixin):
        pass

    logs: list[str] = []
    routed: list[str] = []
    host = Host()
    host.ui = NS(write_log=lambda msg: logs.append(msg), muted=False)
    host.session = None
    host._on_text_command = lambda text: routed.append(text)

    host._setup_stt({"stt_backend": "faster_whisper"})
    assert host._stt is not None
    assert host._local_voice is not None
    assert any("Local STT armed" in msg for msg in logs)

    host.sync_local_voice()
    assert host._local_voice.running is True

    host.session = object()  # Live connected — the loop must yield the mic
    host.sync_local_voice()
    assert host._local_voice.running is False

    host._on_local_voice_text("what time is it")
    assert routed == ["what time is it"]


def test_voice_stack_degrades_cleanly_when_engine_missing(monkeypatch):
    from types import SimpleNamespace as NS

    from app.voice_stack import VoiceStackMixin
    import kernel.voice.stt as stt_mod
    from kernel.voice.engines import EngineUnavailable

    def _boom(cfg):
        raise EngineUnavailable("faster-whisper STT", "faster-whisper")

    monkeypatch.setattr(stt_mod, "load_from_config", _boom, raising=True)
    logs: list[str] = []

    class Host(VoiceStackMixin):
        pass

    host = Host()
    host.ui = NS(write_log=lambda msg: logs.append(msg), muted=False)
    host.session = None
    host._setup_stt({"stt_backend": "faster_whisper"})
    assert host._stt is None
    assert host._local_voice is None
    assert any("Local STT unavailable" in msg for msg in logs)
