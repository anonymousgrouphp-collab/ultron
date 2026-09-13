"""S3 tests — kernel/voice/boundary.py (ported from RealtimeSTT, MIT)."""

from __future__ import annotations

import numpy as np
import pytest

from kernel.voice.boundary import RealtimeSpeechBoundaryDetector


BLOCK = 512  # 32 ms at 16 kHz


def _blocks(count):
    return [np.zeros(BLOCK, dtype=np.float32) for _ in range(count)]


def _tone_blocks(count, freq=160.0, amp=0.3):
    phase = 0.0
    blocks = []
    step = 2 * np.pi * freq / 16000.0
    for _ in range(count):
        samples = amp * np.sin(phase + step * np.arange(BLOCK))
        blocks.append(samples.astype(np.float32))
        phase += step * BLOCK
    return blocks


def test_silence_only_emits_no_boundaries():
    detector = RealtimeSpeechBoundaryDetector()
    events = []
    for block in _blocks(31):  # ~1 s of silence
        result = detector.process_samples(block)
        events.extend(result.events)
    assert events == []


def test_voice_then_pause_emits_boundary_event():
    detector = RealtimeSpeechBoundaryDetector()
    results = []
    stream = _blocks(10) + _tone_blocks(16) + _blocks(12)
    for block in stream:  # 0.32 s floor init, 0.5 s voiced, 0.38 s pause
        results.append(detector.process_samples(block))
    # The tone must register as voiced speech (the precondition for a valley).
    voiced = [r for r in results if r.is_speech]
    assert voiced, "tone frames never registered as speech"
    events = [event for r in results for event in r.events]
    assert events, "no boundary event after voice-to-pause transition"
    assert events[0].reason in ("vowel-ended", "vowel-to-pause")
    assert events[0].drop_db > 0
    # The boundary must sit in the voiced/tone region (a few frames of
    # lookahead may push its center just past the tone end).
    assert events[0].boundary_sample >= 10 * BLOCK - BLOCK
    assert events[0].boundary_sample <= (10 + 16 + 3) * BLOCK


def test_reset_clears_history():
    detector = RealtimeSpeechBoundaryDetector()
    for block in _tone_blocks(8):
        detector.process_samples(block)
    detector.reset()
    result = detector.process_samples(_tone_blocks(4)[0])
    # One 512-sample block = 3 complete 10 ms frames; no stale events survive.
    assert result.processed_frames == 3
    assert not result.events


def test_int16_bytes_accepted():
    detector = RealtimeSpeechBoundaryDetector()
    result = detector.process_bytes((np.zeros(BLOCK, dtype=np.int16)).tobytes())
    # 512 samples = 3 complete 10 ms frames (160 samples each) + a 32-sample tail.
    assert result.processed_frames == 3
