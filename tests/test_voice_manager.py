"""tests/test_voice_manager.py — hermetic tests for TTS asset downloads.

Network is never touched: _fetch is monkeypatched to fake files.
"""

import hashlib
from pathlib import Path

import pytest

from kernel.voice import voice_manager as vm


@pytest.fixture()
def fake_fetch(monkeypatch):
    calls: list[tuple[str, Path]] = []

    def _fake(url: str, dest: Path) -> None:
        calls.append((url, dest))
        dest.parent.mkdir(parents=True, exist_ok=True)
        payload = (
            b"\x00" * (2 * 1024 * 1024)
            if dest.suffix == ".onnx"
            # kokoro voice bins are raw float32 (510×256) — merge checks size
            else b"\x00" * (510 * 256 * 4)
            if dest.suffix == ".bin"
            else b"{}"
        )
        dest.write_bytes(payload)
        vm._write_sha(dest)

    monkeypatch.setattr(vm, "_fetch", _fake)
    return calls


def test_piper_url_format_matches_piper1_gpl() -> None:
    url = vm.PIPER_URL_FORMAT.format(
        lang_family="en", lang_code="en_US", voice_name="lessac",
        voice_quality="medium", extension=".onnx",
    )
    assert url == (
        "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
        "en/en_US/lessac/medium/en_US-lessac-medium.onnx?download=true"
    )


def test_download_piper_voice_happy_path(fake_fetch, tmp_path: Path) -> None:
    model = vm.download_piper_voice("en_US-lessac-medium", tmp_path)
    assert model == tmp_path / "en_US-lessac-medium.onnx"
    assert (tmp_path / "en_US-lessac-medium.onnx.json").exists()
    # both fetches hit the deterministic HF pattern
    assert all("huggingface.co/rhasspy/piper-voices" in u for u, _ in fake_fetch)


def test_download_piper_voice_rejects_bad_name(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="en_US-lessac-medium"):
        vm.download_piper_voice("lessac", tmp_path)


def test_sha_sidecar_written_and_verified(fake_fetch, tmp_path: Path) -> None:
    model = vm.download_piper_voice("en_US-lessac-medium", tmp_path)
    sidecar = model.with_suffix(model.suffix + ".sha256")
    digest = sidecar.read_text().strip()
    assert digest == hashlib.sha256(model.read_bytes()).hexdigest()
    assert vm._sha_ok(model)
    # corrupt the file → integrity check must fail
    model.write_bytes(b"tampered")
    assert not vm._sha_ok(model)


def test_kokoro_merge_raw_float32_bins(tmp_path: Path) -> None:
    import numpy as np

    voices_dir = tmp_path / "kokoro"
    voices_dir.mkdir()
    rng = np.random.default_rng(7)
    for name in ("bm_george", "bm_lewis"):
        (voices_dir / f"{name}.bin").write_bytes(
            rng.standard_normal(510 * 256, dtype=np.float32).tobytes()
        )
    npz = tmp_path / "voices.npz"
    out = vm._merge_voice_bins(voices_dir, npz)
    loaded = np.load(out)
    assert set(loaded.keys()) == {"bm_george", "bm_lewis"}
    assert loaded["bm_george"].shape == (510, 256)
    assert loaded["bm_george"].dtype == np.float32


def test_kokoro_merge_rejects_wrong_size(tmp_path: Path) -> None:
    voices_dir = tmp_path / "kokoro"
    voices_dir.mkdir()
    (voices_dir / "bad.bin").write_bytes(b"\x00" * 100)
    with pytest.raises(RuntimeError, match="float32 samples"):
        vm._merge_voice_bins(voices_dir, tmp_path / "v.npz")


def test_download_kokoro_ensures_model_and_voices(fake_fetch, tmp_path: Path) -> None:
    model = tmp_path / "kokoro" / "model_quantized.onnx"
    npz = tmp_path / "kokoro" / "voices.npz"
    vm.download_kokoro(model, npz, voices=["bm_george"])
    assert model.exists()
    assert npz.exists()
    urls = [u for u, _ in fake_fetch]
    assert any(u.endswith("/kokoro-v1.0.onnx") for u in urls)
    assert any(u.endswith("/voices/bm_george.bin") for u in urls)
    import numpy as np

    assert "bm_george" in np.load(npz)
