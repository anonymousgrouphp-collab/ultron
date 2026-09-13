"""tests/test_weather_tool.py — §P1-A spoken weather (Open-Meteo + fallback).

All network access is monkeypatched with canned responses — hermetic, no keys.
"""

from __future__ import annotations

import pytest

import utils.weather as weather_mod
from utils.weather import (
    WeatherUnavailable,
    WMO_CODES,
    current_weather,
    fetch_current,
    format_weather,
    geocode,
)


class _FakeResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


def _patch_get(monkeypatch, responses):
    """Patch utils.weather.requests.get to pop canned responses in order."""
    calls = []

    def fake_get(url, timeout=None):
        calls.append(url)
        item = responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return _FakeResponse(item)

    monkeypatch.setattr(weather_mod.requests, "get", fake_get)
    return calls


# ------------------------------------------------------------------ geocode

def test_geocode_hard_mapped_country(monkeypatch):
    calls = _patch_get(monkeypatch, [])  # no network calls allowed
    lat, lon, display = geocode("India")
    assert (round(lat, 2), round(lon, 2)) == (28.61, 77.21)
    assert display == "India"
    assert calls == []


def test_geocode_city(monkeypatch):
    _patch_get(monkeypatch, [{
        "results": [{"name": "Tokyo", "country": "Japan",
                     "latitude": 35.68, "longitude": 139.65}],
    }])
    lat, lon, display = geocode("Tokyo")
    assert (lat, lon) == (35.68, 139.65)
    assert display == "Tokyo, Japan"


def test_geocode_unknown_city_raises(monkeypatch):
    _patch_get(monkeypatch, [{"results": []}])
    with pytest.raises(WeatherUnavailable):
        geocode("Nowhereland")


# ------------------------------------------------------------------ fetch

def test_fetch_current_maps_fields(monkeypatch):
    monkeypatch.setattr(weather_mod, "_CACHE", weather_mod._TTLCache())
    _patch_get(monkeypatch, [{
        "current": {"temperature_2m": 28.4, "relative_humidity_2m": 61,
                    "weather_code": 2, "wind_speed_10m": 12.3},
    }])
    payload = fetch_current(28.61, 77.21)
    assert payload["temperature_c"] == 28.4
    assert payload["condition"] == "partly cloudy"


def test_fetch_current_cache_hit(monkeypatch):
    monkeypatch.setattr(weather_mod, "_CACHE", weather_mod._TTLCache())
    calls = _patch_get(monkeypatch, [{
        "current": {"temperature_2m": 20.0, "relative_humidity_2m": 50,
                    "weather_code": 0, "wind_speed_10m": 5.0},
    }])
    fetch_current(10.0, 20.0)
    fetch_current(10.0, 20.0)  # second call must be a cache hit
    assert len(calls) == 1


def test_current_weather_end_to_end(monkeypatch):
    monkeypatch.setattr(weather_mod, "_CACHE", weather_mod._TTLCache())
    _patch_get(monkeypatch, [
        {"results": [{"name": "Delhi", "country": "India",
                      "latitude": 28.61, "longitude": 77.21}]},
        {"current": {"temperature_2m": 31.0, "relative_humidity_2m": 55,
                     "weather_code": 95, "wind_speed_10m": 9.0}},
    ])
    payload, display = current_weather("Delhi")
    assert display == "Delhi, India"
    assert payload["condition"] == "a thunderstorm"


# ------------------------------------------------------------------ format

def test_format_weather_full_summary():
    msg = format_weather(
        {"temperature_c": 28.4, "humidity_pct": 61, "wind_kmh": 12.3,
         "condition": "partly cloudy"},
        "Delhi, India",
    )
    assert "28°C" in msg
    assert "partly cloudy" in msg
    assert "Delhi, India" in msg
    assert "humidity 61%" in msg
    assert "wind 12.3 km/h" in msg


def test_format_weather_missing_fields_still_speaks():
    msg = format_weather({"temperature_c": None, "condition": "unknown"},
                         "Somewhere")
    assert "conditions unavailable" in msg
    assert "Somewhere" in msg


def test_wmo_table_covers_common_codes():
    for code in (0, 2, 3, 61, 80, 95):
        assert code in WMO_CODES


# ------------------------------------------------------------------ action

def test_weather_action_spoken_path(monkeypatch):
    import actions.weather_report as action_mod

    monkeypatch.setattr(
        action_mod, "current_weather",
        lambda city: ({"temperature_c": 24.0, "humidity_pct": 40,
                       "wind_kmh": 6.0, "condition": "clear sky"},
                      "Paris, France"))
    msg = action_mod.weather_action({"city": "Paris"})
    assert "24°C" in msg
    assert "Paris, France" in msg


def test_weather_action_falls_back_to_browser(monkeypatch):
    import actions.weather_report as action_mod

    def dead(city):
        raise WeatherUnavailable("network down")

    monkeypatch.setattr(action_mod, "current_weather", dead)
    opened = {}
    monkeypatch.setattr(action_mod.webbrowser, "open",
                        lambda url: opened.setdefault("url", url) and True)
    msg = action_mod.weather_action({"city": "Delhi"})
    assert "Showing the weather for Delhi" in msg
    assert "google.com" in opened["url"]


def test_weather_action_missing_city():
    import actions.weather_report as action_mod

    assert "city is missing" in action_mod.weather_action({})
