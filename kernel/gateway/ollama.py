"""kernel/gateway/ollama.py — P1-C: local adapter (Ollama /api/chat).

Renders the neutral shapes into Ollama chat payloads (tools wrapped as
{"type":"function","function":{...}}, structured output via `format`) and
parses the reply back into the neutral Response. Ollama emits no tool-call
ids, so they are synthesized — same ToolCall structs as the Gemini adapter.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any

from kernel.gateway.base import (
    Gateway,
    GatewaySettings,
    Message,
    Post,
    Provider,
    Response,
)
from kernel.types import ToolCall


class OllamaAdapter(Gateway):
    provider = Provider.OLLAMA

    def __init__(
        self,
        settings: GatewaySettings,
        post: Post | None = None,
    ) -> None:
        super().__init__(settings, post)

    @property
    def model(self) -> str:
        return self._settings.ollama_model

    def _url(self) -> str:
        return f"{self._settings.ollama_base_url.rstrip('/')}/api/chat"

    def _headers(self) -> dict[str, str]:
        return {}

    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        rendered: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                rendered.append({"role": "system", "content": msg.text})
            elif msg.role == "user":
                rendered.append({"role": "user", "content": msg.text})
            elif msg.role == "assistant":
                entry: dict[str, Any] = {"role": "assistant", "content": msg.text}
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "function": {
                                "name": call.name,
                                "arguments": dict(call.args),
                            }
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
                    rendered.append(
                        {"role": "tool", "name": result.name, "content": content}
                    )
        payload: dict[str, Any] = {
            "model": self._settings.ollama_model,
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
        if response_schema is not None:
            payload["format"] = dict(response_schema)
        return payload

    def _parse(self, data: Mapping[str, Any]) -> Response:
        message = data.get("message") or {}
        text = str(message.get("content") or "")
        calls = [
            ToolCall(
                id=f"ollama-{index + 1}",
                name=call["function"]["name"],
                args=dict(call["function"].get("arguments") or {}),
                source="model",
            )
            for index, call in enumerate(message.get("tool_calls") or [])
        ]
        usage = {
            "input": int(data.get("prompt_eval_count") or 0),
            "output": int(data.get("eval_count") or 0),
        }
        return Response(
            text=text,
            tool_calls=tuple(calls),
            provider=self.provider.value,
            model=str(data.get("model") or self._settings.ollama_model),
            finish="tool_calls" if calls else "stop",
            usage=usage,
        )


__all__ = ["OllamaAdapter"]
