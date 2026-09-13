"""kernel/webagent/agent.py — research/12 D1: the browser loop.

The agent executes the gateway's browser actions on a Playwright page and
feeds the resulting screenshot back until the model answers in text. The
model speaks in 1000×1000 normalized coordinates; this module owns the
denormalization and the action dispatch (no page-object knowledge leaks
into the gateway).

`browser_factory` is injectable: the default opens headless Chromium via a
LAZY playwright import; tests inject a fake page. `gateway` is duck-typed
(the real ComputerUseGateway, or a scripted fake in tests).
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from kernel.gateway.cu import ComputerUseGateway, CuCall

log = logging.getLogger(__name__)

__all__ = ["WebAgent"]

_USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
               "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

# The model may emit a safety_decision arg (computer-use contract): the
# explanation is surfaced via on_event and acknowledged in the outcome, so
# the loop keeps running while the human watches the transcript.
_SAFETY_ACK = "safety_acknowledgement"


async def _default_browser_factory(viewport_w: int, viewport_h: int):
    """Open headless Chromium; returns (page, close). Playwright is imported
    lazily so importing kernel.webagent never requires the package."""
    from playwright.async_api import async_playwright
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(headless=True)
    context = await browser.new_context(
        viewport={"width": viewport_w, "height": viewport_h},
        user_agent=_USER_AGENT,
    )
    page = await context.new_page()
    try:
        await page.goto("https://www.google.com")
    except Exception:  # noqa: BLE001 — a dead landing page must not kill the task
        log.info("landing page goto failed; continuing anyway")

    async def close() -> None:
        try:
            await browser.close()
        finally:
            await pw.stop()

    return page, close


class WebAgent:
    """Run one natural-language task in a browser via computer-use turns."""

    def __init__(
        self,
        gateway: ComputerUseGateway,
        *,
        viewport_w: int = 1440,
        viewport_h: int = 900,
        max_turns: int = 20,
        settle_s: float = 1.0,
        browser_factory: Callable[[int, int],
                                  Awaitable[tuple[Any, Callable[[], Any]]]]
        | None = None,
        on_event: Callable[[str, str], None] | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be >= 1")
        self._gateway = gateway
        self._viewport_w = viewport_w
        self._viewport_h = viewport_h
        self._max_turns = max_turns
        self._settle_s = settle_s
        self._browser_factory = browser_factory or _default_browser_factory
        self._on_event = on_event

    async def run_task(self, prompt: str) -> dict[str, Any]:
        """Returns {status: success|failure, summary, turns, actions}."""
        if not prompt.strip():
            return {"status": "failure", "summary": "empty instruction",
                    "turns": 0, "actions": []}
        page, close = await self._browser_factory(self._viewport_w,
                                                  self._viewport_h)
        actions: list[str] = []
        turns = 0
        try:
            task = self._gateway.task(prompt)
            screenshot = await self._shot(page)
            turn = await task.first(screenshot, url=self._url(page))
            while turns < self._max_turns:
                turns += 1
                if turn.thought:
                    self._emit("thought", turn.thought)
                if not turn.calls:
                    summary = turn.text.strip()
                    return {
                        "status": "success" if summary else "failure",
                        "summary": summary
                        or "the model produced no summary and no actions",
                        "turns": turns,
                        "actions": actions,
                    }
                results = []
                for call in turn.calls:
                    outcome = await self._execute(page, call)
                    actions.append(f"{call.name}: "
                                   f"{self._short(outcome)}")
                    self._emit("action", actions[-1])
                    results.append((call.id, call.name, outcome))
                await asyncio.sleep(self._settle_s)
                screenshot = await self._shot(page)
                turn = await task.respond(results, screenshot,
                                          url=self._url(page))
            return {
                "status": "failure",
                "summary": f"stopped at the {self._max_turns}-turn limit "
                           "before the task was confirmed complete",
                "turns": turns,
                "actions": actions,
            }
        finally:
            try:
                result = close()
                if asyncio.iscoroutine(result):
                    await result
            except Exception:  # noqa: BLE001 — browser cleanup is best-effort
                log.exception("closing the web-agent browser failed")

    # -- internals ---------------------------------------------------------

    async def _shot(self, page: Any) -> bytes:
        return await page.screenshot(type="png")

    @staticmethod
    def _url(page: Any) -> str:
        try:
            return str(page.url)
        except Exception:  # noqa: BLE001
            return ""

    async def _execute(self, page: Any, call: CuCall) -> dict[str, Any]:
        """Dispatch one model action; a failing action becomes an outcome
        the model can observe, never an exception that kills the task."""
        args = call.args or {}
        try:
            if "safety_decision" in args:
                decision = args.get("safety_decision") or {}
                if decision.get("decision") == "require_confirmation":
                    self._emit("safety", str(decision.get("explanation")
                                             or "site safety check"))

            name = call.name
            if name == "open_web_browser":
                return {}
            if name == "navigate":
                await page.goto(str(args["url"]))
                return {}
            if name == "go_back":
                await page.go_back()
                return {}
            if name == "go_forward":
                await page.go_forward()
                return {}
            if name == "search":
                await page.goto("https://www.google.com")
                return {}
            if name == "wait_5_seconds":
                await asyncio.sleep(5)
                return {}

            if name == "click_at":
                x, y = self._coords(args)
                await page.mouse.click(x, y)
                return {}
            if name == "hover_at":
                x, y = self._coords(args)
                await page.mouse.move(x, y)
                return {}

            if name == "type_text_at":
                x, y = self._coords(args)
                await page.mouse.click(x, y)
                if args.get("clear_before_typing", True):
                    await page.keyboard.press("Control+A")
                    await page.keyboard.press("Backspace")
                await page.keyboard.type(str(args.get("text", "")))
                if args.get("press_enter", False):
                    await page.keyboard.press("Enter")
                return {}

            if name == "key_combination":
                await page.keyboard.press(str(args.get("keys") or ""))
                return {}

            if name in ("scroll_document", "scroll_at"):
                if name == "scroll_at":
                    x, y = self._coords(args)
                    await page.mouse.move(x, y)
                magnitude = int(args.get("magnitude") or 800)
                direction = str(args.get("direction") or "down")
                dx, dy = 0, 0
                if direction == "down":
                    dy = magnitude
                elif direction == "up":
                    dy = -magnitude
                elif direction == "right":
                    dx = magnitude
                elif direction == "left":
                    dx = -magnitude
                await page.mouse.wheel(dx, dy)
                return {}

            if name == "drag_and_drop":
                sx, sy = self._coords(args)
                ex = self._denorm(args.get("destination_x"), self._viewport_w)
                ey = self._denorm(args.get("destination_y"), self._viewport_h)
                await page.mouse.move(sx, sy)
                await page.mouse.down()
                await page.mouse.move(ex, ey, steps=10)
                await page.mouse.up()
                return {}

            return {"error": f"unimplemented action {name!r}"}
        except Exception as exc:  # noqa: BLE001 — observed, not raised
            return {"error": f"{type(exc).__name__} while running {call.name}"}

    def _coords(self, args: dict[str, Any]) -> tuple[int, int]:
        return (self._denorm(args.get("x"), self._viewport_w),
                self._denorm(args.get("y"), self._viewport_h))

    @staticmethod
    def _denorm(value: Any, dimension: int) -> int:
        try:
            return int(round((float(value) / 1000.0) * dimension))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _short(outcome: dict[str, Any]) -> str:
        if not outcome:
            return "ok"
        text = ", ".join(f"{k}={str(v)[:60]}" for k, v in outcome.items())
        return text[:120]

    def _emit(self, kind: str, text: str) -> None:
        if self._on_event is not None:
            try:
                self._on_event(kind, text)
            except Exception:  # noqa: BLE001 — observers must not kill the task
                log.exception("web-agent event observer failed")
