"""kernel/voice/tts.py — the TTS engine seam (research adopt A1, report 10).

One seam, N adapters (Kill List #2): the ONLY audio consumer remains the live
loop's playback path (`app/audio.py::_play_audio` / audio_in_queue). Engines
are lazy-loaded like `kernel/voice/engines.py` and degrade to EngineUnavailable
naming exactly what is missing. Nothing here raises into the audio path — a
broken TTS must never take down the assistant.

Backends:
- kokoro  — kokoro-onnx (MIT) + Apache-2.0 Kokoro-82M weights, 24 kHz.
            Recommended default; espeak-ng loads via espeakng_loader wheel.
- piper   — piper-tts 1.8.0 (GPL-3.0-or-later — optional extra, local use).
            NOTE: some hardened Windows setups (WDAC) block piper's C
            espeakbridge; the engine then raises EngineUnavailable at load.

Config keys (config loader, default off — zero behavior change):
    tts_backend:      none | piper | kokoro
    tts_voice:        bm_george (kokoro) / en_US-lessac-medium (piper)
    tts_speed:        1.0          (kokoro 0.5..2.0; piper -> length_scale=1/speed)
    tts_volume:       1.0
    tts_lang:         en-us
    tts_prefer_local: false        (true = speak() locally even with Live up)
    tts_model_path / tts_voices_path: asset overrides (else voice_manager paths)
    tts_pronounce_file: .ultron/pronounce.json
"""

from __future__ import annotations

import json
import logging
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Protocol

import numpy as np

from app import text_splitter

_LOGGER = logging.getLogger(__name__)

# Where the voice_manager puts assets (mirrors voice_manager defaults).
_ULTRON_DIR = Path(".ultron")
KOKORO_DIR = _ULTRON_DIR / "voices" / "kokoro"
PIPER_DIR = _ULTRON_DIR / "voices" / "piper"
METRICS_PATH = _ULTRON_DIR / "tts_metrics.jsonl"


def to_int16(audio_float: np.ndarray, volume: float = 1.0) -> np.ndarray:
    """Float [-1, 1] -> int16 PCM with volume applied and hard clipping."""
    scaled = np.clip(audio_float * volume, -1.0, 1.0)
    return (scaled * 32767.0).astype(np.int16)


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Linear-interpolation resampler (speech quality is fine, stdlib+numpy)."""
    if src_rate == dst_rate or samples.size == 0:
        return samples
    duration = samples.shape[-1] / src_rate
    dst_len = max(1, int(round(duration * dst_rate)))
    src_idx = np.arange(samples.shape[-1], dtype=np.float64)
    dst_idx = np.linspace(0.0, samples.shape[-1] - 1, num=dst_len)
    return np.interp(dst_idx, src_idx, samples.astype(np.float64)).astype(samples.dtype)


@dataclass
class TtsChunk:
    """One sentence-sized piece of synthesized speech (native rate, int16)."""

    audio_int16: np.ndarray
    sample_rate: int
    text: str = ""


class TtsEngine(Protocol):
    def synthesize(self, text: str) -> Iterator[TtsChunk]: ...
    def close(self) -> None: ...


class _MetricsMixin:
    """Appends one JSON line per utterance to .ultron/tts_metrics.jsonl (A6)."""

    backend = "?"

    def _record_metrics(self, text: str, synth_seconds: float, audio_seconds: float) -> None:
        try:
            METRICS_PATH.parent.mkdir(parents=True, exist_ok=True)
            rtf = (synth_seconds / audio_seconds) if audio_seconds > 0 else 0.0
            line = json.dumps(
                {
                    "ts": round(time.time(), 3),
                    "backend": self.backend,
                    "chars": len(text),
                    "synth_s": round(synth_seconds, 3),
                    "audio_s": round(audio_seconds, 3),
                    "rtf": round(rtf, 3),
                }
            )
            with open(METRICS_PATH, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:  # metrics must never break speech
            pass


class Pronouncer:
    """Term -> pronunciation overrides (A4).

    File format (.ultron/pronounce.json):
        {"terms": {"ultron": {"ipa": "ˈʌl.tɹən", "say": "UL-tron"}}}
    piper  : term is replaced with a [[ipa]] raw-phoneme block
             (piper1-gpl voice.py handles [[...]] natively).
    kokoro : term is replaced with the plain-text `say` respelling.
    Missing side falls back to the term itself (no-op).
    """

    def __init__(self, path: str | Path | None = None) -> None:
        self._terms: dict[str, dict[str, str]] = {}
        if path:
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                self._terms = {k.lower(): v for k, v in data.get("terms", {}).items()}
            except FileNotFoundError:
                pass
            except (OSError, ValueError) as exc:
                _LOGGER.warning("pronounce file unreadable (%s): %s", path, exc)

    def apply(self, text: str, backend: str) -> str:
        for term, spec in self._terms.items():
            replacement = spec.get("ipa" if backend == "piper" else "say", "")
            if replacement:
                if backend == "piper":
                    replacement = f"[[{replacement}]]"
                text = _replace_word(text, term, replacement)
        return text


def _replace_word(text: str, term: str, replacement: str) -> str:
    import re

    return re.sub(rf"\b{re.escape(term)}\b", replacement, text, flags=re.IGNORECASE)


class _KokoroSessionCompat:
    """Adapts kokoro_onnx 0.4.7's inference calls to the current kokoro v1.0/v1.1
    ONNX exports (report 10 §A3 residual risk — PyPI lags upstream master).

    The v1.1 re-export declares `style [1,256]` (rank 2) and `speed [1] float`,
    while 0.4.7 feeds `style (256,)` and `speed int32`. The proxy reshapes/casts
    before the real session sees them. Everything else is delegated.
    """

    def __init__(self, session) -> None:
        self._session = session
        self._model_path = getattr(session, "_model_path", "")

    def get_inputs(self):
        return self._session.get_inputs()

    def get_outputs(self):
        return self._session.get_outputs()

    def run(self, output_names, input_feed, *args, **kwargs):
        style = input_feed.get("style")
        if style is not None and style.ndim == 1:
            input_feed["style"] = style.reshape(1, -1)
        speed = input_feed.get("speed")
        if speed is not None and speed.dtype != np.float32:
            input_feed["speed"] = speed.astype(np.float32)
        return self._session.run(output_names, input_feed, *args, **kwargs)


def _make_session(model_path: str | Path):
    """onnxruntime session with the tuned options piper.cpp benchmarks found
    (telemetry off, arena off); graph optimization left at default — the C++
    DISABLE_ALL finding predates current ORT and measurably hurt load-time
    there, so we only disable what is clearly safe."""
    import onnxruntime as ort

    opts = ort.SessionOptions()
    opts.enable_cpu_mem_arena = False
    try:
        opts.disable_telemetry_events()
    except AttributeError:  # older ORT signature
        pass
    sess = ort.InferenceSession(str(model_path), sess_options=opts)
    sess._model_path = str(model_path)  # kokoro_onnx.from_session reads this
    return _KokoroSessionCompat(sess)


class KokoroTtsEngine(_MetricsMixin):
    """kokoro-onnx backend. Sentence-level streaming: each sentence becomes a
    chunk, so playback can start before the whole text is rendered."""

    backend = "kokoro"

    def __init__(
        self,
        model_path: str | Path,
        voices_path: str | Path,
        voice: str = "bm_george",
        speed: float = 1.0,
        lang: str = "en-us",
        volume: float = 1.0,
        pronouncer: Pronouncer | None = None,
    ) -> None:
        from kokoro_onnx import Kokoro  # lazy: heavy import chain

        self._kokoro = Kokoro.from_session(_make_session(model_path), str(voices_path))
        if voice not in self._kokoro.voices:
            available = ", ".join(list(self._kokoro.voices.keys())[:8])
            raise EngineUnavailable("kokoro", f"voice '{voice}' (have: {available}…)")
        self._voice = voice
        self._speed = min(2.0, max(0.5, float(speed)))
        self._lang = lang
        self._volume = float(volume)
        self._pronouncer = pronouncer

    def sample_rate(self) -> int:
        from kokoro_onnx import SAMPLE_RATE

        return SAMPLE_RATE  # 24000

    def synthesize(self, text: str) -> Iterator[TtsChunk]:
        text = self._pronouncer.apply(text, self.backend) if self._pronouncer else text
        started = time.monotonic()
        total_audio = 0.0
        for sentence in text_splitter.split(text):
            if not sentence:
                continue
            audio, rate = self._kokoro.create(
                sentence, self._voice, speed=self._speed, lang=self._lang
            )
            total_audio += audio.shape[-1] / rate
            yield TtsChunk(
                audio_int16=to_int16(audio, self._volume), sample_rate=rate, text=sentence
            )
        self._record_metrics(text, time.monotonic() - started, total_audio)

    def close(self) -> None:  # onnx session is GC-managed
        self._kokoro = None


class PiperTtsEngine(_MetricsMixin):
    """piper-tts 1.8.0 backend (GPL extra). Uses piper's native espeak clause
    grouping (one chunk per sentence) and its SynthesisConfig knobs."""

    backend = "piper"

    def __init__(
        self,
        model_path: str | Path,
        voice: str = "",  # unused; kept for config parity
        speed: float = 1.0,
        volume: float = 1.0,
        speaker_id: int | None = None,
        pronouncer: Pronouncer | None = None,
    ) -> None:
        from piper import PiperVoice, SynthesisConfig  # lazy; may hit WDAC

        self._voice = PiperVoice.load(str(model_path))
        self._syn_config = SynthesisConfig(
            speaker_id=speaker_id,
            length_scale=max(0.1, 1.0 / max(0.1, float(speed))),
            volume=float(volume),
            normalize_audio=True,
        )
        self._pronouncer = pronouncer

    def sample_rate(self) -> int:
        return self._voice.config.sample_rate

    def synthesize(self, text: str) -> Iterator[TtsChunk]:
        text = self._pronouncer.apply(text, self.backend) if self._pronouncer else text
        started = time.monotonic()
        total_audio = 0.0
        rate = self._voice.config.sample_rate
        for chunk in self._voice.synthesize(text, syn_config=self._syn_config):
            audio = chunk.audio_int16_array
            total_audio += audio.shape[-1] / rate
            yield TtsChunk(audio_int16=audio, sample_rate=rate)
        self._record_metrics(text, time.monotonic() - started, total_audio)

    def close(self) -> None:
        self._voice = None  # type: ignore[assignment]  # release the model


class EngineUnavailable(RuntimeError):
    """Mirrors kernel.voice.engines.EngineUnavailable semantics (kept local to
    avoid importing torch-side modules into the TTS path)."""

    def __init__(self, engine: str, reason: str) -> None:
        super().__init__(f"TTS engine '{engine}' unavailable: {reason}")


def load_from_config(cfg: dict) -> TtsEngine | None:
    """Build the configured TTS engine. Returns None for 'none'/missing config;
    raises EngineUnavailable (caller logs and degrades) for broken setups."""
    backend = (cfg.get("tts_backend") or "none").strip().lower()
    if backend in ("", "none", "off"):
        return None
    speed = float(cfg.get("tts_speed", 1.0) or 1.0)
    volume = float(cfg.get("tts_volume", 1.0) or 1.0)
    lang = str(cfg.get("tts_lang", "en-us"))
    pronouncer = Pronouncer(cfg.get("tts_pronounce_file") or (_ULTRON_DIR / "pronounce.json"))

    if backend == "kokoro":
        model = cfg.get("tts_model_path") or KOKORO_DIR / "kokoro-v1.0.onnx"
        voices = cfg.get("tts_voices_path") or KOKORO_DIR / "voices.npz"
        voice = str(cfg.get("tts_voice") or "bm_george")
        if not Path(model).exists() or not Path(voices).exists():
            from kernel.voice.voice_manager import download_kokoro

            download_kokoro(model_path=Path(model), voices_path=Path(voices))
        return KokoroTtsEngine(
            model, voices, voice=voice, speed=speed, lang=lang, volume=volume,
            pronouncer=pronouncer,
        )

    if backend == "piper":
        model = cfg.get("tts_model_path") or PIPER_DIR / f"{cfg.get('tts_voice') or 'en_US-lessac-medium'}.onnx"
        if not Path(model).exists():
            from kernel.voice.voice_manager import download_piper_voice

            model = download_piper_voice(str(cfg.get("tts_voice") or "en_US-lessac-medium"), PIPER_DIR)
        try:
            return PiperTtsEngine(
                model, speed=speed, volume=volume, pronouncer=pronouncer,
                speaker_id=cfg.get("tts_speaker_id"),
            )
        except ImportError as exc:  # package missing OR WDAC DLL block
            raise EngineUnavailable("piper", str(exc)) from exc

    raise EngineUnavailable(backend, f"unknown tts_backend '{backend}' (use piper|kokoro)")


def write_wav(path: str | Path, chunks: Iterator[TtsChunk]) -> int:
    """Debug/eval helper: render a synthesis to a WAV file, return chunk count."""
    written = 0
    with wave.open(str(path), "wb") as wav:
        for chunk in chunks:
            if written == 0:
                wav.setframerate(chunk.sample_rate)
                wav.setsampwidth(2)
                wav.setnchannels(1)
            wav.writeframes(chunk.audio_int16.tobytes())
            written += 1
    return written
