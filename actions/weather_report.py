"""actions/weather_report.py — spoken weather with a browser fallback (§P1-A).

Primary path (§P1-A, K9-derived): Open-Meteo current conditions via
`utils.weather` — ULTRON *speaks* the temperature, sky condition, humidity and
wind for a city. Fallback path: if the weather source is unreachable (no
network, API down, unknown city), the legacy browser-open behavior runs so the
capability is never worse than before.
"""

from __future__ import annotations

import webbrowser
from urllib.parse import quote_plus

from utils.weather import WeatherUnavailable, current_weather, format_weather


def weather_action(
    parameters: dict,
    player=None,
    session_memory=None,
) -> str:
    city = parameters.get("city")
    when = (parameters.get("time") or "today").strip() if parameters.get("time") else "today"

    if not city or not isinstance(city, str) or not city.strip():
        msg = "Sir, the city is missing for the weather report."
        _log(msg, player)
        return msg

    city = city.strip()

    # ── Primary: real current conditions, spoken ─────────────────────────
    try:
        payload, display = current_weather(city)
        msg = format_weather(payload, display)
        _log(msg, player)
        if session_memory:
            try:
                session_memory.set_last_search(query=f"weather in {city}",
                                               response=msg)
            except Exception:
                pass
        return msg
    except WeatherUnavailable as exc:
        _log(f"weather source unavailable ({exc}) — falling back to browser")
    except Exception as exc:  # noqa: BLE001 — the spoken path can never crash
        _log(f"weather lookup failed ({type(exc).__name__}) — falling back to browser")

    # ── Fallback: legacy browser tab (never worse than before) ───────────
    search_query = f"weather in {city} {when}"
    url = f"https://www.google.com/search?q={quote_plus(search_query)}"

    try:
        opened = webbrowser.open(url)
        if not opened:
            raise RuntimeError("webbrowser.open returned False")
    except Exception as e:
        msg = f"Sir, I couldn't open the browser for the weather report: {e}"
        _log(msg, player)
        return msg

    msg = f"Showing the weather for {city}, {when}, sir."
    _log(msg, player)

    if session_memory:
        try:
            session_memory.set_last_search(query=search_query, response=msg)
        except Exception:
            pass

    return msg


def _log(message: str, player=None) -> None:
    print(f"[Weather] {message}")
    if player:
        try:
            player.write_log(f"ULTRON: {message}")
        except Exception:
            pass
