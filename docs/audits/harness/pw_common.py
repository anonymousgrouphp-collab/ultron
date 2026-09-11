"""pUI-audit Playwright harness: capture utils + auth helpers.

Every scenario script imports from here. Capture discipline:
- console messages (type/text/location)          -> console.log
- failed requests + response statuses            -> network.log
- WS frames sent/received                        -> ws_frames.log
- screenshots per step                           -> <evdir>/*.png
Writes everything under docs/audits/evidence/<scenario>/.
"""

from __future__ import annotations

import json
import urllib.request
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO = Path(__file__).resolve().parents[3]
EV = REPO / "docs" / "audits" / "evidence"
HELPER = "http://127.0.0.1:39153"
BASE = "https://127.0.0.1:8000"   # TLS is active when config/certs exist
BASE_HTTP = "http://127.0.0.1:8000"

DESKTOP = {"width": 1280, "height": 800}
PHONE = {"width": 390, "height": 844}


def helper_get(path: str, timeout: float = 10.0) -> dict:
    with urllib.request.urlopen(f"{HELPER}{path}", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def helper_post(path: str, timeout: float = 10.0) -> dict:
    req = urllib.request.Request(f"{HELPER}{path}", method="POST", data=b"")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def mint() -> dict:
    """Mint a one-time key + token bundle the way the Qt button does."""
    return helper_get("/mint")


def evdir(scenario: str) -> Path:
    d = EV / scenario
    d.mkdir(parents=True, exist_ok=True)
    return d


def write_log(d: Path, name: str, lines: list) -> Path:
    p = d / name
    p.write_text("\n".join(str(x) for x in lines) + "\n", encoding="utf-8")
    return p


def shot(page, d: Path, name: str) -> str:
    path = d / f"{name}.png"
    page.screenshot(path=str(path))
    return str(path)


class Captured:
    """Attach console/network/ws capture to a page; dump on demand."""

    def __init__(self, page):
        self.console: list[str] = []
        self.responses: list[str] = []
        self.failed: list[str] = []
        self.ws_frames: list[str] = []
        self.ws_pages: list[str] = []
        page.on("console", lambda m: self.console.append(f"{m.type}: {m.text}"))
        page.on("pageerror", lambda e: self.console.append(f"PAGEERROR: {e}"))
        page.on("response", lambda r: self.responses.append(
            f"{r.status} {r.request.method} {r.url}"))
        page.on("requestfailed", lambda r: self.failed.append(
            f"{r.method} {r.url} failure={r.failure}"))
        page.on("websocket", self._on_ws)
        self._page = page

    def _on_ws(self, ws):
        url = ws.url if isinstance(ws.url, str) else ws.url()
        self.ws_pages.append(f"{url}  (opened)")
        ws.on("framereceived", lambda f: self.ws_frames.append(
            f"RECV {self._cut(f)}"))
        ws.on("framesent", lambda f: self.ws_frames.append(
            f"SENT {self._cut(f)}"))
        ws.on("close", lambda _w: self.ws_pages.append(f"{url}  (closed)"))

    @staticmethod
    def _cut(frame) -> str:
        try:
            payload = frame if isinstance(frame, str) else bytes(frame).decode(
                "utf-8", "replace")
            if len(payload) > 900:
                payload = payload[:900] + f"...<{len(payload)}B>"
            return payload
        except Exception:
            return "<binary frame>"

    def dump(self, d: Path) -> None:
        write_log(d, "console.log", self.console or ["<none>"])
        write_log(d, "network.log",
                  [">>> RESPONSES"] + self.responses +
                  [">>> FAILED REQUESTS"] + (self.failed or ["<none>"]))
        write_log(d, "ws_frames.log", self.ws_frames or ["<no frames>"])
        write_log(d, "ws_pages.log", self.ws_pages or ["<no websockets>"])

    def console_errors(self) -> list[str]:
        return [c for c in self.console if c.startswith(("error:", "PAGEERROR"))]


def open_authed(browser, scenario: str, viewport=None, label="view",
                **ctx_kw):
    """Fresh context -> auto-login (one-time key) -> app.html rendered.

    Returns (ctx, page, cap, dir). Waits out the boot overlay."""
    d = evdir(scenario)
    url = mint()["auto_login"]
    ctx = browser.new_context(viewport=viewport or DESKTOP, **ctx_kw)
    page = ctx.new_page()
    cap = Captured(page)
    page.goto(url, wait_until="domcontentloaded")
    # NB: do NOT wait for "load" — the three.js CDN import stalls it on slow
    # links (audit finding). The DOM app is interactive well before it.
    page.wait_for_url(f"{BASE}/", timeout=15000, wait_until="domcontentloaded")
    page.wait_for_selector("#boot.hidden", state="attached", timeout=10000)
    return ctx, page, cap, d


def launch(**kw):
    """sync_playwright + chromium launch context manager."""
    return sync_playwright()
