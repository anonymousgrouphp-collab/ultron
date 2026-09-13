"""tests/test_search_cascade.py — §P1-B provider cascade + factual cache.

All HTTP is monkeypatched; config/env are patched per-test. Hermetic.
"""

from __future__ import annotations

import time

import utils.search_providers as sp
from utils.search_providers import (
    FactualCache,
    ProviderError,
    SearchResult,
    cascade_search,
    normalize_snippet,
    rotate_key,
)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise ProviderError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


# ------------------------------------------------------------------ keys ----

def test_rotate_key_merges_config_then_env(monkeypatch):
    monkeypatch.setattr(sp, "rotate_key", sp.rotate_key)  # no-op guard
    monkeypatch.setattr("config.loader.load_config",
                        lambda: {"tavily_api_key": "cfg-key"})
    monkeypatch.setenv("TAVILY_API_KEYS", "env1, env2,cfg-key")
    keys = rotate_key("TAVILY_API_KEYS", "tavily_api_key")
    assert keys == ("cfg-key", "env1", "env2")  # config first, dedup, order kept


def test_rotate_key_empty_when_unconfigured(monkeypatch):
    monkeypatch.setattr("config.loader.load_config", lambda: {})
    monkeypatch.delenv("SERPER_API_KEYS", raising=False)
    assert rotate_key("SERPER_API_KEYS", "serper_api_key") == ()


# -------------------------------------------------------------- snippets ----

def test_normalize_snippet_collapses_and_caps():
    assert normalize_snippet("  a \n\t b  ") == "a b"
    long = "x" * 500
    out = normalize_snippet(long, max_length=400)
    assert len(out) == 403 and out.endswith("...")


# --------------------------------------------------------------- cascade ----

def _patch_provider(monkeypatch, fn_name, behavior):
    """Substitute one provider fetch fn inside _PROVIDERS (the cascade reads
    the tuple's captured references, so module-attr patching is not enough)
    and make exactly that provider configured. behavior = results | exception."""
    def fake(key, query, n):
        if isinstance(behavior, Exception):
            raise behavior
        return behavior
    env_var = next(env for env, _cfg, fn in sp._PROVIDERS
                   if fn.__name__ == fn_name)
    monkeypatch.setattr(
        sp, "_PROVIDERS",
        tuple((env, cfg, fake if fn.__name__ == fn_name else fn)
              for env, cfg, fn in sp._PROVIDERS))
    monkeypatch.setattr(
        sp, "rotate_key",
        lambda env, cfg: ("test-key",) if env == env_var else ())


def test_cascade_first_configured_provider_wins(monkeypatch):
    _patch_provider(monkeypatch, "_tavily",
                    [SearchResult("T", "http://t", "s")])
    results = cascade_search("test query")
    assert results and results[0].title == "T"
    assert results[0].snippet == "s"


def test_cascade_moves_past_failing_provider(monkeypatch):
    _patch_provider(monkeypatch, "_tavily", ProviderError("HTTP 429"))
    _patch_provider(monkeypatch, "_serper",
                    [SearchResult("S", "http://s", "snippet")])
    results = cascade_search("test query")
    assert results and results[0].title == "S"


def test_cascade_none_when_nothing_configured(monkeypatch):
    monkeypatch.setattr(sp, "rotate_key", lambda env, cfg: ())
    assert cascade_search("anything") is None


def test_cascade_none_when_all_fail(monkeypatch):
    for fn in ("_tavily", "_serper", "_exa", "_brave", "_serpapi"):
        _patch_provider(monkeypatch, fn, ProviderError("down"))
    assert cascade_search("anything") is None


# ----------------------------------------------------------- fact cache ----

def test_factual_cache_roundtrip_and_ttl(tmp_path, monkeypatch):
    cache = FactualCache(tmp_path / "cache.json")
    cache.put("what is rust", "A systems language.")
    assert cache.get("what is rust") == "A systems language."

    # expire by shifting the stored timestamp beyond the TTL
    cache._data["what is rust"]["timestamp"] -= 10_000_000
    assert cache.get("what is rust") is None


def test_factual_cache_news_ttl_shorter(tmp_path):
    cache = FactualCache(tmp_path / "cache.json")
    cache.put("latest news today", "headline")
    cache.put("who wrote hamlet", "shakespeare")
    assert cache._ttl_for("latest news today") == 600
    assert cache._ttl_for("who wrote hamlet") == 86400


def test_factual_cache_persists_across_instances(tmp_path):
    path = tmp_path / "cache.json"
    FactualCache(path).put("capital of france", "Paris")
    assert FactualCache(path).get("capital of france") == "Paris"


def test_factual_cache_bounded_eviction(tmp_path):
    cache = FactualCache(tmp_path / "cache.json", max_entries=5)
    for i in range(8):
        cache.put(f"query {i}", f"answer {i}")
        time.sleep(0.001)  # distinct timestamps for oldest-first eviction
    assert len(cache._data) == 5
    assert cache.get("query 0") is None      # oldest evicted
    assert cache.get("query 7") is not None  # newest kept


# ------------------------------------------------------- _cascade_query ----

def test_web_search_cache_hit_skips_network(monkeypatch):
    import actions.web_search as ws

    class FakeCache:
        def get(self, q):
            return "CACHED ANSWER"
        def put(self, q, a):
            raise AssertionError("cache hit must not re-put")

    monkeypatch.setattr(sp, "factual_cache", FakeCache())
    monkeypatch.setattr(ws, "_gemini_search",
                        lambda q: (_ for _ in ()).throw(
                            AssertionError("network reached")))
    assert ws._cascade_query("some query") == "CACHED ANSWER"


def test_web_search_gemini_failure_uses_cascade(monkeypatch):
    import actions.web_search as ws

    monkeypatch.setattr(sp, "factual_cache", FactualCache(None))
    monkeypatch.setattr(ws, "_gemini_search",
                        lambda q: (_ for _ in ()).throw(RuntimeError("no key")))
    monkeypatch.setattr(sp, "cascade_search", lambda q, max_results=6: [
        SearchResult("Provider hit", "http://p", "body text")])
    out = ws._cascade_query("unanswered query")
    assert "Provider hit" in out


def test_web_search_full_failure_falls_back_to_ddg(monkeypatch):
    import actions.web_search as ws

    monkeypatch.setattr(sp, "factual_cache", FactualCache(None))
    monkeypatch.setattr(ws, "_gemini_search",
                        lambda q: (_ for _ in ()).throw(RuntimeError("no key")))
    monkeypatch.setattr(sp, "cascade_search", lambda q, max_results=6: None)
    monkeypatch.setattr(ws, "_ddg_search", lambda q, max_results=6: [
        {"title": "DDG hit", "snippet": "snip", "url": "http://d"}])
    out = ws._cascade_query("unanswered query")
    assert "DDG hit" in out


def test_web_search_success_is_cached(monkeypatch):
    import actions.web_search as ws

    stored = {}
    class FakeCache:
        def get(self, q):
            return None
        def put(self, q, a):
            stored[q] = a

    monkeypatch.setattr(sp, "factual_cache", FakeCache())
    monkeypatch.setattr(ws, "_gemini_search", lambda q: "Gemini prose answer")
    out = ws._cascade_query("some stable fact")
    assert out == "Gemini prose answer"
    assert stored == {"some stable fact": "Gemini prose answer"}
