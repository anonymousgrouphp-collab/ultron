"""actions/_llm.py — R2: the ONLY LLM call path for legacy action modules.

Kill List (ROADMAP §5): *no LLM call outside the gateway, no model name
outside the gateway.* The 8 legacy actions that used the `google.genai` SDK
with hardcoded model strings now call `complete_text` / `complete_json` /
`complete_vision` here — every request is built from the neutral
:class:`kernel.gateway.Message` shape and executed by
:func:`kernel.gateway.build_gateway`, whose settings come from
``GatewaySettings.from_config`` (model strings live only in
``kernel/gateway/base.py`` and user config overrides).

Behavior notes:
- One synchronous completion per call (``asyncio.run`` — the actions are
  sync functions running in worker threads, and the gateway's blocking HTTP
  already runs via ``asyncio.to_thread``).
- Provider is whatever the config says (gemini/ollama/openai). The api key
  is looked up per provider from the config loader — never hardcoded.
- Empty model output raises ``ValueError`` so callers fail loudly instead
  of surfacing blank results.
- Multimodal (image/audio) goes through ``complete_vision`` as
  ``InlineData`` parts — the Gemini adapter renders them; Ollama/OpenAI
  refuse with a clean GatewayError (a multimodal call must never silently
  degrade to text-only).
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, Mapping

from config.loader import get_api_key, load_config
from kernel.gateway import (
    GatewaySettings,
    InlineData,
    Message,
    Provider,
    build_gateway,
)

__all__ = [
    "complete_json",
    "complete_text",
    "complete_vision",
]

# Provider-native tool dicts pass through to the adapter verbatim (Gemini's
# grounded-search tool, {"google_search": {}}, is the supported one — R2).
_GOOGLE_SEARCH_TOOL: tuple[Mapping[str, Any], ...] = ({"google_search": {}},)


def _api_key_for(settings: GatewaySettings) -> str | None:
    """The config key the chosen provider needs (None = ollama, no key)."""
    if settings.provider is Provider.GEMINI:
        return get_api_key("gemini_api_key")
    if settings.provider is Provider.OPENAI:
        return get_api_key("openai_api_key")
    return None


def _build_messages(prompt: str, system: str | None,
                    parts: tuple[InlineData, ...]) -> list[Message]:
    messages: list[Message] = []
    if system:
        messages.append(Message(role="system", text=system))
    messages.append(Message(role="user", text=prompt, parts=parts))
    return messages


def _complete(prompt: str, *, system: str | None = None,
              parts: tuple[InlineData, ...] = (),
              tools: tuple[Mapping[str, Any], ...] = (),
              response_schema: Mapping[str, Any] | None = None) -> str:
    settings = GatewaySettings.from_config(load_config())
    gateway = build_gateway(settings, api_key=_api_key_for(settings))
    response = asyncio.run(gateway.complete(
        _build_messages(prompt, system, parts),
        tools=tools,
        response_schema=response_schema,
    ))
    text = (response.text or "").strip()
    if not text:
        raise ValueError("model returned an empty response")
    return text


def complete_text(prompt: str, *, system: str | None = None,
                  tools: tuple[Mapping[str, Any], ...] = ()) -> str:
    """One plain-text completion. `tools` may carry provider-native tool
    dicts (e.g. `({"google_search": {}},)` for Gemini grounded search)."""
    return _complete(prompt, system=system, tools=tools)


def complete_json(prompt: str, *, system: str | None = None) -> Any:
    """One completion forced to JSON (structured output when the provider
    supports it) and parsed. Markdown fences are stripped first (mirrors the
    legacy actions' defensive parsing). Raises ValueError on unparseable
    output."""
    import re

    text = _complete(
        prompt,
        system=system,
        response_schema={"type": "object"},
    )
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```\s*$", "", text)
    return json.loads(text)


def complete_vision(prompt: str, *, data: bytes,
                    mime_type: str = "image/png",
                    system: str | None = None) -> str:
    """One multimodal completion with inline image/audio bytes. Gemini-only
    (Ollama/OpenAI refuse with a clean error)."""
    return _complete(
        prompt,
        system=system,
        parts=(InlineData(mime_type=mime_type, data=data),),
    )


def complete_grounded_search(prompt: str) -> str:
    """Gemini grounded search (the `google_search` tool) through the
    gateway — web_search's primary backend without a second SDK path."""
    return complete_text(prompt, tools=_GOOGLE_SEARCH_TOOL)
