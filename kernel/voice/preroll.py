"""kernel/voice/preroll.py — conservative pre-roll trimming (research adopt S5).

Ported from RealtimeSTT `core/preroll.py` (MIT, © 2023 Kolja Beigel, cloned @
07df360; report 11 §5). Keeps clear dead air out of the transcribed utterance
without re-running a VAD pass: the caller records per-frame metadata (speech
flag, RMS) while audio flows, and this module selects the tail starting a
stable silence before the speech onset. Anything uncertain falls back to the
full pre-roll — a clipped first word is worse than a little extra silence.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Optional, Sequence, Tuple

REASON_BELOW_MINIMUM = "below_minimum"
REASON_EMPTY_BUFFER = "empty_buffer"
REASON_FALLBACK_FULL_PREROLL = "fallback_full_preroll"
REASON_STABLE_SILENCE_FOUND = "stable_silence_found"
REASON_UNCERTAIN = "uncertain"

DEFAULT_PREROLL_MIN_SILENCE_MS = 200.0
DEFAULT_PREROLL_GUARD_MS = 160.0
DEFAULT_PREROLL_MIN_INCLUDED_MS = 600.0
DEFAULT_PREROLL_MAX_GAP_MS = 80.0
DEFAULT_PREROLL_NOISE_FLOOR_MULTIPLIER = 2.5
DEFAULT_PREROLL_ENERGY_MARGIN_RMS = 25.0


@dataclass(frozen=True)
class PrerollFrameMetadata:
    """One frame's speech metadata as captured while audio flowed forward."""

    sample_count: int
    is_speech: Optional[bool]
    rms: Optional[float] = None
    start_sample: Optional[int] = None
    webrtc_is_speech: Optional[bool] = None
    silero_is_speech: Optional[bool] = None


@dataclass(frozen=True)
class PrerollSelection:
    """The selected pre-roll tail: ``start_index`` into the caller's frames."""

    start_index: int
    selected_frame_count: int
    included_sample_count: int
    included_seconds: float
    reason: str
    diagnostics: Dict[str, Any]


def select_preroll_frames(
    frame_metadata: Sequence[PrerollFrameMetadata],
    sample_rate: int,
    min_silence_ms: float = DEFAULT_PREROLL_MIN_SILENCE_MS,
    guard_ms: float = DEFAULT_PREROLL_GUARD_MS,
    max_gap_ms: float = DEFAULT_PREROLL_MAX_GAP_MS,
    min_included_ms: float = DEFAULT_PREROLL_MIN_INCLUDED_MS,
    energy_silence_rms: Optional[float] = None,
    noise_floor_multiplier: float = DEFAULT_PREROLL_NOISE_FLOOR_MULTIPLIER,
    energy_margin_rms: float = DEFAULT_PREROLL_ENERGY_MARGIN_RMS,
) -> PrerollSelection:
    """Selects a conservative tail from pre-recording frame metadata.

    Energy is only a supporting signal for frames already marked non-speech
    or unknown; it can never turn a VAD speech frame into silence or invent
    a speech onset by itself.
    """
    if sample_rate <= 0:
        raise ValueError("sample_rate must be positive")

    frames = list(frame_metadata or ())
    total_sample_count = _sum_samples(frames)
    if not frames or total_sample_count <= 0:
        return _empty_selection(sample_rate)

    max_gap_samples = _ms_to_samples(max_gap_ms, sample_rate)
    min_silence_samples = _ms_to_samples(min_silence_ms, sample_rate)
    guard_samples = _ms_to_samples(guard_ms, sample_rate)
    min_included_samples = _ms_to_samples(min_included_ms, sample_rate)

    base_diagnostics: Dict[str, Any] = {
        "totalSampleCount": total_sample_count,
        "frameCount": len(frames),
        "speechSampleCount": _speech_sample_count(frames, "is_speech"),
        "sileroSpeechSampleCount": _speech_sample_count(frames, "silero_is_speech"),
    }
    if total_sample_count <= min_included_samples:
        return _full_selection(
            frames, sample_rate, REASON_BELOW_MINIMUM,
            dict(base_diagnostics, fallbackDetail="buffer_not_above_minimum"),
        )

    onset_index = _find_merged_speech_onset_index(frames, max_gap_samples)
    base_diagnostics["speechOnsetIndex"] = onset_index
    if onset_index is None:
        return _full_selection(
            frames, sample_rate, REASON_UNCERTAIN,
            dict(base_diagnostics, fallbackDetail="no_speech_onset"),
        )
    if onset_index <= 0:
        return _full_selection(
            frames, sample_rate, REASON_FALLBACK_FULL_PREROLL,
            dict(base_diagnostics, fallbackDetail="onset_at_buffer_start"),
        )

    energy_threshold_rms, noise_floor_rms = _energy_threshold_rms(
        frames[:onset_index],
        energy_silence_rms=energy_silence_rms,
        noise_floor_multiplier=noise_floor_multiplier,
        energy_margin_rms=energy_margin_rms,
    )
    (
        silence_start_index,
        stable_silence_samples,
        effective_onset_index,
        pre_speech_tail_samples,
    ) = _stable_silence_before_onset(
        frames, onset_index=onset_index, energy_threshold_rms=energy_threshold_rms
    )
    diagnostics = dict(
        base_diagnostics,
        stableSilenceStartIndex=silence_start_index,
        stableSilenceSamples=stable_silence_samples,
        effectiveSpeechOnsetIndex=effective_onset_index,
        preSpeechTailSamples=pre_speech_tail_samples,
        energyThresholdRms=energy_threshold_rms,
        noiseFloorRms=noise_floor_rms,
    )
    if stable_silence_samples < min_silence_samples:
        return _full_selection(
            frames, sample_rate, REASON_UNCERTAIN,
            dict(diagnostics, fallbackDetail="stable_silence_too_short"),
        )

    latest_by_guard = effective_onset_sample(
        frames, effective_onset_index
    ) - guard_samples
    latest_by_minimum = total_sample_count - min_included_samples
    selection_start_sample = min(latest_by_guard, latest_by_minimum)
    if selection_start_sample <= 0:
        return _full_selection(
            frames, sample_rate, REASON_BELOW_MINIMUM,
            dict(diagnostics, fallbackDetail="guard_or_minimum_consumes_buffer"),
        )

    start_index = _index_for_sample_offset(frames, selection_start_sample)
    included = _sum_samples(frames[start_index:])
    return PrerollSelection(
        start_index=start_index,
        selected_frame_count=len(frames) - start_index,
        included_sample_count=included,
        included_seconds=included / float(sample_rate),
        reason=REASON_STABLE_SILENCE_FOUND,
        diagnostics=dict(diagnostics, selectionStartSample=selection_start_sample),
    )


def effective_onset_sample(frames: Sequence[PrerollFrameMetadata], index: int) -> int:
    """Cumulative sample offset of frame ``index`` within ``frames``."""
    return _sum_samples(frames[:index])


def _empty_selection(sample_rate: int) -> PrerollSelection:
    return PrerollSelection(
        start_index=0, selected_frame_count=0, included_sample_count=0,
        included_seconds=0.0, reason=REASON_EMPTY_BUFFER,
        diagnostics={"totalSampleCount": 0, "frameCount": 0},
    )


def _full_selection(
    frames: Sequence[PrerollFrameMetadata], sample_rate: int, reason: str,
    diagnostics: Dict[str, Any],
) -> PrerollSelection:
    total = _sum_samples(frames)
    return PrerollSelection(
        start_index=0, selected_frame_count=len(frames),
        included_sample_count=total,
        included_seconds=total / float(sample_rate),
        reason=reason, diagnostics=diagnostics,
    )


def _find_merged_speech_onset_index(
    frames: Sequence[PrerollFrameMetadata], max_gap_samples: int
) -> Optional[int]:
    """First frame of the last speech run, merging gaps up to ``max_gap_samples``."""
    current_start_index: Optional[int] = None
    current_gap_samples = 0
    latest_run_start_index: Optional[int] = None

    for index, frame in enumerate(frames):
        if _is_speech_frame(frame):
            if current_start_index is None or current_gap_samples > max_gap_samples:
                current_start_index = index
            current_gap_samples = 0
            latest_run_start_index = current_start_index
        elif current_start_index is not None:
            current_gap_samples += _frame_sample_count(frame)

    return latest_run_start_index


def _stable_silence_before_onset(
    frames: Sequence[PrerollFrameMetadata], onset_index: int,
    energy_threshold_rms: Optional[float],
) -> Tuple[int, int, int, int]:
    """Walks back from the onset: quiet consonant lead-ins are kept as a
    pre-speech tail, then the stable-silence run before that is measured."""
    pre_speech_tail_samples = 0
    effective_onset_index = onset_index
    index = onset_index - 1

    while index >= 0 and not _is_stable_silence_frame(frames[index], energy_threshold_rms):
        pre_speech_tail_samples += _frame_sample_count(frames[index])
        effective_onset_index = index
        index -= 1

    stable_silence_samples = 0
    silence_start_index = index + 1

    while index >= 0:
        frame = frames[index]
        if not _is_stable_silence_frame(frame, energy_threshold_rms):
            break
        stable_silence_samples += _frame_sample_count(frame)
        silence_start_index = index
        index -= 1

    return (
        silence_start_index, stable_silence_samples,
        effective_onset_index, pre_speech_tail_samples,
    )


def _is_stable_silence_frame(
    frame: PrerollFrameMetadata, energy_threshold_rms: Optional[float]
) -> bool:
    is_speech = _optional_bool(frame.is_speech)
    if is_speech is True:
        return False

    rms = frame.rms
    if rms is not None and energy_threshold_rms is not None:
        try:
            is_low_energy = float(rms) <= energy_threshold_rms
        except (TypeError, ValueError):
            is_low_energy = False
    else:
        is_low_energy = is_speech is False

    if is_speech is False:
        return is_low_energy
    return rms is not None and is_low_energy


def _is_speech_frame(frame: PrerollFrameMetadata) -> bool:
    return _optional_bool(frame.is_speech) is True


def _optional_bool(value: Optional[bool]) -> Optional[bool]:
    return None if value is None else bool(value)


def _energy_threshold_rms(
    frames: Sequence[PrerollFrameMetadata],
    energy_silence_rms: Optional[float],
    noise_floor_multiplier: float,
    energy_margin_rms: float,
) -> Tuple[Optional[float], Optional[float]]:
    """Adaptive silence threshold: lowest-20% RMS floor × multiplier + margin,
    min-ed against an optional absolute ceiling."""
    rms_values = []
    for frame in frames:
        if frame.rms is None:
            continue
        try:
            rms = float(frame.rms)
        except (TypeError, ValueError):
            continue
        if rms >= 0:
            rms_values.append(rms)

    absolute_threshold = (
        None if energy_silence_rms is None else max(0.0, float(energy_silence_rms))
    )
    if not rms_values:
        return absolute_threshold, None

    sorted_values = sorted(rms_values)
    floor_count = max(1, int(math.ceil(len(sorted_values) * 0.2)))
    noise_floor = sum(sorted_values[:floor_count]) / float(floor_count)
    adaptive_threshold = (
        noise_floor * max(0.0, float(noise_floor_multiplier))
        + max(0.0, float(energy_margin_rms))
    )
    if absolute_threshold is None:
        return adaptive_threshold, noise_floor
    return min(absolute_threshold, adaptive_threshold), noise_floor


def _index_for_sample_offset(frames: Sequence[PrerollFrameMetadata], offset: int) -> int:
    running = 0
    for index, frame in enumerate(frames):
        running += _frame_sample_count(frame)
        if running > offset:
            return index
    return len(frames)


def _sum_samples(frames: Sequence[PrerollFrameMetadata]) -> int:
    return sum(_frame_sample_count(frame) for frame in frames)


def _speech_sample_count(frames: Sequence[PrerollFrameMetadata], attr: str) -> int:
    return sum(
        _frame_sample_count(frame)
        for frame in frames
        if _optional_bool(getattr(frame, attr, None)) is True
    )


def _frame_sample_count(frame: PrerollFrameMetadata) -> int:
    return max(0, int(frame.sample_count))


def _ms_to_samples(milliseconds: float, sample_rate: int) -> int:
    return max(0, int(math.ceil(float(milliseconds) * float(sample_rate) / 1000.0)))
