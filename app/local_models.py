"""app/local_models.py — research/12 D7: Ollama VRAM arbitration.

ada_local's model-lifecycle insight (model_manager.py + model_persistence.py),
rebuilt on ULTRON's seams: one GPU serves the Ollama brain AND (optionally)
faster-whisper / bge-m3 embeddings, so idle loaded models are pure waste.
This manager:

- `running_models()`   — Ollama /api/ps
- `unload(name)`       — POST /api/generate with keep_alive=0 (immediate evict)
- `ensure_exclusive()` — evict every running model except the keep-list
- `warm(model)`        — 1-token ping with a keep_alive lease (boot pre-load)
- `run_idle_sweeper()` — async task: evict everything after `idle_s` of no use

stdlib transport only (urllib in a thread, injectable `post` for tests);
config stays OUT of the app object's construction via `from_config`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

log = logging.getLogger(__name__)

__all__ = ["OllamaModelManager"]

DEFAULT_IDLE_S = 900.0        # 15 min idle → evict
DEFAULT_KEEP_ALIVE = "30m"    # lease granted on warm


def _default_post(url: str, payload: dict, timeout_s: float) -> dict:
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=timeout_s) as resp:
        return json.loads(resp.read().decode("utf-8"))


class OllamaModelManager:
    """Load/evict lifecycle for Ollama models on a shared GPU."""

    def __init__(self, base_url: str, *, idle_s: float = DEFAULT_IDLE_S,
                 keep_alive: str = DEFAULT_KEEP_ALIVE,
                 request_timeout_s: float = 120.0,
                 post: Callable[[str, dict, float], dict] | None = None,
                 list_running: Callable[[], list[str]] | None = None,
                 clock: Callable[[], float] = time.monotonic) -> None:
        self._base_url = base_url.rstrip("/")
        self._idle_s = max(60.0, float(idle_s))
        self._keep_alive = keep_alive
        self._timeout_s = request_timeout_s
        self._post = post or _default_post
        self._list_running = list_running
        self._clock = clock
        self._last_used = 0.0        # 0 = nothing warm (idle sweeper no-ops)
        self._lock = threading.Lock()

    @classmethod
    def from_config(cls, cfg: dict) -> "OllamaModelManager | None":
        """Build from config; None when Ollama is not the configured
        provider (nothing to arbitrate — the cloud path holds no VRAM)."""
        provider = str(cfg.get("llm_provider", "gemini")).lower()
        if provider != "ollama":
            return None
        base = str(cfg.get("ollama_base_url") or "http://127.0.0.1:11434")
        idle = float(cfg.get("ollama_idle_unload_s") or DEFAULT_IDLE_S)
        return cls(base, idle_s=idle,
                   keep_alive=str(cfg.get("ollama_keep_alive") or DEFAULT_KEEP_ALIVE))

    # -- Ollama API --------------------------------------------------------

    def running_models(self) -> list[str]:
        if self._list_running is not None:      # injected (tests / dashboards)
            return list(self._list_running())
        try:
            with urllib.request.urlopen(
                    f"{self._base_url}/api/ps", timeout=3.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
            return [str(m.get("name") or "")
                    for m in data.get("models") or [] if m.get("name")]
        except (urllib.error.URLError, OSError, ValueError):
            return []

    def unload(self, model: str) -> bool:
        """Evict immediately via keep_alive=0 (ada_local's trick)."""
        try:
            self._post(f"{self._base_url}/api/generate",
                       {"model": model, "prompt": "", "keep_alive": 0},
                       self._timeout_s)
            log.info("unloaded Ollama model %s", model)
            return True
        except Exception as exc:  # noqa: BLE001 — eviction is best-effort
            log.warning("unload %s failed: %s", model, type(exc).__name__)
            return False

    def warm(self, model: str) -> bool:
        """1-token ping that forces the model into VRAM under a lease."""
        try:
            self._post(f"{self._base_url}/api/generate",
                       {"model": model, "prompt": "hi", "stream": False,
                        "keep_alive": self._keep_alive,
                        "options": {"num_predict": 1}},
                       self._timeout_s)
            self.mark_used()
            return True
        except Exception as exc:  # noqa: BLE001
            log.warning("warm %s failed: %s", model, type(exc).__name__)
            return False

    # -- arbitration -------------------------------------------------------

    def mark_used(self) -> None:
        with self._lock:
            self._last_used = self._clock()

    def ensure_exclusive(self, keep: list[str] | tuple[str, ...] = ()) -> list[str]:
        """Evict every running model not in `keep` (prefix-tolerant: Ollama
        names may carry tags/port suffixes). Returns evicted names."""
        keep_normalized = [k.lower() for k in keep]
        evicted: list[str] = []
        for name in self.running_models():
            lowered = name.lower()
            if any(k and (k in lowered or lowered in k)
                   for k in keep_normalized):
                continue
            if self.unload(name):
                evicted.append(name)
        return evicted

    async def run_idle_sweeper(self, *, poll_s: float = 30.0) -> None:
        """Evict everything when the brain has been idle past `idle_s`."""
        while True:
            await asyncio.sleep(poll_s)
            with self._lock:
                last = self._last_used
            if last == 0.0 or (self._clock() - last) < self._idle_s:
                continue
            running = await asyncio.to_thread(self.running_models)
            if not running:
                with self._lock:
                    self._last_used = 0.0
                continue
            evicted = await asyncio.to_thread(self.ensure_exclusive, [])
            if evicted:
                log.info("idle sweep evicted: %s", ", ".join(evicted))
            with self._lock:
                self._last_used = 0.0
