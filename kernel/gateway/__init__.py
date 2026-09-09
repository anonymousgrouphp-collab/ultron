"""kernel/gateway — P1-C: the one model gateway (Gemini + Ollama adapters).

The agent loop (P1-G) programs against :class:`Gateway.complete` and reads
neutral :class:`Response` records; provider payloads never leave this package.
Model strings live only here (Kill List #3).
"""

from kernel.gateway.base import (
    DEFAULT_GEMINI_MODEL,
    DEFAULT_OLLAMA_MODEL,
    DEFAULT_OLLAMA_URL,
    DEFAULT_OPENAI_MODEL,
    DEFAULT_OPENAI_URL,
    Gateway,
    GatewayError,
    GatewaySettings,
    Message,
    Post,
    Provider,
    Response,
    ToolResultLike,
    build_gateway,
    urllib_post,
)
from kernel.gateway.gemini import GeminiAdapter
from kernel.gateway.ollama import OllamaAdapter
from kernel.gateway.openai import OpenAIChatAdapter

__all__ = [
    "DEFAULT_GEMINI_MODEL",
    "DEFAULT_OLLAMA_MODEL",
    "DEFAULT_OLLAMA_URL",
    "DEFAULT_OPENAI_MODEL",
    "DEFAULT_OPENAI_URL",
    "Gateway",
    "GatewayError",
    "GatewaySettings",
    "GeminiAdapter",
    "Message",
    "OllamaAdapter",
    "OpenAIChatAdapter",
    "Post",
    "Provider",
    "Response",
    "ToolResultLike",
    "build_gateway",
    "urllib_post",
]
