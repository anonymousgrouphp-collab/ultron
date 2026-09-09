"""kernel/gateway/openai.py — P1-C/P5: ChatGPT-compatible adapter.

One adapter for any endpoint speaking the OpenAI /chat/completions dialect
(OpenAI itself, TokenRouter, OpenRouter, vLLM, LM Studio, llama.cpp server).
This is NOT a second agent loop or LLM implementation — it renders the same
neutral Message/Response shapes through the same Gateway ABC as Gemini and
Ollama (research/06 §4 provider-neutrality: the loop cannot tell adapters
apart beyond Response.provider).

Protocol specifics handled here:
- tools            : {"type":"function","function":{name,description,parameters}}
- tool round-trip : assistant tool_calls carry ids; results come back as
                    role="tool" messages with tool_call_id set
- model strings    : only via GatewaySettings (defaults in base.py — the
                     authorized home per Kill List #3)
- api key          : caller-supplied (env at the wiring layer), sent as
                     Authorization: Bearer; NEVER logged, NEVER stored
- structured output: response_schema → {"type":"json_object"} (the portable
                     dialect — strict per-schema json_schema is endpoint-
                     specific and NOT assumed)
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from kernel.gateway.base import (
    Gateway,
    GatewayError,
    GatewaySettings,
    Message,
    Post,
    Provider,
    Response,
)
from kernel.types import ToolCall


class OpenAIChatAdapter(Gateway):
    provider = Provider.OPENAI

    def __init__(
        self,
        settings: GatewaySettings,
        api_key: str | None = None,
        post: Post | None = None,
    ) -> None:
        super().__init__(settings, post)
        if not api_key:
            raise GatewayError(
                "openai-compatible provider needs an API key "
                "(set OPENAI_API_KEY at the wiring layer)")
        self._api_key = api_key

    @property
    def model(self) -> str:
        return self._settings.openai_model

    def _url(self) -> str:
        return f"{self._settings.openai_base_url.rstrip('/')}/chat/completions"

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        rendered: list[dict[str, Any]] = []
        # Positional tool_call_id pairing: ToolResultLike carries the tool
        # NAME (not the call id), but the loop appends exactly one tool
        # Message per result, in the same order as the preceding assistant
        # echo's tool_calls — so ids pair 1:1 positionally.
        pending_call_ids: list[str] = []
        for msg in messages:
            if msg.role == "system":
                rendered.append({"role": "system", "content": msg.text})
            elif msg.role == "user":
                pending_call_ids = []
                rendered.append({"role": "user", "content": msg.text})
            elif msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": msg.text}
                if msg.tool_calls:
                    pending_call_ids = [call.id for call in msg.tool_calls]
                    entry["tool_calls"] = [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.name,
                                "arguments": json.dumps(dict(call.args)),
                            },
                        }
                        for call in msg.tool_calls
                    ]
                rendered.append(entry)
            elif msg.role == "tool":
                for result in msg.tool_results:
                    content = json.dumps(
                        {"ok": True, "result": result.data}
                        if result.ok
                        else {"ok": False, "error": result.error or "tool failed"},
                        default=str,
                    )
                    call_id = (pending_call_ids.pop(0)
                               if pending_call_ids else result.name)
                    rendered.append({
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": content,
                    })
        payload: dict[str, Any] = {
            "model": self._settings.openai_model,
            "messages": rendered,
            "stream": False,
        }
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": decl["name"],
                        "description": decl.get("description", ""),
                        "parameters": dict(decl.get("parameters") or {}),
                    },
                }
                for decl in tools
            ]
            payload["tool_choice"] = "auto"
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        return payload

    def _parse(self, data: Mapping[str, Any]) -> Response:
        choices = data.get("choices") or []
        if not choices:
            raise GatewayError("completion returned no choices")
        message = choices[0].get("message") or {}
        text = str(message.get("content") or "")
        calls: list[ToolCall] = []
        for index, call in enumerate(message.get("tool_calls") or []):
            fn = call.get("function") or {}
            raw_args = fn.get("arguments")
            if isinstance(raw_args, str):
                try:
                    args: dict[str, Any] = json.loads(raw_args)
                except json.JSONDecodeError:
                    args = {}
            elif isinstance(raw_args, Mapping):
                args = dict(raw_args)
            else:
                args = {}
            calls.append(ToolCall(
                id=str(call.get("id") or f"openai-{index + 1}"),
                name=str(fn.get("name") or ""),
                args=args,
                source="model",
            ))
        usage_raw = data.get("usage") or {}
        usage = {
            "input": int(usage_raw.get("prompt_tokens") or 0),
            "output": int(usage_raw.get("completion_tokens") or 0),
        }
        return Response(
            text=text,
            tool_calls=tuple(calls),
            provider=self.provider.value,
            model=str(data.get("model") or self._settings.openai_model),
            finish="tool_calls" if calls else "stop",
            usage=usage,
        )


__all__ = ["OpenAIChatAdapter"]
