"""app/local_voice.py — the offline voice loop (research adopt S1, report 11).

When the Gemini Live session is absent (no key, network down, quota) ULTRON
was deaf: the only STT was Live's server-side `input_transcription`. This
loop gives the local path ears, reusing the contracts the product already has
so nothing becomes a second implementation:

    mic → EchoGate (`gate_mic_frame`, same as the Live path) → Silero VAD
        (`kernel.voice.engines.load_silero_vad`) → utterance capture with
        pre-roll trim (S5, `kernel.voice.preroll`) → `SttEngine`
        (`kernel.voice.stt`, faster-whisper) → `clean_transcript` → the ONE
        text router (`_on_text_command`) → AgentLoop/commands → speak()
        (local TTS fallback when Live is down, report 10 A1).

Latency/quality mechanisms ported from RealtimeSTT (report 11 §5): partial
decodes during speech are refreshed at acoustic boundaries (S3,
`kernel.voice.boundary`) or a fallback timer, then published through the text
stabilizer (S2, `kernel.voice.stt_stabilizer`) so the HUD shows stable
captions instead of flickering partials; speech-end needs a confirmed silence
window (breaths don't cut the utterance); a hard cap bounds runaway capture.

Everything is best-effort: a decode failure logs and drops, never raises into
the audio thread. The loop runs only while the Live session is down
(`VoiceStackMixin.sync_local_voice` owns start/stop), so the mic is never
double-captured.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections import deque
from dataclasses import dataclass
from typing import Callable, Optional

import numpy as np

from kernel.voice.boundary import RealtimeSpeechBoundaryDetector
from kernel.voice.preroll import PrerollFrameMetadata, select_preroll_frames
from kernel.voice.stt import SttEngine
from kernel.voice.stt_stabilizer import (
    RealtimeTextFinalObservation,
    RealtimeTextObservation,
    RealtimeTextStabilizationConfig,
    RealtimeTextStabilizer,
)

_LOGGER = logging.getLogger(__name__)

_INT16_MAX = 32768.0


@dataclass
class LocalVoiceConfig:
    """Tuning for the offline loop; every knob has a RealtimeSTT-derived
    default. Wired to `stt_*` config keys in `from_cfg`."""

    sample_rate: int = 16000
    block_size: int = 512
    vad_threshold: float = 0.5
    preroll_seconds: float = 1.0
    post_speech_silence: float = 0.6
    silence_confirmation: float = 0.16
    min_recording_seconds: float = 0.5
    max_utterance_seconds: float = 60.0
    enable_partials: bool = True
    partial_fallback_interval: float = 1.0
    partial_min_new_seconds: float = 0.4

    @classmethod
    def from_cfg(cls, cfg: dict) -> "LocalVoiceConfig":
        return cls(
            vad_threshold=float(cfg.get("stt_vad_threshold", 0.5)),
            preroll_seconds=float(cfg.get("stt_preroll_seconds", 1.0)),
            post_speech_silence=float(cfg.get("stt_post_speech_silence", 0.6)),
            silence_confirmation=float(cfg.get("stt_silence_confirmation", 0.16)),
            min_recording_seconds=float(cfg.get("stt_min_recording_seconds", 0.5)),
            enable_partials=bool(cfg.get("stt_partials_enabled", True)),
        )


class LocalVoiceLoop:
    """VAD-gated utterance capture → STT → host text router (offline ears).

    Host contract: `gate_mic_frame(frame) -> frame | None` (EchoGate), and
    optionally `identify_speaker(audio)` (P2 tie-in) plus `ui.muted`. The
    loop never touches the Live session — it exists precisely when there is
    none.
    """

    def __init__(
        self,
        host,
        engine: SttEngine,
        vad,
        on_text: Callable[[str], None],
        on_partial: Optional[Callable[[str], None]] = None,
        config: Optional[LocalVoiceConfig] = None,
    ) -> None:
        self._host = host
        self._engine = engine
        self._vad = vad
        self._on_text = on_text
        self._on_partial = on_partial
        self.cfg = config or LocalVoiceConfig()

        self._stop_event = threading.Event()
        self._running = False
        self._queue: "queue.Queue[np.ndarray]" = queue.Queue(maxsize=256)
        self._thread: Optional[threading.Thread] = None
        self._stream = None
        # Injectable clock (tests drive a simulated timeline; production
        # uses the monotonic wall clock).
        self._clock: Callable[[], float] = time.monotonic

        self._stabilizer = RealtimeTextStabilizer(RealtimeTextStabilizationConfig())
        self._boundary = RealtimeSpeechBoundaryDetector(
            sample_rate=self.cfg.sample_rate
        )

        # Recording state (worker thread only).
        self._recording = False
        self._frames: list[np.ndarray] = []          # int16 blocks, flat
        self._recording_start = 0.0
        self._silence_start: Optional[float] = None
        self._silence_candidate: Optional[float] = None
        self._recording_id = 0
        self._sequence = 0
        self._last_partial_wall = 0.0
        self._last_boundary_wall = 0.0
        self._last_partial_sample_count = 0
        self._last_partial_display = ""
        self._partial_lock = threading.Lock()
        self._preroll: deque[tuple[np.ndarray, PrerollFrameMetadata]] = deque(
            maxlen=max(
                1,
                int(self.cfg.preroll_seconds * self.cfg.sample_rate)
                // self.cfg.block_size,
            )
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self._running

    def start(self) -> None:
        if self._running:
            return
        import sounddevice as sd  # lazy: keeps the module import hermetic

        self._stop_event.clear()
        self._running = True
        self._thread = threading.Thread(
            target=self._run_worker, name="ULTRONLocalVoice", daemon=True
        )
        self._thread.start()
        try:
            self._stream = stream = sd.InputStream(
                samplerate=self.cfg.sample_rate,
                channels=1,
                dtype="int16",
                blocksize=self.cfg.block_size,
                callback=self._on_audio,
            )
            stream.start()
        except Exception as exc:  # mic busy/absent — degrade, never crash
            self._running = False
            self._stop_event.set()
            if self._thread is not None:
                self._thread.join(timeout=2)
                self._thread = None
            _LOGGER.warning("Local voice loop could not open the mic: %s", exc)

    def stop(self) -> None:
        self._stop_event.set()
        stream, self._stream = self._stream, None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None and thread.is_alive():
            thread.join(timeout=2)
        self._running = False
        self._recording = False
        self._frames = []

    # ------------------------------------------------------------------
    # Audio callback (PortAudio thread)
    # ------------------------------------------------------------------

    def _on_audio(self, indata, frames, time_info, status) -> None:  # noqa: ANN001
        if self._stop_event.is_set():
            return
        if getattr(self._host, "ui", None) is not None and self._host.ui.muted:
            return
        gated = self._host.gate_mic_frame(indata)
        if gated is None:  # EchoGate: ULTRON is speaking — buffer, don't hear
            return
        block = np.asarray(gated).reshape(-1)
        try:
            self._queue.put_nowait(block)
        except queue.Full:
            # Drop the oldest block: latency stays bounded and the newest
            # speech (the onset of the next word) is what we must not lose.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(block)
            except (queue.Empty, queue.Full):
                pass

    # ------------------------------------------------------------------
    # Worker thread: VAD state machine
    # ------------------------------------------------------------------

    def _run_worker(self) -> None:
        while not self._stop_event.is_set():
            try:
                block = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                self._process_block(block)
            except Exception as exc:  # never kill the loop
                _LOGGER.warning("Local voice worker error: %s", exc, exc_info=True)
                self._reset_recording()

    def _process_block(self, block: np.ndarray) -> None:
        audio_f32 = block.astype(np.float32) / _INT16_MAX
        # Feed the boundary detector every block (its noise floor and voiced
        # history only stay valid when the stream is continuous); the partial
        # lane uses boundary events as its refresh hint.
        boundary_result = self._boundary.process_samples(audio_f32)
        if boundary_result.events:
            self._last_boundary_wall = self._clock()
        try:
            prob = self._vad.speech_probability(audio_f32)
        except Exception as exc:
            _LOGGER.debug("VAD failed (%s); skipping block", exc)
            return
        is_speech = prob >= self.cfg.vad_threshold
        rms = float(np.sqrt(np.mean(np.square(audio_f32)))) if audio_f32.size else 0.0

        if not self._recording:
            if is_speech:
                self._start_recording(block, is_speech=is_speech, rms=rms)
            else:
                self._preroll.append(
                    (
                        block,
                        PrerollFrameMetadata(
                            sample_count=block.size, is_speech=is_speech, rms=rms
                        ),
                    )
                )
            return

        now = self._clock()
        self._frames.append(block)
        recorded = now - self._recording_start

        if is_speech:
            self._silence_start = None
            self._silence_candidate = None
        else:
            if self._silence_candidate is None:
                self._silence_candidate = now
            # Silence only begins counting once the utterance is meaningful
            # (RealtimeSTT's min_length_of_recording semantics) — a short
            # fragment keeps capturing instead of finalizing.
            if (
                self._silence_start is None
                and recorded >= self.cfg.min_recording_seconds
                and now - self._silence_candidate >= self.cfg.silence_confirmation
            ):
                self._silence_start = self._silence_candidate
            if (
                self._silence_start is not None
                and now - self._silence_start >= self.cfg.post_speech_silence
            ):
                self._finalize()
                return

        if recorded >= self.cfg.max_utterance_seconds:
            self._finalize()
            return

        self._maybe_partial()

    def _start_recording(
        self, block: np.ndarray, is_speech: bool, rms: float
    ) -> None:
        self._recording = True
        self._recording_start = self._clock()
        self._silence_start = None
        self._silence_candidate = None
        self._recording_id += 1
        self._last_partial_display = ""
        self._last_partial_sample_count = 0
        self._last_partial_wall = self._clock()
        self._stabilizer.reset(self._recording_id)

        # S5: trim clear dead air from the pre-roll before it joins the
        # utterance; anything uncertain keeps the full pre-roll.
        buffered = list(self._preroll)
        self._preroll.clear()
        metadata = [meta for _, meta in buffered]
        metadata.append(
            PrerollFrameMetadata(
                sample_count=block.size, is_speech=is_speech, rms=rms
            )
        )
        selection = select_preroll_frames(
            metadata, self.cfg.sample_rate,
            min_included_ms=min(
                600.0, self.cfg.preroll_seconds * 1000.0 * 0.6
            ),
        )
        kept = buffered[selection.start_index:] + [(block, metadata[-1])]
        self._frames = [b for b, _ in kept]

    def _reset_recording(self) -> None:
        self._recording = False
        self._frames = []
        self._silence_start = None
        self._silence_candidate = None

    # ------------------------------------------------------------------
    # Partial lane (S2 + S3 + S4-lite): stable captions during speech
    # ------------------------------------------------------------------

    def _maybe_partial(self) -> None:
        if not self.cfg.enable_partials:
            return
        sample_count = sum(block.size for block in self._frames)
        new_samples = sample_count - self._last_partial_sample_count
        boundary_hint = self._last_boundary_wall > self._last_partial_wall
        fallback_due = (
            self._clock() - self._last_partial_wall
            >= self.cfg.partial_fallback_interval
        )
        if new_samples < self.cfg.partial_min_new_seconds * self.cfg.sample_rate:
            return
        if not (boundary_hint or fallback_due):
            return
        if self._partial_lock.locked():
            return  # previous partial decode still running — drop, don't queue

        snapshot = list(self._frames)
        recording_id = self._recording_id
        sequence = self._sequence + 1
        reason = "boundary" if boundary_hint else "timer"
        threading.Thread(
            target=self._run_partial,
            args=(recording_id, sequence, sample_count, snapshot, reason),
            name="ULTRONLocalVoicePartial",
            daemon=True,
        ).start()
        self._last_partial_wall = self._clock()

    def _run_partial(
        self,
        recording_id: int,
        sequence: int,
        sample_count: int,
        snapshot: list[np.ndarray],
        reason: str,
    ) -> None:
        if not self._partial_lock.acquire(blocking=False):
            return  # another decode is in flight — drop this one
        try:
            audio = (
                np.concatenate(snapshot) if len(snapshot) > 1 else snapshot[0]
            )
            started = time.monotonic()
            result = self._engine.transcribe(audio, self.cfg.sample_rate)
            if recording_id != self._recording_id:
                return  # utterance finalized/restarted mid-decode — stale
            now = time.monotonic()
            self._sequence = max(self._sequence, sequence)
            observation = RealtimeTextObservation(
                recording_id=recording_id,
                sequence=sequence,
                raw_text=result.text or "",
                audio_start_sample=0,
                audio_end_sample_exclusive=sample_count,
                sample_rate=self.cfg.sample_rate,
                created_at_monotonic=started,
                completed_at_monotonic=now,
                engine_name=getattr(self._engine, "engine_name", None),
                trigger_reason=reason,
                frame_count=len(snapshot),
                sample_count=sample_count,
            )
            event = self._stabilizer.observe(observation)
            if (
                event.accepted
                and event.should_publish
                and event.display_text
                and event.display_text != self._last_partial_display
            ):
                self._last_partial_display = event.display_text
                if self._on_partial is not None:
                    self._on_partial(event.display_text)
        except Exception as exc:
            _LOGGER.debug("Partial decode failed: %s", exc)
        finally:
            self._partial_lock.release()

    # ------------------------------------------------------------------
    # Finalization
    # ------------------------------------------------------------------

    def _finalize(self) -> None:
        frames = self._frames
        recording_id = self._recording_id
        self._reset_recording()
        if not frames:
            return
        audio = np.concatenate(frames) if len(frames) > 1 else frames[0]
        if audio.size < int(0.2 * self.cfg.sample_rate):  # <200 ms — noise tick
            return
        try:
            result = self._engine.transcribe(audio, self.cfg.sample_rate)
        except Exception as exc:
            _LOGGER.warning("Final decode failed: %s", exc)
            return

        from app.text import clean_transcript

        text = clean_transcript(result.text or "").strip()
        self._stabilizer.finalize(
            RealtimeTextFinalObservation(
                recording_id=recording_id, final_text=text
            )
        )
        if not text:
            return

        self._sequence += 1
        # P1×P2 tie-in: speaker ID over the finalized utterance (best-effort).
        identify = getattr(self._host, "identify_speaker", None)
        if identify is not None:
            try:
                identify(audio.astype(np.float32) / _INT16_MAX)
            except Exception:
                pass
        self._on_text(text)
