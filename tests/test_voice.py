"""tests/test_voice.py — P4-B: voice front-end contracts (J-01/J-02).

Hermetic: the optional engines (openwakeword/silero-vad/speechbrain) are NOT
installed on this box and NOT installed in CI — the tests pin the CONTRACTS:
- loaders raise the clean EngineUnavailable (named package, no internals)
  when a library is missing, and never import/download anything at module
  import time;
- protocol conformance: any object with the right methods satisfies the
  engine Protocols (a fake wake word + fake VAD drive a fake session loop);
- the echo gate: while TTS plays, frames are gated (engines never see them),
  buffered (not dropped), loud speech re-opens the gate (barge-in), and the
  open path passes frames untouched with counters.
"""

from __future__ import annotations

import numpy as np
import pytest

import kernel.voice as kv
from kernel.voice import (
    EchoGate,
    EngineUnavailable,
    GateState,
    load_openwakeword,
    load_silero_vad,
    load_speechbrain,
)


class TestOptionalEngineLoaders:
    def test_openwakeword_missing_is_a_clean_named_error(self) -> None:
        with pytest.raises(EngineUnavailable) as exc:
            load_openwakeword()
        assert "openwakeword" in str(exc.value)  # names the package
        assert "pip install" in str(exc.value)
        # and never leaks internals:
        assert "Traceback" not in str(exc.value)

    def test_silero_missing_is_a_clean_named_error(self) -> None:
        with pytest.raises(EngineUnavailable) as exc:
            load_silero_vad()
        assert "silero-vad" in str(exc.value)

    def test_speechbrain_missing_is_a_clean_named_error(self) -> None:
        with pytest.raises(EngineUnavailable) as exc:
            load_speechbrain()
        assert "speechbrain" in str(exc.value)

    def test_importing_kernel_voice_never_imports_the_heavy_libs(self) -> None:
        import sys

        heavy = [m for m in sys.modules
                  if m.split(".")[0] in ("openwakeword", "torch", "speechbrain")]
        assert heavy == []  # module import stays lazy


# ------------------------------------------------------- contract conformance

class FakeWakeWord:
    """Contract-shaped wake word: fires once per 'keyword frame' marker."""

    def __init__(self) -> None:
        self.fired: list[str] = []

    def predict(self, frame: np.ndarray) -> str | None:
        if frame.size and frame[0] == 999.0:
            self.fired.append("hey_jarvis")
            return "hey_jarvis"
        return None


class FakeVad:
    def speech_probability(self, chunk: np.ndarray) -> float:
        return 0.9 if frame_loud(chunk) else 0.05


class FakeSpeakerId:
    def __init__(self) -> None:
        self.voices: dict[str, np.ndarray] = {}

    def enroll(self, name: str, audio: np.ndarray) -> None:
        self.voices[name] = audio

    def identify(self, audio: np.ndarray) -> str | None:
        for name, ref in self.voices.items():
            if ref.shape == audio.shape and np.allclose(ref, audio):
                return name
        return None


def frame_loud(frame: np.ndarray) -> bool:
    return float(np.sqrt(np.mean(np.square(frame)))) > 0.1


class TestProtocols:
    def test_fake_wake_word_satisfies_the_protocol(self) -> None:
        ww: kv.WakeWordEngine = FakeWakeWord()
        assert ww.predict(np.zeros(1280)) is None
        hit = np.zeros(1280)
        hit[0] = 999.0
        assert ww.predict(hit) == "hey_jarvis"

    def test_fake_vad_satisfies_the_protocol(self) -> None:
        vad: kv.VadEngine = FakeVad()
        assert vad.speech_probability(np.zeros(512, dtype=np.float32)) < 0.5
        assert vad.speech_probability(np.ones(512, dtype=np.float32)) > 0.5

    def test_fake_speaker_id_enroll_and_identify(self) -> None:
        sid: kv.SpeakerIdEngine = FakeSpeakerId()
        tony = np.ones(16000, dtype=np.float32)
        sid.enroll("tony", tony)
        assert sid.identify(tony.copy()) == "tony"
        assert sid.identify(np.zeros(16000, dtype=np.float32)) is None


class TestSessionLoop:
    """Wake → VAD → identify: the J-01/J-02 front-end shape, fakes wired."""

    def test_wake_then_speech_then_identity(self) -> None:
        ww = FakeWakeWord()
        vad = FakeVad()
        sid = FakeSpeakerId()
        sid.enroll("tony", np.ones(16000, dtype=np.float32))

        wake_frame = np.zeros(1280)
        wake_frame[0] = 999.0
        silence = np.zeros(1280, dtype=np.float32)
        speech = np.full(1280, 0.5, dtype=np.float32)

        assert ww.predict(silence) is None
        assert ww.predict(wake_frame) == "hey_jarvis"   # wake opens a session
        assert vad.speech_probability(silence) < 0.5      # not yet talking
        assert vad.speech_probability(speech) > 0.5       # speech
        assert sid.identify(np.ones(16000, dtype=np.float32)) == "tony"


# ------------------------------------------------------------------- echo gate

class TestEchoGate:
    def test_open_gate_passes_frames(self) -> None:
        gate = EchoGate()
        frame = np.zeros(512, dtype=np.float32)
        assert gate.process(frame) is not None
        assert gate.stats["passed_frames"] == 1
        assert gate.stats["gated_frames"] == 0

    def test_tts_playing_gates_and_buffers(self) -> None:
        gate = EchoGate()
        gate.tts_started()
        assert gate.state is GateState.MUTED
        quiet = np.zeros(512, dtype=np.float32) * 0.01
        assert gate.process(quiet) is None          # engines stay blind
        assert gate.stats["gated_frames"] == 1
        buffered = gate.flush_buffer()
        assert buffered.size == 512                 # buffered, not dropped

    def test_loud_speech_while_muted_reopens_the_gate(self) -> None:
        gate = EchoGate()
        gate.tts_started()
        loud = np.full(512, 0.9, dtype=np.float32)   # barge-in
        assert gate.process(loud) is not None         # the interrupt passes
        assert gate.state is GateState.OPEN
        assert gate.stats["barge_ins"] == 1
        # echo-contaminated buffer is discarded on interrupt
        assert gate.flush_buffer().size == 0

    def test_tts_finished_reopens(self) -> None:
        gate = EchoGate()
        gate.tts_started()
        gate.tts_finished()
        assert gate.state is GateState.OPEN

    def test_quiet_speech_below_floor_stays_gated(self) -> None:
        gate = EchoGate(barge_in_floor=0.15)
        gate.tts_started()
        soft = np.full(512, 0.05, dtype=np.float32)  # under the floor
        assert gate.process(soft) is None
        assert gate.stats["barge_ins"] == 0

    def test_empty_flush_is_empty_array(self) -> None:
        gate = EchoGate()
        assert gate.flush_buffer().size == 0
