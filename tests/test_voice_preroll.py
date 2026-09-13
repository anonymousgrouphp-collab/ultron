"""S5 tests — kernel/voice/preroll.py (ported from RealtimeSTT, MIT)."""

from __future__ import annotations

import pytest

from kernel.voice.preroll import (
    PrerollFrameMetadata,
    select_preroll_frames,
)


def frame(seconds, is_speech, rms, sample_rate=16000):
    return PrerollFrameMetadata(
        sample_count=int(seconds * sample_rate), is_speech=is_speech, rms=rms
    )


def test_empty_buffer_is_reported():
    selection = select_preroll_frames([], 16000)
    assert selection.start_index == 0
    assert selection.reason == "empty_buffer"


def test_speech_onset_at_buffer_start_keeps_everything():
    frames = [frame(0.1, True, 500.0) for _ in range(10)]
    selection = select_preroll_frames(frames, 16000)
    assert selection.reason == "fallback_full_preroll"
    assert selection.start_index == 0
    assert selection.included_seconds == pytest.approx(1.0)


def test_clear_silence_then_speech_trims_dead_air():
    silence = [frame(0.1, False, 2.0) for _ in range(8)]   # 0.8 s quiet
    speech = [frame(0.1, True, 500.0) for _ in range(12)]  # 1.2 s voiced
    selection = select_preroll_frames(silence + speech, 16000)
    assert selection.reason == "stable_silence_found"
    assert selection.start_index > 0
    # Guard (160 ms) + min included (600 ms) are respected: what remains must
    # end at the buffer end and cover at least the 600 ms floor.
    assert selection.start_index < len(silence)
    assert selection.included_seconds >= 0.6
    assert selection.included_seconds < 2.0


def test_no_speech_onset_is_uncertain_and_keeps_all():
    frames = [frame(0.1, None, 3.0) for _ in range(10)]
    selection = select_preroll_frames(frames, 16000)
    assert selection.reason == "uncertain"
    assert selection.start_index == 0


def test_short_buffer_below_minimum_keeps_all():
    frames = [frame(0.05, False, 2.0) for _ in range(6)]
    frames += [frame(0.05, True, 500.0) for _ in range(4)]
    selection = select_preroll_frames(frames, 16000)
    assert selection.reason == "below_minimum"
    assert selection.start_index == 0


def test_loud_non_speech_frames_cannot_create_silence():
    # VAD said speech; the energy helper must never flip that to silence.
    frames = [frame(0.1, True, 1.0) for _ in range(6)]
    frames += [frame(0.1, True, 500.0) for _ in range(6)]
    selection = select_preroll_frames(frames, 16000)
    assert selection.reason == "fallback_full_preroll"


def test_rejects_nonpositive_sample_rate():
    with pytest.raises(ValueError):
        select_preroll_frames([frame(0.1, True, 1.0)], 0)
