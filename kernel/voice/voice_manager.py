"""kernel/voice/voice_manager.py — on-demand TTS asset downloads (A5).

Sources (all public, no auth):
- Piper voices  : rhasspy/piper-voices on HuggingFace (deterministic URL
                  pattern identical to piper1-gpl download_voices.py).
- Kokoro assets : thewh1teagle/kokoro-onnx GitHub releases — the model export
                  is the one built for this exact package (the onnx-community
                  HF export wants a rank-2 style input kokoro_onnx does not
                  feed). Voices come either as the full voices-v1.0.bin or as
                  per-voice raw-float32 bins (0.5 MB each) merged into a
                  voices.npz that kokoro_onnx reads via np.load()[name].

Every file gets a sha256 sidecar (<file>.sha256) written after a successful
download so later integrity checks are possible (piper's own downloader only
checks file existence/size — we improve on that, report 10 §A5).

Windows note: runs on the system py -3.14 runtime, stdlib-only (urllib).
"""

from __future__ import annotations

import hashlib
import re
import shutil
import urllib.request
from pathlib import Path

PIPER_URL_FORMAT = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/"
    "{lang_family}/{lang_code}/{voice_name}/{voice_quality}/"
    "{lang_code}-{voice_name}-{voice_quality}{extension}?download=true"
)
PIPER_VOICES_JSON_URL = (
    "https://huggingface.co/rhasspy/piper-voices/resolve/main/voices.json?download=true"
)
PIPER_VOICE_PATTERN = re.compile(
    r"^(?P<lang_family>[^-]+)_(?P<lang_region>[^-]+)-(?P<voice_name>[^-]+)-(?P<voice_quality>.+)$"
)

KOKORO_BASE = "https://huggingface.co/onnx-community/Kokoro-82M-v1.0-ONNX/resolve/main"
# fp32 export — on this machine (py-3.14 ORT 1.30 CPU) it benchmarks RTF ~3.0
# vs ~5.1 (v1.0-tag int8) and ~10.6 (v1.1 int8 QDQ); DML fails on the
# ConvTranspose node. Re-benchmark if the runtime or ORT version changes.
KOKORO_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/"
    "model-files-v1.1/kokoro-v1.0.onnx"
)
KOKORO_VOICE_URL = KOKORO_BASE + "/voices/{voice}.bin"

_MIN_SIZES = {".onnx": 1024 * 1024, ".json": 10, ".bin": 100 * 1024}
_USER_AGENT = "ULTRON-voice-manager/1.0 (local assistant; research adopt A5)"


def _fetch(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(request, timeout=120) as response, open(dest, "wb") as out:
        shutil.copyfileobj(response, out)
    size = dest.stat().st_size
    floor = _MIN_SIZES.get(dest.suffix.lower(), 1)
    if size < floor:
        dest.unlink(missing_ok=True)
        raise RuntimeError(f"downloaded file too small ({size} < {floor} bytes): {url}")
    _write_sha(dest)


def _write_sha(path: Path) -> None:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_suffix(path.suffix + ".sha256").write_text(digest, encoding="utf-8")


def _sha_ok(path: Path) -> bool:
    sidecar = path.with_suffix(path.suffix + ".sha256")
    if not sidecar.exists():
        return True  # nothing to verify against
    expected = sidecar.read_text(encoding="utf-8").strip()
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    return actual == expected


def download_piper_voice(voice: str, dest_dir: Path) -> Path:
    """Download one piper voice (`en_US-lessac-medium`) → its .onnx path."""
    match = PIPER_VOICE_PATTERN.match(voice.strip())
    if not match:
        raise ValueError(f"voice '{voice}' must look like en_US-lessac-medium")
    lang_family = match.group("lang_family")
    lang_code = f"{lang_family}_{match.group('lang_region')}"
    fmt = {
        "lang_family": lang_family,
        "lang_code": lang_code,
        "voice_name": match.group("voice_name"),
        "voice_quality": match.group("voice_quality"),
    }
    model_path = dest_dir / f"{voice}.onnx"
    config_path = dest_dir / f"{voice}.onnx.json"
    for ext, target in ((".onnx", model_path), (".onnx.json", config_path)):
        if not (target.exists() and target.stat().st_size > 0 and _sha_ok(target)):
            _fetch(PIPER_URL_FORMAT.format(extension=ext, **fmt), target)
    return model_path


def piper_voices_json(dest_dir: Path) -> Path:
    """Download the piper voice catalog (voices.json) for listing."""
    out = dest_dir / "voices.json"
    if not out.exists():
        _fetch(PIPER_VOICES_JSON_URL, out)
    return out


def _ensure_kokoro_model(model_path: Path) -> Path:
    if not (model_path.exists() and model_path.stat().st_size > 0 and _sha_ok(model_path)):
        _fetch(KOKORO_MODEL_URL, model_path)
    return model_path


def _merge_voice_bins(voices_dir: Path, npz_path: Path) -> Path:
    """Merge per-voice .bin files in voices_dir into one npz kokoro_onnx reads.

    The onnx-community voice .bin files are RAW little-endian float32 tensors
    of shape (510, 256) — no numpy header — so they are read with fromfile.
    """
    import numpy as np

    bins = sorted(voices_dir.glob("*.bin"))
    if not bins:
        raise RuntimeError(f"no kokoro voice .bin files found in {voices_dir}")
    payload = {}
    for bin_path in bins:
        arr = np.fromfile(bin_path, dtype="<f4")
        expected = 510 * 256
        if arr.size != expected:
            raise RuntimeError(
                f"{bin_path.name}: expected {expected} float32 samples, got {arr.size}"
            )
        payload[bin_path.stem] = arr.reshape(510, 256)
    np.savez(npz_path, **payload)  # type: ignore[arg-type]  # npz kwargs, not bool
    _write_sha(npz_path)
    return npz_path


def download_kokoro_voice(voice: str, voices_dir: Path, npz_path: Path) -> Path:
    """Fetch one kokoro voice and rebuild voices.npz (cheap: ~0.5 MB/voice)."""
    bin_path = voices_dir / f"{voice}.bin"
    if not (bin_path.exists() and _sha_ok(bin_path)):
        _fetch(KOKORO_VOICE_URL.format(voice=voice), bin_path)
    return _merge_voice_bins(voices_dir, npz_path)


def download_kokoro(model_path: Path, voices_path: Path, voices: list[str] | None = None) -> None:
    """Ensure the kokoro int8 model + requested voices exist (downloads what's
    missing), then (re)build the voices.npz the engine loads."""
    _ensure_kokoro_model(model_path)
    voices_dir = model_path.parent
    for voice in voices or ["bm_george", "bm_lewis", "af_heart"]:
        bin_path = voices_dir / f"{voice}.bin"
        if not (bin_path.exists() and _sha_ok(bin_path)):
            _fetch(KOKORO_VOICE_URL.format(voice=voice), bin_path)
    _merge_voice_bins(voices_dir, voices_path)
