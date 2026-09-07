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

DEFAULT_GEMINI_MODEL = "gemini-2.5-flash"
DEFAULT_OLLAMA_MODEL = "qwen3:8b"
DEFAULT_OLLAMA_URL = "http://127.0.0.1:11434"
DEFAULT_TIMEOUT_S = 60.0

_GEMINI_ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/models"
_ROLES = ("system", "user", "assistant", "tool")


class Provider(str, Enum):
    GEMINI = "gemini"
    OLLAMA = "ollama"


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
class Message:
    """One conversation turn in provider-neutral form.

    role="assistant" echoes a previous model turn (optionally with the tool
    calls it made); role="tool" carries the results that answer those calls.
    """

    role: str
    text: str = ""
    tool_calls: tuple[ToolCall, ...] = ()
    tool_results: tuple[ToolResultLike, ...] = ()

    def __post_init__(self) -> None:
        if self.role not in _ROLES:
            raise ValueError(f"Message.role must be one of {_ROLES}, got {self.role!r}")
        if self.role == "tool" and not self.tool_results:
            raise ValueError("Message(role='tool') requires tool_results")


@dataclass(frozen=True)
class Response:
    """A provider-neutral completion. `tool_calls` are kernel ToolCall structs
    (source="model"), identical in shape from every adapter."""

    text: str
    tool_calls: tuple[ToolCall, ...] = ()
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
    request_timeout_s: float = DEFAULT_TIMEOUT_S

    @classmethod
    def from_config(cls, cfg: Mapping[str, Any]) -> "GatewaySettings":
        raw = str(cfg.get("llm_provider", "gemini")).lower()
        try:
            provider = Provider(raw)
        except ValueError:
            raise GatewayError(
                f"unknown llm_provider {raw!r} (expected 'gemini' or 'ollama')"
            ) from None
        return cls(
            provider=provider,
            gemini_model=str(cfg.get("gemini_model") or DEFAULT_GEMINI_MODEL),
            ollama_model=str(cfg.get("ollama_model") or DEFAULT_OLLAMA_MODEL),
            ollama_base_url=str(cfg.get("ollama_base_url") or DEFAULT_OLLAMA_URL),
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
        (Gemini-style JSON-schema shape); `response_schema` forces structured
        output (Gemini responseSchema / Ollama format). Blocking HTTP runs in a
        thread so the caller's loop never stalls."""
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

    if settings.provider is Provider.GEMINI:
        return GeminiAdapter(settings, api_key=api_key, post=post)
    if settings.provider is Provider.OLLAMA:
        return OllamaAdapter(settings, post=post)
    raise GatewayError(f"no adapter for provider {settings.provider!r}")


__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OLLAMA_URL",
    "Gateway",
    "GatewayError",
    "GatewaySettings",
    "Message",
    "Post",
    "Provider",
    "Response",
    "ToolResultLike",
    "build_gateway",
    "urllib_post",
]
