"""kernel/gateway/cu.py — research/12 D1: the computer-use gateway.

The Gemini 2.5 Computer-Use protocol (ada_v2's `web_agent.py`, rebuilt
behind the gateway): a `computerUse` tool declaration, browser screenshots
traveling INSIDE functionResponse parts, and thought parts for narration.

- Model strings live HERE (Kill List #3) — the tool layer only ever sees
  this module's task objects.
- No google SDK import: the same injectable `Post` transport and clean
  `GatewayError` discipline as the text gateway (stdlib only).
- The agent loop (kernel/webagent/agent.py) drives a ComputerUseTask; the
  task owns the conversation contents so protocol details never leak.
"""

from __future__ import annotations

import asyncio
import base64
from dataclasses import dataclass
from typing import Any, Mapping

from kernel.gateway.base import (
    _GEMINI_ENDPOINT,
    GatewayError,
    Post,
    urllib_post,
)

DEFAULT_GEMINI_CU_MODEL = "gemini-2.5-computer-use-preview-10-2025"
_CU_ENVIRONMENT = "ENVIRONMENT_BROWSER"


@dataclass(frozen=True)
class CuCall:
    """One model-requested browser action (1000×1000 normalized coords)."""

    id: str
    name: str
    args: dict


@dataclass(frozen=True)
class CuTurn:
    """One model decision: narration plus the actions it wants executed."""

    thought: str = ""
    text: str = ""
    calls: tuple[CuCall, ...] = ()


def _screenshot_part(screenshot_png: bytes) -> dict[str, Any]:
    return {"inlineData": {
        "mimeType": "image/png",
        "data": base64.b64encode(screenshot_png).decode("ascii"),
    }}


class ComputerUseTask:
    """The turn-by-turn state of ONE web-agent task. The agent calls
    `first()` with the initial screenshot, executes the returned calls, then
    `respond()` with the outcomes and the NEW screenshot — repeat until the
    model answers in text (done) or the caller's turn budget ends."""

    def __init__(self, gateway: "ComputerUseGateway", instruction: str):
        self._gateway = gateway
        self._instruction = instruction
        self._contents: list[dict[str, Any]] = []

    async def first(self, screenshot_png: bytes, url: str = "") -> CuTurn:
        self._contents.append({"role": "user", "parts": [
            {"text": self._instruction},
            _screenshot_part(screenshot_png),
        ]})
        return await self._exchange(url)

    async def respond(self, results, screenshot_png: bytes,
                      url: str = "") -> CuTurn:
        """Report executed actions and get the next decision. `results` is a
        sequence of (call_id, action_name, outcome-dict) — the screenshot is
        embedded in each functionResponse per the computer-use contract."""
        model_parts: list[dict[str, Any]] = []
        for call in self._last_calls:
            model_parts.append({"functionCall": {"name": call.name,
                                                 "args": dict(call.args)}})
        if model_parts:
            self._contents.append({"role": "model", "parts": model_parts})
        response_parts = []
        for call_id, name, outcome in results:
            payload = dict(outcome)
            if url:
                payload.setdefault("url", url)
            response_parts.append({"functionResponse": {
                "name": name,
                "id": call_id,
                "response": payload,
                "parts": [_screenshot_part(screenshot_png)],
            }})
        self._contents.append({"role": "user", "parts": response_parts})
        return await self._exchange(url)

    async def _exchange(self, url: str) -> CuTurn:
        turn = await self._gateway._complete(self._contents)
        self._last_calls = turn.calls
        return turn

    _last_calls: tuple[CuCall, ...] = ()


class ComputerUseGateway:
    """Owns the CU model string + protocol. Tasks are cheap; one gateway may
    drive many sequential tasks (they never share contents)."""

    def __init__(self, api_key: str, *,
                 model: str = DEFAULT_GEMINI_CU_MODEL,
                 post: Post | None = None,
                 timeout_s: float = 90.0) -> None:
        if not api_key:
            raise GatewayError(
                "ComputerUseGateway requires an API key (gateway provider)")
        self._api_key = api_key
        self._model = model
        self._post: Post = post if post is not None else urllib_post
        self._timeout_s = timeout_s

    @property
    def model(self) -> str:
        return self._model

    def task(self, instruction: str) -> ComputerUseTask:
        if not instruction.strip():
            raise GatewayError("computer-use task needs an instruction")
        return ComputerUseTask(self, instruction)

    async def _complete(self, contents: list[dict[str, Any]]) -> CuTurn:
        payload: dict[str, Any] = {
            "contents": contents,
            "tools": [{"computerUse": {"environment": _CU_ENVIRONMENT}}],
            "generationConfig": {"thinkingConfig": {"includeThoughts": True}},
        }
        url = f"{_GEMINI_ENDPOINT}/{self._model}:generateContent"
        status, data = await asyncio.to_thread(
            self._post, url, {"x-goog-api-key": self._api_key},
            payload, self._timeout_s)
        if status >= 400:
            raise GatewayError(f"HTTP {status} from computer-use gateway")
        return self._parse(data)

    @staticmethod
    def _parse(data: Mapping[str, Any]) -> CuTurn:
        candidates = data.get("candidates") or []
        if not candidates:
            raise GatewayError("computer-use gateway returned no candidates")
        parts = (candidates[0].get("content") or {}).get("parts") or []
        thought_chunks: list[str] = []
        text_chunks: list[str] = []
        calls: list[CuCall] = []
        for index, part in enumerate(parts):
            if part.get("thought"):
                thought_chunks.append(str(part.get("text", "")))
                continue
            if "text" in part:
                text_chunks.append(str(part.get("text", "")))
            fc = part.get("functionCall")
            if fc:
                calls.append(CuCall(
                    id=str(fc.get("id") or f"cu-{index + 1}"),
                    name=str(fc.get("name") or ""),
                    args=dict(fc.get("args") or {}),
                ))
        return CuTurn(thought="".join(thought_chunks),
                      text="".join(text_chunks),
                      calls=tuple(calls))


__all__ = ["CuCall", "ComputerUseGateway", "ComputerUseTask", "CuTurn",
           "DEFAULT_GEMINI_CU_MODEL"]
