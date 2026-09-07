"""kernel/memory/embedders.py — P1-D: text embedding providers.

The engine stores vectors per fact; the embedder is injectable so tests and CI
run hermetically (HashingEmbedder, stdlib) while the local tier (BGE-M3 via
fastembed, research/04 §10 / research/06 tier table) is a drop-in upgrade.
Embeddings are ALWAYS local — they are free and private (research/06 §5).
"""

from __future__ import annotations

import hashlib
import math
import re
from typing import Protocol

_TOKEN = re.compile(r"[a-z0-9]+")
_DIM = 256


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
    make_embedder("bge-m3") only, never at kernel import time.
    """

    def __init__(self) -> None:
        try:
            from fastembed import TextEmbedding
        except ImportError as exc:  # pragma: no cover - env-dependent
            raise ValueError(
                "bge-m3 embedder needs the optional 'fastembed' package "
                "(pip install fastembed); falling back to 'hashing' otherwise"
            ) from exc
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
    raise ValueError(f"unknown embedder {name!r} (expected 'hashing' or 'bge-m3')")


__all__ = ["BGEM3Embedder", "Embedder", "HashingEmbedder", "make_embedder"]
