"""kernel/gateway/gemini.py — P1-C: cloud adapter (Gemini generateContent).

Renders the neutral Message/declarations shapes into Gemini v1beta payloads
and parses candidates back into the neutral Response. Function-call parts
become kernel ToolCall structs (ids are synthesized — Gemini emits none).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from kernel.gateway.base import (
    _GEMINI_ENDPOINT,
    Gateway,
    GatewayError,
    GatewaySettings,
    Message,
    Post,
    Provider,
    Response,
    ToolResultLike,
)
from kernel.types import ToolCall

_FINISH = {"STOP": "stop", "MAX_TOKENS": "length", "SAFETY": "safety"}


def _tool_response_payload(result: ToolResultLike) -> dict[str, Any]:
    if result.ok:
        return {"ok": True, "result": result.data}
    return {"ok": False, "error": result.error or "tool failed"}


def _render_contents(
    messages: Sequence[Message],
) -> tuple[list[dict[str, Any]], list[str]]:
    """Neutral turns → (contents, system_texts). Tool results render as
    functionResponse parts (role "user") per the v1beta function-calling flow."""
    contents: list[dict[str, Any]] = []
    system: list[str] = []
    for msg in messages:
        if msg.role == "system":
            system.append(msg.text)
        elif msg.role == "user":
            contents.append({"role": "user", "parts": [{"text": msg.text}]})
        elif msg.role == "assistant":
            parts: list[dict[str, Any]] = []
            if msg.text:
                parts.append({"text": msg.text})
            for index, call in enumerate(msg.tool_calls):
                fc: dict[str, Any] = {"name": call.name, "args": dict(call.args)}
                part: dict[str, Any] = {"functionCall": fc}
                if index < len(msg.tool_signatures) and msg.tool_signatures[index]:
                    part["thoughtSignature"] = msg.tool_signatures[index]
                parts.append(part)
            contents.append({"role": "model", "parts": parts or [{"text": ""}]})
        elif msg.role == "tool":
            parts = [
                {
                    "functionResponse": {
                        "name": result.name,
                        "response": _tool_response_payload(result),
                    }
                }
                for result in msg.tool_results
            ]
            contents.append({"role": "user", "parts": parts})
    return contents, system


class GeminiAdapter(Gateway):
    provider = Provider.GEMINI

    def __init__(
        self,
        settings: GatewaySettings,
        api_key: str | None = None,
        post: Post | None = None,
    ) -> None:
        super().__init__(settings, post)
        if not api_key:
            raise GatewayError(
                "GeminiAdapter requires an API key (config loader: get_api_key())"
            )
        self._api_key = api_key

    @property
    def model(self) -> str:
        return self._settings.gemini_model

    def _url(self) -> str:
        return f"{_GEMINI_ENDPOINT}/{self._settings.gemini_model}:generateContent"

    def _headers(self) -> dict[str, str]:
        return {"x-goog-api-key": self._api_key}

    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        contents, system = _render_contents(messages)
        payload: dict[str, Any] = {"contents": contents}
        if system:
            payload["systemInstruction"] = {
                "parts": [{"text": text} for text in system]
            }
        if tools:
            payload["tools"] = [
                {"functionDeclarations": [dict(decl) for decl in tools]}
            ]
        gen_config: dict[str, Any] = {}
        if response_schema is not None:
            gen_config["responseMimeType"] = "application/json"
            gen_config["responseSchema"] = dict(response_schema)
        if gen_config:
            payload["generationConfig"] = gen_config
        return payload

    def _parse(self, data: Mapping[str, Any]) -> Response:
        candidates = data.get("candidates") or []
        if not candidates:
            raise GatewayError("Gemini returned no candidates (blocked or empty)")
        candidate = candidates[0]
        parts = (candidate.get("content") or {}).get("parts") or []
        text = "".join(part.get("text", "") for part in parts if "text" in part)
        call_parts = [part for part in parts if "functionCall" in part]
        calls = [
            ToolCall(
                id=f"gemini-{index + 1}",
                name=part["functionCall"]["name"],
                args=dict(part["functionCall"].get("args") or {}),
                source="model",
            )
            for index, part in enumerate(call_parts)
        ]
        # Gemini 3 enforces thought signatures on echoed functionCall parts —
        # capture them here so the agent loop can round-trip them (400 otherwise).
        signatures = tuple(
            str(part.get("thoughtSignature")
                or part["functionCall"].get("thoughtSignature") or "")
            for part in call_parts
        )
        raw_finish = str(candidate.get("finishReason") or "STOP")
        finish = _FINISH.get(raw_finish.upper(), raw_finish.lower())
        meta = data.get("usageMetadata") or {}
        usage = {
            "input": int(meta.get("promptTokenCount") or 0),
            "output": int(meta.get("candidatesTokenCount") or 0),
        }
        return Response(
            text=text,
            tool_calls=tuple(calls),
            tool_signatures=signatures,
            provider=self.provider.value,
            model=self._settings.gemini_model,
            finish=finish,
            usage=usage,
        )


__all__ = ["GeminiAdapter"]
