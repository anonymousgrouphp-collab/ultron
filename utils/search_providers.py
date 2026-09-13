"""utils/search_providers.py — key-based search cascade + factual cache (§P1-B).

K9-derived (research/09): DDG alone is a single point of failure. This module
adds a provider cascade (Tavily → Serper → Exa → Brave → SerpAPI) and a
disk-persisted factual cache with per-query TTL. It complements — not replaces
— the existing search stack: Gemini grounded search stays primary for prose
answers, this cascade fills the gap when it fails, and DDG remains the keyless
last resort (in actions/web_search.py).

Key resolution (per provider): ``config/api_keys.json`` first (lowercase key
names like ``tavily_api_key``), then the environment (``TAVILY_API_KEYS``,
comma-separated — multiple keys rotate round-robin for free rate-limit
headroom, the K9 trick). A provider with no key anywhere is skipped.

Failure contract: provider functions raise ``ProviderError``; the cascade
swallows provider failures and moves on. Cache IO degrades silently — a broken
disk never breaks a search.
"""

from __future__ import annotations

import itertools
import json
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import requests

__all__ = [
    "ProviderError",
    "SearchResult",
    "cascade_search",
    "configured_providers",
    "factual_cache",
    "normalize_snippet",
    "rotate_key",
]

_TIMEOUT_S = 8

_NEWSISH = re.compile(r"\b(news|latest|today|now|price|current|live)\b", re.I)
_NEWS_TTL_S = 600       # 10 minutes for time-sensitive queries
_FACT_TTL_S = 86400     # 24 hours for stable facts
_CACHE_MAX_ENTRIES = 256  # bounded — the K9 factual cache grew forever


class ProviderError(RuntimeError):
    """A search provider failed (network, auth, or bad payload)."""


@dataclass(frozen=True)
class SearchResult:
    title: str
    url: str
    snippet: str


def normalize_snippet(text: str, max_length: int = 400) -> str:
    """Collapse whitespace and cap length — keeps prompts lean (K9-derived)."""
    cleaned = " ".join((text or "").split())
    if len(cleaned) > max_length:
        return cleaned[:max_length] + "..."
    return cleaned


# ------------------------------------------------------------------ keys ----

def rotate_key(env_var: str, config_key: str) -> tuple[str, ...]:
    """All usable keys for one provider: config/api_keys.json first, then the
    environment (comma-separated). Empty tuple = provider not configured."""
    keys: list[str] = []
    try:
        from config.loader import load_config
        raw = load_config().get(config_key)
        if isinstance(raw, str) and raw.strip():
            keys.append(raw.strip())
        elif isinstance(raw, list):
            keys.extend(str(k).strip() for k in raw if str(k).strip())
    except Exception:  # noqa: BLE001 — config trouble must not kill search
        pass
    env_val = os.environ.get(env_var, "")
    keys.extend(k.strip() for k in env_val.split(",") if k.strip())
    # de-dup, preserve order
    seen: set[str] = set()
    unique: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            unique.append(k)
    return tuple(unique)


class _KeyCycle:
    """Thread-safe round-robin over a key tuple (empty = exhausted)."""

    def __init__(self, keys: tuple[str, ...]) -> None:
        self._cycle = itertools.cycle(keys) if keys else None
        self._lock = threading.Lock()

    def next(self) -> str | None:
        if self._cycle is None:
            return None
        with self._lock:
            return next(self._cycle)


# ------------------------------------------------------------- providers ----

def _post_json(url: str, *, payload: dict | None = None,
               headers: dict | None = None) -> dict:
    try:
        resp = requests.post(url, json=payload, headers=headers,
                             timeout=_TIMEOUT_S)
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}")
        return resp.json()
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 — unify network/JSON failures
        raise ProviderError(type(exc).__name__) from exc


def _get_json(url: str, *, headers: dict | None = None) -> dict:
    try:
        resp = requests.get(url, headers=headers, timeout=_TIMEOUT_S)
        if resp.status_code >= 400:
            raise ProviderError(f"HTTP {resp.status_code}")
        return resp.json()
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ProviderError(type(exc).__name__) from exc


def _tavily(key: str, query: str, n: int) -> list[SearchResult]:
    data = _post_json("https://api.tavily.com/search", payload={
        "api_key": key, "query": query, "search_depth": "basic",
        "include_answer": False, "max_results": n,
    })
    return [SearchResult(r.get("title", "No Title"), r.get("url", ""),
                         normalize_snippet(r.get("content", "")))
            for r in data.get("results", [])[:n]]


def _serper(key: str, query: str, n: int) -> list[SearchResult]:
    data = _post_json("https://google.serper.dev/search",
                      payload={"q": query, "num": n},
                      headers={"X-API-KEY": key,
                               "Content-Type": "application/json"})
    return [SearchResult(r.get("title", "No Title"), r.get("link", ""),
                         normalize_snippet(r.get("snippet", "")))
            for r in data.get("organic", [])[:n]]


def _exa(key: str, query: str, n: int) -> list[SearchResult]:
    data = _post_json("https://api.exa.ai/search",
                      payload={"query": query, "numResults": n},
                      headers={"x-api-key": key,
                               "Content-Type": "application/json"})
    return [SearchResult(r.get("title", "No Title"), r.get("url", ""),
                         normalize_snippet(r.get("text", "")))
            for r in data.get("results", [])[:n]]


def _brave(key: str, query: str, n: int) -> list[SearchResult]:
    import urllib.parse
    url = (f"https://api.search.brave.com/res/v1/web/search?"
           f"q={urllib.parse.quote_plus(query)}&count={n}")
    data = _get_json(url, headers={"X-Subscription-Token": key,
                                   "Accept": "application/json"})
    return [SearchResult(r.get("title", "No Title"), r.get("url", ""),
                         normalize_snippet(r.get("description", "")))
            for r in (data.get("web", {}) or {}).get("results", [])[:n]]


def _serpapi(key: str, query: str, n: int) -> list[SearchResult]:
    import urllib.parse
    url = (f"https://serpapi.com/search.json?"
           f"q={urllib.parse.quote_plus(query)}&api_key={key}&num={n}")
    data = _get_json(url)
    return [SearchResult(r.get("title", "No Title"), r.get("link", ""),
                         normalize_snippet(r.get("snippet", "")))
            for r in data.get("organic_results", [])[:n]]


# (env var, config key, fetch fn) — cascade order, first success wins
_PROVIDERS: tuple[tuple[str, str, object], ...] = (
    ("TAVILY_API_KEYS", "tavily_api_key", _tavily),
    ("SERPER_API_KEYS", "serper_api_key", _serper),
    ("EXA_API_KEYS", "exa_api_key", _exa),
    ("BRAVE_API_KEYS", "brave_api_key", _brave),
    ("SERPAPI_API_KEYS", "serpapi_api_key", _serpapi),
)


def configured_providers() -> list[str]:
    """Names of providers that have at least one usable key (diagnostics)."""
    names = []
    for env_var, config_key, _fn in _PROVIDERS:
        if rotate_key(env_var, config_key):
            names.append(env_var.removesuffix("_API_KEYS").lower())
    return names


def cascade_search(query: str, max_results: int = 6) -> list[SearchResult] | None:
    """Run the key-based provider cascade; first provider with results wins.
    Returns None when no provider is configured or every one failed — the
    caller then falls back (e.g. to keyless DDG)."""
    for env_var, config_key, fn in _PROVIDERS:
        keys = rotate_key(env_var, config_key)
        if not keys:
            continue
        cycle = _KeyCycle(keys)
        # try at most 2 keys of this provider before moving on
        for _ in range(min(2, len(keys))):
            key = cycle.next()
            try:
                results = fn(key, query, max_results)  # type: ignore[operator]
            except ProviderError:
                continue
            if results:
                return results
    return None


# --------------------------------------------------------- factual cache ----

class FactualCache:
    """TTL cache for search answers, persisted as JSON (silent IO degradation).

    TTL: 10 min for news/price-style queries, 24 h for stable facts. Bounded —
    oldest entries are evicted past _CACHE_MAX_ENTRIES."""

    def __init__(self, path: Path | None = None,
                 max_entries: int = _CACHE_MAX_ENTRIES) -> None:
        self._path = path
        self._max = max_entries
        self._lock = threading.Lock()
        self._data: dict[str, dict] = {}
        self._load()

    def _load(self) -> None:
        if self._path is None:
            return
        try:
            if self._path.exists():
                raw = json.loads(self._path.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._data = raw
        except Exception:  # noqa: BLE001 — corrupt cache = cold cache
            self._data = {}

    def _persist(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(
                json.dumps(self._data, indent=2, ensure_ascii=False),
                encoding="utf-8")
        except Exception:  # noqa: BLE001 — a dead disk never breaks a search
            pass

    @staticmethod
    def _norm(query: str) -> str:
        return re.sub(r"\s+", " ", (query or "").lower()).strip()

    @staticmethod
    def _ttl_for(query: str) -> float:
        return _NEWS_TTL_S if _NEWSISH.search(query or "") else _FACT_TTL_S

    def get(self, query: str) -> str | None:
        norm = self._norm(query)
        if not norm:
            return None
        with self._lock:
            hit = self._data.get(norm)
            if not hit:
                return None
            if time.time() - float(hit.get("timestamp", 0)) > self._ttl_for(norm):
                del self._data[norm]
                return None
            return str(hit.get("answer", ""))

    def put(self, query: str, answer: str) -> None:
        norm = self._norm(query)
        if not norm or not answer:
            return
        with self._lock:
            self._data[norm] = {"answer": answer, "timestamp": time.time()}
            while len(self._data) > self._max:
                oldest = min(self._data,
                             key=lambda k: self._data[k].get("timestamp", 0))
                del self._data[oldest]
            self._persist()


def _default_cache_path() -> Path | None:
    try:
        from config.loader import get_base_dir
        return get_base_dir() / "memory" / "factual_cache.json"
    except Exception:  # noqa: BLE001 — no base dir → memory-only cache
        return None


factual_cache = FactualCache(_default_cache_path())
