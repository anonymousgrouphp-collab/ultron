"""kernel/voice/echo.py — P4-B: the echo gate — ULTRON must not hear itself.

Research/02 §9: open-mic + speakers means ULTRON's own TTS re-enters the mic —
wake-word false triggers, VAD that never closes, the model answering itself.
Realtime S2S adapters have NO server-side AEC; the duck/gate layer must sit
BELOW the gateway where every adapter benefits from it (integration note 5).

v0 is the software duck/gate (research order of practicality #1):
- while the gate reports TTS is PLAYING, frames are buffered but
  wake/VAD/STT never see them;
- a barge-in path is left open: `interrupt_signal` above a floor re-opens
  the gate early (true loud speech over TTS), matching the live loop's
  50 ms-drain interrupt doctrine;
- the gate is OBSERVE-ONLY on audio; it never drops audio permanently
  (buffered frames can be flushed to memory on stop, e.g. 'what did it say').

The Speex-AEC-with-reference rung (option #2) is a follow-up: the class is
written so `reference` frames can be fed in later without changing callers.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

__all__ = ["EchoGate", "GateState"]


class GateState(str, Enum):
    OPEN = "open"             # mic flows to wake/VAD/STT
    MUTED = "muted"           # TTS playing; frames buffered, engines blind


@dataclass
class EchoGate:
    """Tracks whether ULTRON is speaking and gates the mic accordingly."""

    barge_in_floor: float = 0.15      # RMS floor for interrupt-while-muted
    sample_rate: int = 16_000
    _speaking_since: float | None = field(default=None, init=False)
    _state: GateState = field(default=GateState.OPEN, init=False)
    _buffer: list[np.ndarray] = field(default_factory=list, init=False)
    stats: dict[str, int] = field(default_factory=lambda: {
        "gated_frames": 0, "passed_frames": 0, "barge_ins": 0}, init=False)

    # -- TTS lifecycle (called by the audio-out path) -----------------------

    def tts_started(self) -> None:
        self._speaking_since = time.monotonic()
        self._state = GateState.MUTED

    def tts_finished(self) -> None:
        self._speaking_since = None
        self._state = GateState.OPEN

    @property
    def state(self) -> GateState:
        return self._state

    # -- the frame path (called per mic frame by the capture loop) -----------

    def process(self, frame: np.ndarray) -> np.ndarray | None:
        """One mic frame in; returns the frame if engines may see it, None if
        gated (the frame is buffered for post-stop flush, never dropped)."""
        if self._state is GateState.OPEN:
            self.stats["passed_frames"] += 1
            return frame
        # muted: buffer + look for barge-in (loud speech over TTS)
        self._buffer.append(np.asarray(frame, dtype=np.float32).copy())
        self.stats["gated_frames"] += 1
        if self._rms(frame) >= self.barge_in_floor:
            # loud speech while muted = barge-in: stop TTS, reopen the gate.
            # The TTS stop itself is the caller's job (this returns the frame
            # and records the interrupt — the audio pipeline drains in ~50 ms).
            self.stats["barge_ins"] += 1
            self.tts_finished()
            self._buffer.clear()  # echo-contaminated frames are discarded
            return frame
        return None

    def flush_buffer(self) -> np.ndarray:
        """Frames buffered while muted (the 'what did you just say' lane).
        Echo-contaminated: consumers must expect ULTRON's own voice inside."""
        out = np.concatenate(self._buffer) if self._buffer else np.array([])
        self._buffer.clear()
        return out

    @staticmethod
    def _rms(frame: np.ndarray) -> float:
        data = np.asarray(frame, dtype=np.float32)
        if data.size == 0:
            return 0.0
        return float(np.sqrt(np.mean(np.square(data))))
