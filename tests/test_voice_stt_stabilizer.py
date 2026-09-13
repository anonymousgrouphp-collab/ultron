"""S2 tests — kernel/voice/stt_stabilizer.py (ported from RealtimeSTT, MIT)."""

from __future__ import annotations

import pytest

from kernel.voice.stt_stabilizer import (
    RealtimeTextFinalObservation,
    RealtimeTextObservation,
    RealtimeTextStabilizationConfig,
    RealtimeTextStabilizer,
)


def make_obs(seq, text, end_sample, now=100.0, recording_id=1):
    return RealtimeTextObservation(
        recording_id=recording_id,
        sequence=seq,
        raw_text=text,
        audio_start_sample=0,
        audio_end_sample_exclusive=end_sample,
        sample_rate=16000,
        created_at_monotonic=now,
        completed_at_monotonic=now,
    )


def eager_config():
    """All spans zeroed / confirmations 1: characters stabilize immediately
    (punctuation keeps its stricter default gate)."""
    return RealtimeTextStabilizationConfig(
        min_char_confirmations=1,
        min_char_evidence_span_seconds=0.0,
        space_min_confirmations=1,
        space_min_evidence_span_seconds=0.0,
        space_requires_stable_right_context=False,
        punctuation_requires_stable_right_context=False,
        initial_prefix_min_confirmations=1,
        initial_prefix_min_evidence_span_seconds=0.0,
    )


def test_first_observation_stabilizes_under_eager_config():
    st = RealtimeTextStabilizer(eager_config())
    event = st.observe(make_obs(1, "hello world", end_sample=3200))
    assert event.accepted
    assert event.stable_text == "hello world"
    assert event.has_new_stable_text


def test_stable_text_extends_across_growing_observations():
    st = RealtimeTextStabilizer(eager_config())
    e1 = st.observe(make_obs(1, "hello", end_sample=1600))
    assert e1.stable_text == "hello"
    e2 = st.observe(make_obs(2, "hello world", end_sample=3200))
    assert e2.stable_text == "hello world"
    assert e2.display_text.startswith("hello world")


def test_punctuation_is_not_published_until_confirmed():
    st = RealtimeTextStabilizer(eager_config())
    event = st.observe(make_obs(1, "hello, world", end_sample=3200))
    assert event.accepted
    # The comparison projection strips punctuation; the default punctuation
    # gate (4 confirmations) keeps the comma out of the published text.
    assert event.stable_text == "hello world"
    assert "," not in event.stable_text


def test_audio_progress_gates_evidence():
    st = RealtimeTextStabilizer(eager_config())
    st.observe(make_obs(1, "hello", end_sample=1600))
    # Same audio extent twice: no new evidence, text unchanged.
    e2 = st.observe(make_obs(2, "hello", end_sample=1600))
    assert e2.stable_text == "hello"
    # New audio: the added words can stabilize.
    e3 = st.observe(make_obs(3, "hello world", end_sample=3200))
    assert e3.stable_text == "hello world"


def test_outlier_observation_is_quarantined():
    st = RealtimeTextStabilizer(eager_config())
    st.observe(make_obs(1, "hello world today", end_sample=4800))
    wild = st.observe(
        make_obs(2, "zebra quantum xylophone jumps", end_sample=6400)
    )
    assert not wild.accepted
    assert wild.ignored_reason == "outlier"
    assert wild.stable_text == "hello world today"


def test_stale_sequence_ignored():
    st = RealtimeTextStabilizer(eager_config())
    st.observe(make_obs(5, "hello", end_sample=1600))
    stale = st.observe(make_obs(5, "hello world", end_sample=3200))
    assert not stale.accepted
    assert stale.ignored_reason == "stale-sequence"


def test_finalize_reconciles_with_final_text():
    st = RealtimeTextStabilizer(eager_config())
    st.observe(make_obs(1, "hello", end_sample=1600))
    event = st.finalize(
        RealtimeTextFinalObservation(recording_id=1, final_text="hello world")
    )
    assert event.agrees_with_stable_prefix
    assert event.final_suffix_after_stable == " world"


def test_finalize_flags_prefix_mismatch():
    st = RealtimeTextStabilizer(eager_config())
    st.observe(make_obs(1, "hello world", end_sample=3200))
    event = st.finalize(
        RealtimeTextFinalObservation(recording_id=1, final_text="completely other words")
    )
    assert not event.agrees_with_stable_prefix
    assert event.mismatch_reason == "stable-prefix-mismatch"


def test_default_config_requires_multiple_confirmations():
    st = RealtimeTextStabilizer(RealtimeTextStabilizationConfig())
    event = st.observe(make_obs(1, "hello world", end_sample=3200))
    assert event.accepted
    assert event.stable_text == ""  # one observation is not evidence enough
    assert event.display_text == "hello world"  # but it still shows as preview


def test_recording_switch_resets_state():
    st = RealtimeTextStabilizer(eager_config())
    st.observe(make_obs(1, "hello", end_sample=1600, recording_id=1))
    other = st.observe(make_obs(2, "new utterance", end_sample=3200, recording_id=2))
    assert not other.accepted
    assert other.ignored_reason == "wrong-recording"
