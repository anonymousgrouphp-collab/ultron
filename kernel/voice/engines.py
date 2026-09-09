"""kernel/voice/engines.py — P4-B: wake/VAD/speaker-ID engine contracts + loaders.

Research/02 picks: openWakeWord (ships a pretrained 'hey jarvis' model),
Silero VAD v6, SpeechBrain ECAPA-TDNN. None are hard dependencies — they are
OPTIONAL ACCELERATORS in the P1-D BGE-M3 sense: the kernel defines the
contract, the loader imports lazily, and a missing library degrades to a
clean named error the app layer can surface ("install openwakeword to enable
wake word"). No model files, no downloads, no network at import time.
"""

from __future__ import annotations

import logging
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

__all__ = [
    "EngineUnavailable",
    "SpeakerIdEngine",
    "VadEngine",
    "WakeWordEngine",
    "load_openwakeword",
    "load_silero_vad",
    "load_speechbrain",
]


class EngineUnavailable(RuntimeError):
    """An optional voice engine's library is not installed. The message is
    user-safe (names the pip package, never internals)."""

    def __init__(self, engine: str, package: str) -> None:
        super().__init__(
            f"{engine} is not installed — `pip install {package}` to enable it")
        self.engine = engine
        self.package = package


class WakeWordEngine(Protocol):
    """Scores 16 kHz mono int16/float frames; returns the detected phrase or
    None. Implementation contract (openWakeWord): 80 ms frames, per-model
    threshold, prediction resets on fire."""

    def predict(self, frame: np.ndarray) -> str | None: ...


class VadEngine(Protocol):
    """Voice activity on fixed chunks; returns probability of speech."""

    def speech_probability(self, chunk: np.ndarray) -> float: ...


class SpeakerIdEngine(Protocol):
    """Enrollment + cosine identification. Enroll stores an embedding under a
    name; identify returns the best enrolled name above threshold, else None."""

    def enroll(self, name: str, audio: np.ndarray) -> None: ...

    def identify(self, audio: np.ndarray) -> str | None: ...


# ------------------------------------------------------------------ loaders

def load_openwakeword(models: tuple[str, ...] = ("hey_jarvis",),
                      threshold: float = 0.5) -> WakeWordEngine:
    """openWakeWord wrapper (research/02 §1). Raises EngineUnavailable when
    the package is missing — the wiring layer decides whether that is fatal."""
    try:
        from openwakeword.model import Model as OWWModel
    except ImportError as exc:
        raise EngineUnavailable("openWakeWord", "openwakeword") from exc

    model = OWWModel(wakeword_models=list(models), inference_framework="onnx")

    class _OWW:
        def predict(self, frame: np.ndarray) -> str | None:
            data = np.asarray(frame, dtype=np.float32)
            if data.ndim == 1:
                data = data.reshape(1, -1)
            scores = model.predict(data)
            for name, score in scores.items():
                if score >= threshold:
                    model.reset()
                    return name
            return None

    return _OWW()


def load_silero_vad(threshold: float = 0.5) -> VadEngine:
    """Silero VAD v6 wrapper (research/02 §2). onnxruntime is the backend;
    512-sample 16 kHz chunks (~32 ms) is the documented cadence."""
    try:
        from silero_vad import load_silero_vad as _load
    except ImportError as exc:
        raise EngineUnavailable("Silero VAD", "silero-vad") from exc

    vad = _load(onnx=True)

    class _Silero:
        def speech_probability(self, chunk: np.ndarray) -> float:
            data = np.asarray(chunk, dtype=np.float32).reshape(-1)
            return float(vad(torch_tensor(data)))

    def torch_tensor(data: np.ndarray):  # silero needs a torch tensor
        import torch

        return torch.from_numpy(data)

    return _Silero()


def load_speechbrain(threshold: float = 0.75) -> SpeakerIdEngine:
    """SpeechBrain ECAPA-TDNN wrapper (research/02 §8). Enrollment stores the
    MEAN embedding per name (the recipe's centroid); identification is
    cosine vs centroids. 16 kHz audio expected."""
    try:
        import torch
        from speechbrain.inference.speaker import EncoderClassifier
    except ImportError as exc:
        raise EngineUnavailable("SpeechBrain ECAPA-TDNN", "speechbrain") from exc

    encoder = EncoderClassifier.from_hparams(
        source="speechbrain/spkrec-ecapa-voxceleb")

    def _embed(audio: np.ndarray) -> np.ndarray:
        wav = torch.from_numpy(np.asarray(audio, dtype=np.float32).reshape(1, -1))
        return encoder.encode_batch(wav).squeeze().detach().numpy()

    class _ECAPA:
        def __init__(self) -> None:
            self._centroids: dict[str, np.ndarray] = {}

        def enroll(self, name: str, audio: np.ndarray) -> None:
            if not name:
                raise ValueError("speaker name is required")
            emb = _embed(audio)
            if name in self._centroids:  # re-enroll = running mean
                prev = self._centroids[name]
                self._centroids[name] = (prev + emb) / 2.0
            else:
                self._centroids[name] = emb

        def identify(self, audio: np.ndarray) -> str | None:
            if not self._centroids:
                return None
            emb = _embed(audio)
            best_name, best = None, -1.0
            for name, centroid in self._centroids.items():
                cos = float(np.dot(emb, centroid) /
                            (np.linalg.norm(emb) * np.linalg.norm(centroid) + 1e-9))
                if cos > best:
                    best_name, best = name, cos
            return best_name if best >= threshold else None

    return _ECAPA()
