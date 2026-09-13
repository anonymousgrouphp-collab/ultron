"""utils/weather.py — real current-conditions weather capability (§P1-A).

K9-derived (research/09): the legacy weather action only opened a browser tab;
this module makes ULTRON able to *speak* the weather. Open-Meteo geocoding +
forecast APIs (free, no key), WMO weather-code table, intent-aware formatting,
and a thread-safe 3-minute coordinate cache (~1.1 km precision) so back-to-back
questions never re-hit the API.

One shared implementation — the spoken tool (actions/weather_report.py) wraps
this; kernel/briefing keeps its own daily-forecast section shape (DI fetch
transport, different endpoint params).

Failure contract: `current_weather` and `geocode` raise `WeatherUnavailable`
on any network/API problem — the caller decides the fallback, never a raw
exception leaking into a spoken response.
"""

from __future__ import annotations

import threading
import time
import urllib.parse
from typing import Any

import requests

__all__ = [
    "WeatherUnavailable",
    "WMO_CODES",
    "current_weather",
    "format_weather",
    "geocode",
]

_GEOCODE_URL = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
_TIMEOUT_S = 8
_CACHE_TTL_S = 180

# WMO weather interpretation codes (Open-Meteo docs table)
WMO_CODES: dict[int, str] = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snowfall", 73: "moderate snowfall", 75: "heavy snowfall",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "a thunderstorm", 96: "a thunderstorm with slight hail",
    99: "a thunderstorm with heavy hail",
}

# Deterministic ground-truth coordinates for bare large-country queries —
# prevents geocoder drift ("India" resolving somewhere odd).
_HARD_MAPPED: dict[str, tuple[float, float, str]] = {
    "india": (28.6139, 77.2090, "India"),
    "united states": (39.8283, -98.5795, "the United States"),
    "usa": (39.8283, -98.5795, "the United States"),
    "us": (39.8283, -98.5795, "the United States"),
    "united kingdom": (51.5072, -0.1276, "the United Kingdom"),
    "uk": (51.5072, -0.1276, "the United Kingdom"),
    "canada": (45.4215, -75.6972, "Canada"),
    "australia": (-33.8688, 151.2093, "Australia"),
    "germany": (52.5200, 13.4050, "Germany"),
    "france": (48.8566, 2.3522, "France"),
    "japan": (35.6762, 139.6503, "Japan"),
    "china": (39.9042, 116.4074, "China"),
    "brazil": (-15.7975, -47.8919, "Brazil"),
    "russia": (55.7558, 37.6173, "Russia"),
    "uae": (24.4539, 54.3773, "the UAE"),
    "dubai": (25.2048, 55.2708, "Dubai"),
}


class WeatherUnavailable(RuntimeError):
    """The weather source could not be reached or returned nothing usable."""


class _TTLCache:
    """Tiny thread-safe TTL cache keyed by rounded coordinates."""

    def __init__(self, ttl_s: float = _CACHE_TTL_S) -> None:
        self._ttl = ttl_s
        self._lock = threading.Lock()
        self._data: dict[tuple[float, float], tuple[float, Any]] = {}

    def get(self, key: tuple[float, float]) -> Any | None:
        with self._lock:
            hit = self._data.get(key)
            if hit is None:
                return None
            ts, value = hit
            if time.time() - ts > self._ttl:
                del self._data[key]
                return None
            return value

    def put(self, key: tuple[float, float], value: Any) -> None:
        with self._lock:
            self._data[key] = (time.time(), value)


_CACHE = _TTLCache()


def geocode(city: str) -> tuple[float, float, str]:
    """Resolve a city name to (latitude, longitude, display name)."""
    name = (city or "").strip().lower()
    if not name:
        raise WeatherUnavailable("no location given")
    hard = _HARD_MAPPED.get(name)
    if hard is not None:
        return hard[0], hard[1], hard[2]
    url = (
        f"{_GEOCODE_URL}?name={urllib.parse.quote(city.strip())}"
        "&count=10&language=en&format=json"
    )
    try:
        resp = requests.get(url, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        results = resp.json().get("results") or []
    except Exception as exc:  # noqa: BLE001 — network/JSON problems unify
        raise WeatherUnavailable(f"geocoding failed: {type(exc).__name__}") from exc
    if not results:
        raise WeatherUnavailable(f"no known location named {city.strip()!r}")
    top = results[0]
    display = top.get("name", city.strip())
    country = top.get("country", "")
    lat, lon = float(top["latitude"]), float(top["longitude"])
    return lat, lon, f"{display}, {country}" if country else display


def fetch_current(lat: float, lon: float) -> dict[str, Any]:
    """Fetch current conditions for coordinates (cached 3 min per ~1.1 km)."""
    key = (round(float(lat), 2), round(float(lon), 2))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    url = (
        f"{_FORECAST_URL}?latitude={lat}&longitude={lon}"
        "&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m"
    )
    try:
        resp = requests.get(url, timeout=_TIMEOUT_S)
        resp.raise_for_status()
        current = resp.json().get("current") or {}
    except Exception as exc:  # noqa: BLE001
        raise WeatherUnavailable(
            f"forecast fetch failed: {type(exc).__name__}") from exc
    if "temperature_2m" not in current:
        raise WeatherUnavailable("forecast returned no current block")
    payload = {
        "temperature_c": current.get("temperature_2m"),
        "humidity_pct": current.get("relative_humidity_2m"),
        "wind_kmh": current.get("wind_speed_10m"),
        "condition": WMO_CODES.get(current.get("weather_code", -1), "unknown"),
    }
    _CACHE.put(key, payload)
    return payload


def current_weather(city: str) -> tuple[dict[str, Any], str]:
    """Geocode + fetch: returns (payload, display_name). Raises
    WeatherUnavailable when either step fails."""
    lat, lon, display = geocode(city)
    return fetch_current(lat, lon), display


def format_weather(payload: dict[str, Any], display: str) -> str:
    """One spoken line: full summary, honest about missing fields."""
    temp = payload.get("temperature_c")
    cond = payload.get("condition") or "unknown"
    parts: list[str] = []
    if temp is not None:
        parts.append(f"{temp:.0f}°C")
    if cond != "unknown":
        parts.append(cond)
    summary = " and ".join(parts) if parts else "conditions unavailable"
    msg = f"It's currently {summary} in {display}"
    extras: list[str] = []
    if payload.get("humidity_pct") is not None:
        extras.append(f"humidity {payload['humidity_pct']}%")
    if payload.get("wind_kmh") is not None:
        extras.append(f"wind {payload['wind_kmh']} km/h")
    if extras:
        msg += f" with {', '.join(extras)}"
    return msg + "."
