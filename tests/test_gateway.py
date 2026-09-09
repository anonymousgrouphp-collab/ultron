"""tests/test_gateway.py — P1-C: provider-neutral gateway contract.

All tests are hermetic: the HTTP transport is a FakePost (no network). One test
uses a real urllib POST to a refused loopback port to pin the error mapping.
The provider-neutrality test is research/06 §4's core requirement: Gemini and
Ollama produce the same Response/ToolCall shapes.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

import pytest

from kernel.gateway import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    GeminiAdapter,
    GatewayError,
    GatewaySettings,
    Message,
    OllamaAdapter,
    Provider,
    build_gateway,
    urllib_post,
)
from kernel.gateway.base import Response, ToolResultLike
from kernel.types import ToolCall

DECLS: tuple[dict[str, Any], ...] = (
    {
        "name": "open_app",
        "description": "Open an application.",
        "parameters": {
            "type": "object",
            "properties": {"app": {"type": "string"}},
            "required": ["app"],
        },
    },
)

GEMINI_TEXT_BODY = {
    "candidates": [
        {
            "content": {"parts": [{"text": "Hello."}]},
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 11, "candidatesTokenCount": 7},
}

GEMINI_TOOL_BODY = {
    "candidates": [
        {
            "content": {
                "parts": [
                    {"text": "Opening."},
                    {"functionCall": {"name": "open_app", "args": {"app": "calc"}}},
                ]
            },
            "finishReason": "STOP",
        }
    ],
    "usageMetadata": {"promptTokenCount": 20, "candidatesTokenCount": 4},
}

OLLAMA_TEXT_BODY = {
    "message": {"content": "Hello."},
    "done": True,
    "prompt_eval_count": 9,
    "eval_count": 5,
    "model": "qwen3:8b",
}

OLLAMA_TOOL_BODY = {
    "message": {
        "content": "Opening.",
        "tool_calls": [
            {"function": {"name": "open_app", "arguments": {"app": "calc"}}}
        ],
    },
    "done": True,
    "prompt_eval_count": 12,
    "eval_count": 6,
    "model": "qwen3:8b",
}


@dataclass
class FakePost:
    """Transport fake: records every call, returns canned status/body."""

    status: int = 200
    body: dict[str, Any] = field(default_factory=dict)
    error: Exception | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(
        self,
        url: str,
        headers: dict[str, str],
        payload: dict[str, Any],
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]:
        self.calls.append(
            {"url": url, "headers": dict(headers), "payload": payload,
             "timeout": timeout_s}
        )
        if self.error is not None:
            raise self.error
        return self.status, self.body


def gemini_settings(**over: Any) -> GatewaySettings:
    return GatewaySettings(provider=Provider.GEMINI, **over)


def ollama_settings(**over: Any) -> GatewaySettings:
    return GatewaySettings(provider=Provider.OLLAMA, **over)


# ---------------------------------------------------------------- Gemini ----


def test_gemini_text_response_parses_neutrally() -> None:
    post = FakePost(body=GEMINI_TEXT_BODY)
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    resp = asyncio.run(gw.complete([Message(role="user", text="hi")]))
    assert resp.text == "Hello."
    assert resp.tool_calls == ()
    assert resp.provider == "gemini"
    assert resp.model == DEFAULT_GEMINI_MODEL
    assert resp.finish == "stop"
    assert resp.usage == {"input": 11, "output": 7}


def test_gemini_tool_calls_become_kernel_tool_calls() -> None:
    post = FakePost(body=GEMINI_TOOL_BODY)
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    resp = asyncio.run(gw.complete([Message(role="user", text="open calc")]))
    assert resp.finish == "stop"
    assert len(resp.tool_calls) == 1
    call = resp.tool_calls[0]
    assert isinstance(call, ToolCall)
    assert call.id == "gemini-1"
    assert call.name == "open_app"
    assert dict(call.args) == {"app": "calc"}
    assert call.source == "model"


def test_gemini_thought_signature_round_trip() -> None:
    """Gemini 3 enforces thought signatures on echoed functionCall parts."""
    body = {
        "candidates": [{
            "content": {"parts": [{
                "functionCall": {"name": "open_app", "args": {"app": "calc"},
                                 "thoughtSignature": "sig-abc"}}]},
            "finishReason": "STOP",
        }],
    }
    post = FakePost(body=body)
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    resp = asyncio.run(gw.complete([Message(role="user", text="open calc")]))
    assert resp.tool_signatures == ("sig-abc",)
    history = [
        Message(role="user", text="open calc"),
        Message(role="assistant", tool_calls=resp.tool_calls,
                tool_signatures=resp.tool_signatures),
    ]
    asyncio.run(gw.complete(history, tools=DECLS))
    part = post.calls[1]["payload"]["contents"][1]["parts"][0]
    assert part["thoughtSignature"] == "sig-abc"
    assert part["functionCall"]["name"] == "open_app"


def test_gemini_payload_shape_system_and_declarations() -> None:
    post = FakePost(body=GEMINI_TEXT_BODY)
    gw = GeminiAdapter(gemini_settings(), api_key="secret", post=post)
    asyncio.run(
        gw.complete(
            [Message(role="system", text="be brief"), Message(role="user", text="hi")],
            tools=DECLS,
        )
    )
    payload = post.calls[0]["payload"]
    assert payload["systemInstruction"] == {"parts": [{"text": "be brief"}]}
    assert payload["tools"] == [{"functionDeclarations": [dict(DECLS[0])]}]
    assert post.calls[0]["headers"] == {"x-goog-api-key": "secret"}
    assert post.calls[0]["url"].endswith(
        f"/models/{DEFAULT_GEMINI_MODEL}:generateContent"
    )


def test_gemini_structured_output_config() -> None:
    post = FakePost(body=GEMINI_TEXT_BODY)
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    asyncio.run(
        gw.complete([Message(role="user", text="hi")], response_schema=schema)
    )
    gen = post.calls[0]["payload"]["generationConfig"]
    assert gen["responseMimeType"] == "application/json"
    assert gen["responseSchema"] == schema


def test_gemini_tool_result_round_trip() -> None:
    post = FakePost(body=GEMINI_TEXT_BODY)
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    history = [
        Message(role="user", text="open calc"),
        Message(role="assistant", tool_calls=(ToolCall("gemini-1", "open_app"),)),
        Message(
            role="tool",
            tool_results=(
                ToolResultLike(name="open_app", ok=False, error="app not found"),
            ),
        ),
    ]
    asyncio.run(gw.complete(history))
    parts = post.calls[0]["payload"]["contents"][2]["parts"]
    assert parts[0]["functionResponse"]["name"] == "open_app"
    assert parts[0]["functionResponse"]["response"] == {
        "ok": False, "error": "app not found",
    }
    assert post.calls[0]["payload"]["contents"][1]["role"] == "model"


def test_gemini_no_candidates_raises_clean_error() -> None:
    post = FakePost(body={"promptFeedback": {"blockReason": "SAFETY"}})
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    with pytest.raises(GatewayError, match="no candidates"):
        asyncio.run(gw.complete([Message(role="user", text="hi")]))


def test_gemini_requires_api_key() -> None:
    with pytest.raises(GatewayError, match="API key"):
        GeminiAdapter(gemini_settings())


def test_gemini_http_error_is_clean() -> None:
    post = FakePost(status=429, body={"error": {"message": "quota exceeded"}})
    gw = GeminiAdapter(gemini_settings(), api_key="k", post=post)
    with pytest.raises(GatewayError, match="429") as excinfo:
        asyncio.run(gw.complete([Message(role="user", text="hi")]))
    # the provider body must not leak into the error text
    assert "quota" not in str(excinfo.value)


# ---------------------------------------------------------------- Ollama ----


def test_ollama_text_response_parses_neutrally() -> None:
    post = FakePost(body=OLLAMA_TEXT_BODY)
    gw = OllamaAdapter(ollama_settings(), post=post)
    resp = asyncio.run(gw.complete([Message(role="user", text="hi")]))
    assert resp.text == "Hello."
    assert resp.finish == "stop"
    assert resp.provider == "ollama"
    assert resp.model == DEFAULT_OLLAMA_MODEL
    assert resp.usage == {"input": 9, "output": 5}


def test_ollama_tool_calls_become_kernel_tool_calls() -> None:
    post = FakePost(body=OLLAMA_TOOL_BODY)
    gw = OllamaAdapter(ollama_settings(), post=post)
    resp = asyncio.run(gw.complete([Message(role="user", text="open calc")]))
    assert resp.finish == "tool_calls"
    assert [c.name for c in resp.tool_calls] == ["open_app"]
    assert dict(resp.tool_calls[0].args) == {"app": "calc"}
    assert resp.tool_calls[0].source == "model"


def test_ollama_payload_shape_tools_and_stream() -> None:
    post = FakePost(body=OLLAMA_TEXT_BODY)
    gw = OllamaAdapter(ollama_settings(), post=post)
    asyncio.run(
        gw.complete(
            [Message(role="system", text="be brief"), Message(role="user", text="hi")],
            tools=DECLS,
        )
    )
    payload = post.calls[0]["payload"]
    assert payload["stream"] is False
    assert payload["model"] == DEFAULT_OLLAMA_MODEL
    assert payload["messages"][0] == {"role": "system", "content": "be brief"}
    assert payload["tools"] == [
        {
            "type": "function",
            "function": {
                "name": "open_app",
                "description": "Open an application.",
                "parameters": dict(DECLS[0]["parameters"]),
            },
        }
    ]
    assert post.calls[0]["url"] == f"{DEFAULT_OLLAMA_URL}/api/chat"


def test_ollama_structured_output_via_format() -> None:
    post = FakePost(body=OLLAMA_TEXT_BODY)
    gw = OllamaAdapter(ollama_settings(), post=post)
    schema = {"type": "object", "properties": {"answer": {"type": "string"}}}
    asyncio.run(
        gw.complete([Message(role="user", text="hi")], response_schema=schema)
    )
    assert post.calls[0]["payload"]["format"] == schema


def test_ollama_tool_result_round_trip() -> None:
    post = FakePost(body=OLLAMA_TEXT_BODY)
    gw = OllamaAdapter(ollama_settings(), post=post)
    history = [
        Message(role="user", text="open calc"),
        Message(
            role="tool",
            tool_results=(
                ToolResultLike(name="open_app", ok=True, data={"pid": 4242}),
            ),
        ),
    ]
    asyncio.run(gw.complete(history))
    sent = post.calls[0]["payload"]["messages"][1]
    assert sent["role"] == "tool"
    assert sent["name"] == "open_app"
    assert json.loads(sent["content"]) == {"ok": True, "result": {"pid": 4242}}


# ------------------------------------------------- provider neutrality ------


def test_gemini_and_ollama_produce_identical_neutral_shapes() -> None:
    """research/06 §4: the tool-call shape must be provider-neutral."""

    def normalize(resp: Response) -> tuple[str, list[tuple[str, str]], str]:
        return (
            resp.text,
            [
                (c.name, json.dumps(dict(c.args), sort_keys=True))
                for c in resp.tool_calls
            ],
            resp.tool_calls[0].source if resp.tool_calls else "",
        )

    gemini = GeminiAdapter(
        gemini_settings(), api_key="k", post=FakePost(body=GEMINI_TOOL_BODY)
    )
    ollama = OllamaAdapter(ollama_settings(), post=FakePost(body=OLLAMA_TOOL_BODY))
    r1 = asyncio.run(gemini.complete([Message(role="user", text="open calc")]))
    r2 = asyncio.run(ollama.complete([Message(role="user", text="open calc")]))
    assert normalize(r1) == normalize(r2)
    assert r1.tool_calls[0].source == r2.tool_calls[0].source == "model"


# ------------------------------------------------------- settings/factory ---


def test_settings_from_config_defaults_and_overrides() -> None:
    s = GatewaySettings.from_config({})
    assert s.provider is Provider.GEMINI
    assert s.gemini_model == DEFAULT_GEMINI_MODEL
    s = GatewaySettings.from_config(
        {"llm_provider": "ollama", "ollama_model": "qwen3:4b",
         "ollama_base_url": "http://192.168.1.50:11434", "llm_timeout_s": 30}
    )
    assert s.provider is Provider.OLLAMA
    assert s.ollama_model == "qwen3:4b"
    assert s.ollama_base_url == "http://192.168.1.50:11434"
    assert s.request_timeout_s == 30.0
    with pytest.raises(GatewayError, match="llm_provider"):
        GatewaySettings.from_config({"llm_provider": "chatgpt"})


def test_build_gateway_returns_right_adapter() -> None:
    gw = build_gateway(gemini_settings(), api_key="k")
    assert isinstance(gw, GeminiAdapter)
    assert gw.model == DEFAULT_GEMINI_MODEL
    gw = build_gateway(ollama_settings())
    assert isinstance(gw, OllamaAdapter)
    assert gw.model == DEFAULT_OLLAMA_MODEL


def test_complete_rejects_empty_messages() -> None:
    gw = OllamaAdapter(ollama_settings(), post=FakePost(body=OLLAMA_TEXT_BODY))
    with pytest.raises(GatewayError, match="at least one message"):
        asyncio.run(gw.complete([]))


def test_urllib_post_maps_connection_refusal_to_gateway_error() -> None:
    # port 1 on loopback: connection refused, no external network involved
    with pytest.raises(GatewayError, match="cannot reach"):
        urllib_post(
            "http://127.0.0.1:1/api/chat", {}, {"model": "x"}, timeout_s=2.0
        )


class TestOpenAIChatAdapter:
    """P5 live-run wiring: the ChatGPT-compatible adapter renders the SAME
    neutral shapes as Gemini/Ollama (research/06 §4 provider-neutrality)."""

    def _adapter(self, post):
        from kernel.gateway import GatewaySettings, OpenAIChatAdapter, Provider

        settings = GatewaySettings(
            provider=Provider.OPENAI,
            openai_model="test-model", openai_base_url="https://unit.test/v1")
        return OpenAIChatAdapter(settings, api_key="k", post=post)

    def test_missing_key_refused(self) -> None:
        import pytest
        from kernel.gateway import GatewayError, GatewaySettings, OpenAIChatAdapter, Provider

        with pytest.raises(GatewayError, match="API key"):
            OpenAIChatAdapter(
                GatewaySettings(provider=Provider.OPENAI),
                api_key=None)

    def test_tool_call_round_trip_and_id_pairing(self) -> None:
        import asyncio
        from kernel.gateway import Message, ToolResultLike
        from kernel.types import ToolCall

        captured: dict = {}

        def post(url, headers, payload, timeout_s):
            captured["url"] = url
            captured["auth"] = headers.get("Authorization")
            captured["payload"] = payload
            # echo the requested tool call back
            tc = payload["tools"][0]["function"]
            return 200, {"model": payload["model"], "choices": [{"message": {
                "content": "",
                "tool_calls": [{"id": "call-1", "type": "function", "function": {
                    "name": tc["name"], "arguments": '{"path": "a.txt"}'}}],
            }}], "usage": {"prompt_tokens": 3, "completion_tokens": 5}}

        gw = self._adapter(post)
        assert gw.model == "test-model"
        call = ToolCall(id="call-1", name="write_note", args={"path": "a.txt"})
        response = asyncio.run(gw.complete(
            [Message(role="user", text="make a note"),
             Message(role="assistant", text="", tool_calls=(call,)),
             Message(role="tool", tool_results=(ToolResultLike(
                 name="write_note", ok=True, data="done"),))],
            tools=[{"name": "write_note", "description": "d",
                    "parameters": {"type": "object", "properties": {}}}],
        ))
        assert captured["url"].endswith("/chat/completions")
        assert captured["auth"] == "Bearer k"
        # the tool RESULT message answered the right call id positionally
        tool_msgs = [m for m in captured["payload"]["messages"]
                     if m["role"] == "tool"]
        assert tool_msgs[0]["tool_call_id"] == "call-1"
        # reply parses to the neutral Response with kernel ToolCalls
        assert response.provider == "openai"
        assert response.finish == "tool_calls"
        assert response.tool_calls[0].name == "write_note"
        assert response.tool_calls[0].args == {"path": "a.txt"}
        assert response.tool_calls[0].source == "model"
        assert response.usage == {"input": 3, "output": 5}

    def test_text_reply_and_json_mode(self) -> None:
        import asyncio
        from kernel.gateway import Message

        seen: dict = {}

        def post(url, headers, payload, timeout_s):
            seen["response_format"] = payload.get("response_format")
            return 200, {"choices": [{"message": {
                "content": "ready"}}]}

        gw = self._adapter(post)
        r = asyncio.run(gw.complete(
            [Message(role="user", text="hi")],
            response_schema={"type": "object", "properties": {}}))
        assert r.text == "ready" and r.finish == "stop" and r.tool_calls == ()
        assert seen["response_format"] == {"type": "json_object"}

    def test_error_and_no_choices_clean(self) -> None:
        import asyncio
        import pytest
        from kernel.gateway import GatewayError, Message

        gw = self._adapter(lambda *a: (500, {}))
        with pytest.raises(GatewayError, match="HTTP 500"):
            asyncio.run(gw.complete([Message(role="user", text="x")]))
        gw2 = self._adapter(lambda *a: (200, {"choices": []}))
        with pytest.raises(GatewayError, match="no choices"):
            asyncio.run(gw2.complete([Message(role="user", text="x")]))
