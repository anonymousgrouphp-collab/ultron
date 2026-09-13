"""kernel/voice/stt.py — the STT engine seam (research adopt S1, report 11).

One seam, N adapters (Kill List #2): the local speech-to-text lane that gives
ULTRON ears when the Gemini Live session is absent (no key, network down,
quota). The mic, the VAD contracts and the text router already exist — this
module is only the transcription engine behind the same lazy-load discipline
as `kernel/voice/tts.py` (research report 10 A1). Nothing here raises into an
audio path — a broken STT must never take down the assistant.

The seam mirrors RealtimeSTT's `transcription_engines/base.py` (MIT, report 11
§1): a sync `transcribe()` plus an optional streaming-session contract, so the
faster-whisper adapter today and whisper.cpp/sherpa adapters later all look
identical to the caller.

Config keys (config/api_keys.json — default off, zero behavior change):
    stt_backend:       none (default) | faster_whisper
    stt_model:         tiny (default) | base | small | ... (faster-whisper sizes)
    stt_device:        cpu (default) | cuda
    stt_compute_type:  int8 (cpu default) | float16 (cuda) | ...
    stt_language:      "" (auto-detect) | en | hi | ...
    stt_beam_size:     5
    stt_download_root: <base_dir>/.ultron/models  (whisper weights live here,
                       gated behind this flag — never downloaded by default)
    stt_word_timestamps: false (S9: word metadata in SttResult.words)

The local voice loop that consumes this seam lives in `app/local_voice.py`
(armed by `stt_backend`, active only while the Live session is down).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np

_LOGGER = logging.getLogger(__name__)

_DEFAULT_DOWNLOAD_ROOT = Path(".ultron") / "models"


@dataclass(frozen=True)
class SttResult:
    """One finalized transcription. `words` follows the S9 metadata contract
    (`[{word, start, end}]`) and is empty unless word timestamps were asked."""

    text: str
    language: str | None = None
    language_probability: float = 0.0
    words: tuple[dict[str, Any], ...] = field(default_factory=tuple)


class SttEngine(Protocol):
    """Synchronous speech-to-text over 16 kHz mono PCM.

    `pcm16` is int16 (sounddevice's native dtype on the mic path); engines
    convert internally. Implementations MUST be safe to call from a worker
    thread and MUST NOT raise on empty/short audio — return an empty result
    instead (the audio path never propagates STT errors)."""

    def transcribe(
        self, pcm16: np.ndarray, sample_rate: int = 16000
    ) -> SttResult: ...

    def close(self) -> None: ...


class StreamingSttSession(Protocol):
    """Incremental decode contract (RealtimeSTT `StreamingTranscriptionSession`
    shape). Optional — engines declare support via `supports_streaming`; the
    local loop uses it for partials when available, whole-buffer otherwise."""

    def reset(self) -> None: ...

    def accept_audio(self, audio: np.ndarray, sample_rate: int = 16000) -> None: ...

    def get_result(self) -> SttResult: ...

    def finish(self) -> SttResult: ...

    def close(self) -> None: ...


def _empty_result() -> SttResult:
    return SttResult(text="")


def pcm_to_float(pcm16: np.ndarray) -> np.ndarray:
    """int16 PCM → float32 in [-1, 1] (whisper's input range)."""
    data = np.asarray(pcm16)
    if data.size == 0:
        return np.empty(0, dtype=np.float32)
    if data.dtype == np.float32:
        return data
    return data.astype(np.float32) / 32768.0


class FasterWhisperSttEngine:
    """faster-whisper adapter (report 11 §1, `faster_whisper_engine.py` is the
    114-line reference). int8 CPU by default — the desktop without a GPU is
    the deployment target (ROADMAP hardware tier N)."""

    engine_name = "faster_whisper"
    supports_streaming = False

    def __init__(
        self,
        model: str = "tiny",
        device: str = "cpu",
        compute_type: str = "int8",
        language: str = "",
        beam_size: int = 5,
        word_timestamps: bool = False,
        download_root: Path | str | None = None,
        vad_filter: bool = True,
    ) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:  # pragma: no cover - import guard
            from kernel.voice.engines import EngineUnavailable

            raise EngineUnavailable("faster-whisper STT", "faster-whisper") from exc

        root = Path(download_root) if download_root else _DEFAULT_DOWNLOAD_ROOT
        root.mkdir(parents=True, exist_ok=True)
        self.language = (language or "").strip() or None
        self.beam_size = max(1, int(beam_size))
        self.word_timestamps = bool(word_timestamps)
        self._model = WhisperModel(
            model_size_or_path=model,
            device=device,
            compute_type=compute_type,
            download_root=str(root),
        )

    def transcribe(
        self, pcm16: np.ndarray, sample_rate: int = 16000
    ) -> SttResult:
        audio = pcm_to_float(pcm16)
        if audio.size < int(0.05 * sample_rate):  # <50 ms — nothing to decode
            return _empty_result()
        if sample_rate != 16000:
            from kernel.voice.tts import resample_linear

            audio = resample_linear(audio, int(sample_rate), 16000)
        try:
            segments, info = self._model.transcribe(
                audio,
                language=self.language,
                beam_size=self.beam_size,
                vad_filter=True,
                word_timestamps=self.word_timestamps,
            )
            texts: list[str] = []
            words: list[dict[str, Any]] = []
            for segment in segments:
                text = (segment.text or "").strip()
                if text:
                    texts.append(text)
                if self.word_timestamps:
                    for word in getattr(segment, "words", None) or []:
                        words.append(
                            {
                                "word": word.word,
                                "start": word.start,
                                "end": word.end,
                            }
                        )
            return SttResult(
                text=" ".join(texts).strip(),
                language=getattr(info, "language", None),
                language_probability=float(
                    getattr(info, "language_probability", 0.0) or 0.0
                ),
                words=tuple(words),
            )
        except Exception as exc:  # engine failures never break the audio path
            _LOGGER.warning("faster-whisper transcription failed: %s", exc)
            return _empty_result()

    def close(self) -> None:
        self._model = None


def load_from_config(cfg: dict) -> SttEngine:
    """Build the engine `stt_backend` names. Raises EngineUnavailable (missing
    optional package) or ValueError (unknown backend) — the arming layer in
    app/voice_stack.py catches both and degrades to a logged no-op."""
    backend = str(cfg.get("stt_backend") or "none").strip().lower()
    if backend in ("", "none", "off"):
        raise ValueError("stt_backend is not enabled")
    if backend in ("faster_whisper", "faster-whisper", "fasterwhisper"):
        return FasterWhisperSttEngine(
            model=str(cfg.get("stt_model") or "tiny"),
            device=str(cfg.get("stt_device") or "cpu"),
            compute_type=str(cfg.get("stt_compute_type") or "int8"),
            language=str(cfg.get("stt_language") or ""),
            beam_size=int(cfg.get("stt_beam_size") or 5),
            word_timestamps=bool(cfg.get("stt_word_timestamps", False)),
            download_root=cfg.get("stt_download_root") or None,
        )
    raise ValueError(
        f"unknown stt_backend {backend!r} — expected 'faster_whisper'"
    )
