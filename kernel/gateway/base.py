"""kernel/gateway/base.py — P1-C: the provider-neutral model gateway contract.

Design notes:
- ONE interface, TWO adapters (research/06 §4): Gemini (cloud) and Ollama (local).
  Both produce the SAME neutral `Response` whose tool calls are real kernel
  `ToolCall` structs — the agent loop (P1-G) never sees provider-specific shapes.
- Model strings live here (and in config overrides read via `GatewaySettings.
  from_config`) — never outside the gateway (Kill List #3).
- stdlib only: the HTTP transport is a urllib POST, injectable for hermetic
  tests (`post=` callable) — no SDK dependency, voice/UI stay outside the kernel.
- Errors raise `GatewayError` with clean technical messages; provider response
  bodies and raw exception text never travel further (Kill List #4).
"""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.parse
import urllib.request
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, ClassVar, Protocol

from kernel.types import ToolCall

DEFAULT_GEMINI_MODEL = "gemini-3.6-flash"  # 2.5 retired for new keys (API notice 2026-09-08)
# R2: the live audio session's model string lives here too (Kill List #3 —
# no model name outside the gateway). The gateway has no Live adapter yet
# (P1-H modality-adapter residual), so the constant is the app's only touch
# point; when a Live adapter lands it moves behind GatewaySettings.
DEFAULT_GEMINI_LIVE_MODEL = "models/gemini-2.5-flash-native-audio-preview-12-2025"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_OPENAI_MODEL = "gpt-4o-mini"          # overridden per endpoint
DEFAULT_OPENAI_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_S = 60.0

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
_ROLES = ("system", "user", "assistant", "tool")


class Provider(str, Enum):
    GEMINI = "gemini"
    OLLAMA = "ollama"
    OPENAI = "openai"   # any ChatGPT-compatible /chat/completions endpoint


class GatewayError(Exception):
    """A clean, speakable gateway failure (status/reason only — never a body)."""


class Post(Protocol):
    """Blocking JSON POST transport (real: urllib; tests: fakes)."""

    def __call__(
        self,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, Any],
        timeout_s: float,
    ) -> tuple[int, dict[str, Any]]: ...


def urllib_post(
    url: str,
    headers: Mapping[str, str],
    payload: Mapping[str, Any],
    timeout_s: float,
) -> tuple[int, dict[str, Any]]:
    """Default transport: blocking JSON POST via urllib (run in a thread)."""
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={**dict(headers), "Content-Type": "application/json"},
        method="POST",
    )
    host = urllib.parse.urlsplit(url).netloc
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as resp:
            return int(resp.status), json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise GatewayError(f"HTTP {exc.code} from {host}") from None
    except urllib.error.URLError as exc:
        raise GatewayError(f"cannot reach {host}: {exc.reason}") from None
    except json.JSONDecodeError:
        raise GatewayError(f"{host} returned non-JSON response") from None


@dataclass(frozen=True)
class ToolResultLike:
    """Minimal view of a ToolResult for history rendering (name + outcome);
    adapters never import the execution layer, they only read these fields."""

    name: str
    ok: bool
    data: Any = None
    error: str | None = None


@dataclass(frozen=True)
class InlineData:
    """A binary part (image/audio/video bytes) for multimodal completions.

    Rendered as Gemini `inlineData` parts (base64). Only the Gemini adapter
    supports it today — Ollama/OpenAI adapters refuse with a clean error so
    a multimodal call never silently degrades to text-only.
    """

    mime_type: str
    data: bytes


@dataclass(frozen=True)
class Message:
    """One conversation turn in provider-neutral form.

    role="assistant" echoes a previous model turn (optionally with the tool
    calls it made); role="tool" carries the results that answer those calls.
    `tool_signatures` is a parallel array to `tool_calls` for provider-side
    per-call metadata that must survive the round trip (Gemini 3 thought
    signatures — enforced by the API, ignored by Ollama).
    `parts` carries multimodal inline data (images/audio) alongside `text` —
    user role only (R2: the legacy actions' vision/audio calls route through
    the gateway instead of the google SDK).
    """

    role: str
    text: str = ""
    parts: tuple[InlineData, ...] = ()
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResultLike, ...] = ()
    tool_signatures: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"Message.role must be one of {_ROLES}, got {self.role!r}")
        if self.role == "tool" and not self.tool_results:
            raise ValueError("Message(role='tool') requires tool_results")
        if self.tool_signatures and len(self.tool_signatures) != len(self.tool_calls):
            raise ValueError("tool_signatures must align 1:1 with tool_calls")
        if self.parts and self.role != "user":
            raise ValueError("Message parts (multimodal) are only valid on role='user'")


@dataclass(frozen=True)
class Response:
    """A provider-neutral completion. `tool_calls` are kernel ToolCall structs
    (source="model"), identical in shape from every adapter. `tool_signatures`
    is a parallel array of provider per-call round-trip payloads (Gemini 3
    thought signatures; empty strings when the provider has none)."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
    tool_signatures: tuple[str, ...] = ()
    provider: str = ""
    model: str = ""
    finish: str = "stop"  # stop | tool_calls | length | safety | ...
    usage: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class GatewaySettings:
    """Gateway configuration — the ONE place model strings may appear (config
    overrides flow through `from_config`; defaults live in this module)."""

    provider: Provider
    gemini_model: str = DEFAULT_GEMINI_MODEL
    ollama_model: str = DEFAULT_OLLAMA_MODEL
    ollama_base_url: str = DEFAULT_OLLAMA_URL
    openai_model: str = DEFAULT_OPENAI_MODEL
    openai_base_url: str = DEFAULT_OPENAI_URL
    request_timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "GatewaySettings":
        raw = str(cfg.get("llm_provider", "gemini")).lower()
        try:
            provider = Provider(raw)
        except ValueError:
            raise GatewayError(
                f"unknown llm_provider {raw!r} (expected 'gemini', 'ollama' "
                "or 'openai')"
            ) from None
        return cls(
            provider=provider,
            gemini_model=str(cfg.get("gemini_model") or DEFAULT_GEMINI_MODEL),
            ollama_model=str(cfg.get("ollama_model") or DEFAULT_OLLAMA_MODEL),
            ollama_base_url=str(cfg.get("ollama_base_url") or DEFAULT_OLLAMA_URL),
            openai_model=str(cfg.get("openai_model") or DEFAULT_OPENAI_MODEL),
            openai_base_url=str(cfg.get("openai_base_url") or DEFAULT_OPENAI_URL),
            request_timeout_s=float(cfg.get("llm_timeout_s") or DEFAULT_TIMEOUT_S),
        )


class Gateway(ABC):
    """The one interface the agent loop (P1-G) programs against."""

    provider: ClassVar[Provider]

    def __init__(self, settings: GatewaySettings, post: Post | None = None) -> None:
        self._settings = settings
        self._post: Post = post if post is not None else urllib_post

    @property
    @abstractmethod
    def model(self) -> str: ...

    @abstractmethod
    def _url(self) -> str: ...

    @abstractmethod
    def _headers(self) -> dict[str, str]: ...

    @abstractmethod
    def _build_payload(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]],
        response_schema: Mapping[str, Any] | None,
    ) -> dict[str, Any]: ...

    @abstractmethod
    def _parse(self, data: Mapping[str, Any]) -> Response: ...

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[Mapping[str, Any]] = (),
        response_schema: Mapping[str, Any] | None = None,
    ) -> Response:
        """One non-streaming completion. `tools` are registry.declarations()
        (Gemini-style JSON-schema shape, or provider-native tool dicts like
        `{"google_search": {}}` — the Gemini adapter passes those through
        verbatim, see its payload builder); `response_schema` forces
        structured output (Gemini responseSchema / Ollama format). Blocking
        HTTP runs in a thread so the caller's loop never stalls."""
        if not messages:
            raise GatewayError("complete() needs at least one message")
        payload = self._build_payload(messages, tools, response_schema)
        status, data = await asyncio.to_thread(
            self._post,
            self._url(),
            self._headers(),
            payload,
            self._settings.request_timeout_s,
        )
        if status >= 400:
            raise GatewayError(f"HTTP {status} from {self.provider.value}")
        return self._parse(data)


def build_gateway(
    settings: GatewaySettings,
    api_key: str | None = None,
    post: Post | None = None,
) -> Gateway:
    """Factory: the only place that knows which adapters exist (config enum)."""
    from kernel.gateway.gemini import GeminiAdapter
    from kernel.gateway.ollama import OllamaAdapter
    from kernel.gateway.openai import OpenAIChatAdapter

    if settings.provider is Provider.GEMINI:
        return GeminiAdapter(settings, api_key=api_key, post=post)
    if settings.provider is Provider.OLLAMA:
        return OllamaAdapter(settings, post=post)
    if settings.provider is Provider.OPENAI:
        return OpenAIChatAdapter(settings, api_key=api_key, post=post)
    raise GatewayError(f"no adapter for provider {settings.provider!r}")


__all__ = [
    "DEFAULT_GEMINI_LIVE_MODEL",
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_OPENAI_URL",
    "Gateway",
    "GatewayError",
    "GatewaySettings",
    "InlineData",
    "Message",
    "Post",
    "Provider",
    "Response",
    "ToolResultLike",
    "build_gateway",
    "urllib_post",
]
