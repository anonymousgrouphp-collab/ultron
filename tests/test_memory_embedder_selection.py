"""tests/test_memory_embedder_selection.py — Phase A4: the `memory_embedder`
config-key seam (kernel/memory/embedders.py + the MemoryEngine constructor).

Hermetic by construction: the stdlib HashingEmbedder covers every path here.
The real BGE-M3 model is NEVER touched in CI — the missing-dep contract is
tested on the exact absence path CI has, and the real-model run is opt-in via
ULTRON_BGE_TESTS=1 (it would download/load a multi-GB model otherwise).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from kernel.memory.embedders import (
    Embedder,
    EmbedderUnavailable,
    HashingEmbedder,
    embedder_from_config,
    make_embedder,
)
from kernel.memory.engine import MemoryEngine


class _Dim64Embedder:
    """A foreign-dimension embedder stand-in (hermetic — no fastembed)."""

    @property
    def dim(self) -> int:
        return 64

    def embed(self, text: str) -> list[float]:
        return [1.0 / (self.dim ** 0.5)] * self.dim


# ------------------------------------------------- embedder_from_config ----


def test_config_default_is_hashing() -> None:
    emb = embedder_from_config({})
    assert isinstance(emb, HashingEmbedder)
    assert emb.dim == 256


def test_config_blank_value_falls_back_to_hashing() -> None:
    for blank in ("", "   ", None):
        assert isinstance(embedder_from_config({"memory_embedder": blank}),
                          HashingEmbedder)


def test_config_explicit_hashing() -> None:
    emb = embedder_from_config({"memory_embedder": "hashing"})
    assert isinstance(emb, HashingEmbedder)


def test_config_value_is_stripped() -> None:
    emb = embedder_from_config({"memory_embedder": " hashing "})
    assert isinstance(emb, HashingEmbedder)


def test_config_unknown_name_fails_loudly() -> None:
    # A typo'd value must NOT silently degrade recall — raise with the
    # valid names so the wiring layer can surface it honestly.
    with pytest.raises(ValueError, match="bge-m3"):
        embedder_from_config({"memory_embedder": "bge-m4"})


def test_config_custom_key_name() -> None:
    assert isinstance(
        embedder_from_config({"embedder": "hashing"}, key="embedder"),
        HashingEmbedder)
    with pytest.raises(ValueError):
        embedder_from_config({"embedder": "nope"}, key="embedder")


# ---------------------------------------------------- engine ctor seam -----


def test_engine_accepts_embedder_name(tmp_path: Path) -> None:
    eng = MemoryEngine(tmp_path / "m.sqlite3", embedder_name="hashing")
    try:
        eng.remember("Alice's favorite color is teal")
        hits = eng.search("favorite color")
        assert hits and "teal" in hits[0].content
    finally:
        eng.close()


def test_engine_rejects_ambiguous_embedder_args(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="either"):
        MemoryEngine(
            tmp_path / "m.sqlite3",
            embedder=HashingEmbedder(),
            embedder_name="hashing",
        )


def test_engine_unknown_name_fails_before_db_exists(tmp_path: Path) -> None:
    db = tmp_path / "never.sqlite3"
    with pytest.raises(ValueError):
        MemoryEngine(db, embedder_name="gpt-embed")
    assert not db.exists()  # resolved before any side effects


def test_engine_default_is_hashing(tmp_path: Path) -> None:
    eng = MemoryEngine(tmp_path / "m.sqlite3")
    try:
        assert eng._embedder.dim == 256  # type: ignore[attr-defined]
    finally:
        eng.close()


# ------------------------------------------------ optional-dep contract ----


def test_bge_m3_missing_dep_raises_unavailable() -> None:
    try:
        import fastembed  # noqa: F401
    except ImportError:
        pass
    else:
        pytest.skip("fastembed installed — absence path not exercisable here")
    with pytest.raises(EmbedderUnavailable) as excinfo:
        make_embedder("bge-m3")
    # User-safe error: names the pip package, never internals.
    assert "fastembed" in str(excinfo.value)
    assert excinfo.value.package == "fastembed"


def test_no_embedder_dep_imported_at_kernel_import_time() -> None:
    # The lazy-loader doctrine (P1-D/P4-B): importing the kernel module never
    # pulls the heavy optional dependency. Checked as a sys.modules delta —
    # module reload would rebind classes other modules already imported.
    already = "fastembed" in sys.modules  # e.g. the opt-in real-model test ran
    import kernel.memory.embedders as _mod  # noqa: F401

    if not already:
        assert "fastembed" not in sys.modules


def test_real_bge_m3_optin_only() -> None:
    """Real-model check — NEVER runs in CI (downloads/loads the model).
    Opt in on a developer box: ULTRON_BGE_TESTS=1 with fastembed installed."""
    import os

    if not os.environ.get("ULTRON_BGE_TESTS"):
        pytest.skip("opt-in: set ULTRON_BGE_TESTS=1 (downloads the BGE-M3 model)")
    pytest.importorskip("fastembed")
    emb: Embedder = make_embedder("bge-m3")
    vec = emb.embed("hello world")
    assert emb.dim > 0 and len(vec) == emb.dim
    norm = sum(v * v for v in vec) ** 0.5
    assert 0.5 < norm < 1.5  # cosine-space vector, roughly normalized


# ------------------------------- same-DB re-open with a foreign embedder ---


def test_same_db_reopen_with_foreign_embedder_skips_not_crashes(
    tmp_path: Path,
) -> None:
    db = tmp_path / "m.sqlite3"
    eng = MemoryEngine(db)  # hashing, dim 256
    eng.remember("The server room code is 4471")
    eng.remember("Alice prefers tea over coffee")
    eng.close()

    # Re-open the SAME DB with a different-dimension embedder.
    eng2 = MemoryEngine(db, embedder=_Dim64Embedder())
    try:
        # FTS leg untouched by the embedder swap — facts still retrievable.
        hits = eng2.search("server room code")
        assert hits and "4471" in hits[0].content
        # The 256-dim rows are foreign now: they are skipped, not mixed —
        # and a new fact under the 64-dim embedder coexists in the table.
        eng2.remember("Bob drives a blue hatchback")
        rows = eng2._conn.execute(
            "SELECT dim, COUNT(*) FROM fact_vectors GROUP BY dim"
        ).fetchall()
        dims = {int(dim): count for dim, count in rows}
        assert dims.get(256) == 2  # old vectors still on disk, unretrievable
        assert dims.get(64) == 1   # new embedder's vector stored alongside
    finally:
        eng2.close()


def test_reopen_original_embedder_recovers_vector_leg(tmp_path: Path) -> None:
    db = tmp_path / "m.sqlite3"
    eng = MemoryEngine(db)
    eng.remember("The wifi password is hunter2")
    eng.close()
    foreign = MemoryEngine(db, embedder=_Dim64Embedder())
    foreign.remember("temp marker")
    foreign.close()

    eng2 = MemoryEngine(db)  # back to hashing 256
    try:
        hits = eng2.search("wifi password")
        assert hits and "hunter2" in hits[0].content
    finally:
        eng2.close()
