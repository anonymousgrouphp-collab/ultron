"""kernel/memory/embedders.py — P1-D: text embedding providers.

The engine stores vectors per fact; the embedder is injectable so tests and CI
run hermetically (HashingEmbedder, stdlib) while the local tier (BGE-M3 via
fastembed, research/04 §10 / research/06 tier table) is a drop-in upgrade.
Embeddings are ALWAYS local — they are free and private (research/06 §5).

Phase A4: the embedder is selectable through the `memory_embedder` config key
("hashing" | "bge-m3", default "hashing"). Config VALUES are passed in here —
the kernel never reads config files (P2-D gate precedent); the wiring layer
does `MemoryEngine(path, embedder_name=cfg["memory_embedder"])` or
`embedder_from_config(cfg)`.
"""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from typing import Any, Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
_DIM = 256

VALID_EMBEDDER_NAMES = ("hashing", "bge-m3")


class EmbedderUnavailable(RuntimeError):
    """An optional embedder's library is not installed. User-safe: names the
    pip package, never internals (same contract as kernel.voice.EngineUnavailable)."""

    def __init__(self, engine: str, package: str) -> None:
        super().__init__(
            f"{engine} is not installed — `pip install {package}` to enable it")
        self.engine = engine
        self.package = package


class Embedder(Protocol):
    @property
    def dim(self) -> int: ...

    def embed(self, text: str) -> list[float]: ...


class HashingEmbedder:
    """Deterministic feature-hashing embedder (stdlib only).

    Bag of word/bigram features hashed into a fixed, L2-normalized space.
    Not semantically deep, but stable, private, and good enough to rank
    keyword-adjacent facts — which is exactly what the RRF fusion needs from
    the vector leg during v0.
    """

    def __init__(self, dim: int = _DIM) -> None:
        if dim < 16:
            raise ValueError("HashingEmbedder dim must be >= 16")
        self._dim = dim

    @property
    def dim(self) -> int:
        return self._dim

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self._dim
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        for feature in features:
            digest = hashlib.sha1(feature.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dim
            sign = 1.0 if digest[4] & 1 else -1.0
            vec[bucket] += sign
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]


class BGEM3Embedder:
    """BGE-M3 embeddings via fastembed (optional dependency, local model).

    First use downloads/loads the model — constructed lazily by
    make_embedder("bge-m3") only, never at kernel import time, and never
    inside CI/tests (the real-model path is opt-in via env in tests).
    """

    def __init__(self) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise EmbedderUnavailable("BGE-M3", "fastembed") from exc
        self._model = TextEmbedding(model_name="BAAI/bge-m3")
        self._dim = 0

    @property
    def dim(self) -> int:
        if not self._dim:
            self._dim = len(next(iter(self._model.embed(["dim probe"]))))
        return self._dim

    def embed(self, text: str) -> list[float]:
        return [float(v) for v in next(iter(self._model.embed([text])))]


def make_embedder(name: str = "hashing") -> Embedder:
    """Factory: 'hashing' (stdlib default) or 'bge-m3' (needs fastembed)."""
    if name == "hashing":
        return HashingEmbedder()
    if name == "bge-m3":
        return BGEM3Embedder()
    raise ValueError(
        f"unknown embedder {name!r} (expected 'hashing' or 'bge-m3')")


def embedder_from_config(
    config: Mapping[str, Any], *, key: str = "memory_embedder"
) -> Embedder:
    """Select the embedder from a loaded config dict (Phase A4 seam).

    Absent/blank value → HashingEmbedder (the hermetic default — nothing
    breaks). Any other value goes through `make_embedder`, so a typo fails
    loudly with the valid names rather than silently degrading recall; the
    'bge-m3' path raises EmbedderUnavailable when fastembed is absent. The
    wiring layer decides whether to fall back or surface the error.
    """
    raw = config.get(key)
    if raw is None or not str(raw).strip():
        return HashingEmbedder()
    return make_embedder(str(raw).strip())


__all__ = [
    "BGEM3Embedder",
    "Embedder",
    "EmbedderUnavailable",
    "HashingEmbedder",
    "VALID_EMBEDDER_NAMES",
    "embedder_from_config",
    "make_embedder",
]
