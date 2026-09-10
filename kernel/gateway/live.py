"""kernel/gateway/live.py — Gemini Live audio session wrapper.

Wraps the ``google.genai`` SDK so that main.py (and only main.py) imports
it from the gateway rather than directly.  Model strings, API key resolution,
and session configuration live here — the rest of the codebase never sees
the SDK.

Design notes
------------
- The Live (real-time audio) protocol is Gemini-specific; no other provider
  has an equivalent, so this is NOT behind the generic ``Gateway.complete``
  interface — it is a separate adapter in the same package.
- Config building (``LiveConnectConfig``) is centralized here so that voice
  personality, memory injection, tool declarations, and speech settings are
  all in one place instead of scattered across main.py helpers.
- The ``LiveSession`` is an async context manager: ``async with`` gives you
  a connected session; exiting closes it cleanly.
- Kill List compliance: the ONLY place ``from google import genai`` appears
  (outside of a comment/docstring) is this module.
"""

from __future__ import annotations

from typing import Any

from kernel.gateway.base import (
    DEFAULT_GEMINI_LIVE_MODEL,
    GatewayError,
    GatewaySettings,
)

# ---------------------------------------------------------------------------
# SDK import — the ONLY ``from google import genai`` in the entire codebase
# (Kill List #3 / #4 compliance; enforced by evals/killlist_check.py)
# ---------------------------------------------------------------------------
try:
    from google import genai  # type: ignore[import-untyped]
    from google.genai import types  # type: ignore[import-untyped]
    from google.genai.types import FunctionResponse  # type: ignore[import-untyped]

    _SDK_AVAILABLE = True
except ImportError:
    genai = None  # type: ignore[assignment]
    types = None  # type: ignore[assignment]
    FunctionResponse = None  # type: ignore[assignment,misc]
    _SDK_AVAILABLE = False


def _require_sdk() -> None:
    if not _SDK_AVAILABLE:
        raise GatewayError(
            "google-genai SDK not installed — "
            "pip install google-genai"
        )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

class LiveSession:
    """Managed Gemini Live audio session.

    Usage::

        settings = GatewaySettings.from_config(cfg)
        async with LiveSession(settings, api_key=key) as session:
            await session.connect(config=build_voice_config(...))
            await session.send_realtime({"data": audio, "mime_type": ...})
            response = session.session  # raw SDK session for receive
    """

    def __init__(
        self,
        settings: GatewaySettings,
        api_key: str | None = None,
    ) -> None:
        _require_sdk()
        self._settings = settings
        self._api_key = api_key
        self._client: Any = None
        self._session: Any = None

    @property
    def model(self) -> str:
        """The Live model string — from gateway config, never hardcoded."""
        return DEFAULT_GEMINI_LIVE_MODEL

    @property
    def session(self) -> Any:
        """The raw SDK session object (for receive_audio, send_realtime)."""
        if self._session is None:
            raise GatewayError("LiveSession not connected — use async with")
        return self._session

    async def connect(self, config: Any) -> None:
        """Create the SDK client and open the Live session."""
        _require_sdk()
        self._client = genai.Client(
            api_key=self._api_key,
            http_options={"api_version": "v1beta"},
        )
        self._session_ctx = self._client.aio.live.connect(
            model=self.model,
            config=config,
        )
        self._session = await self._session_ctx.__aenter__()

    async def close(self) -> None:
        """Close the session and client cleanly."""
        if hasattr(self, "_session_ctx") and self._session_ctx is not None:
            try:
                await self._session_ctx.__aexit__(None, None, None)
            except Exception:
                pass
            self._session_ctx = None
        self._session = None
        self._client = None

    async def __aenter__(self) -> "LiveSession":
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.close()


# ---------------------------------------------------------------------------
# Config builder — centralizes voice/personality/memory/tool configuration
# ---------------------------------------------------------------------------

def build_live_config(
    *,
    system_prompt: str,
    tool_declarations: list[dict[str, Any]],
    voice_name: str = "Charon",
    response_modalities: list[str] | None = None,
    enable_audio_transcription: bool = True,
    session_resumption: bool = True,
) -> Any:
    """Build a ``types.LiveConnectConfig`` for the Gemini Live session.

    This replaces the scattered ``_build_config`` method that was in main.py,
    centralizing the SDK type construction in the gateway package.

    Parameters
    ----------
    system_prompt:
        The full system instruction (identity + time + memory + personality).
    tool_declarations:
        Tool declarations (from ``core/tool_declarations.TOOL_DECLARATIONS``).
    voice_name:
        Prebuilt voice name (default ``"Charon"``).
    response_modalities:
        Response modalities (default ``["AUDIO"]``).
    enable_audio_transcription:
        Enable input/output audio transcription.
    session_resumption:
        Enable session resumption config.
    """
    _require_sdk()
    if response_modalities is None:
        response_modalities = ["AUDIO"]

    speech_config = types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=voice_name,
            )
        )
    )

    config_kwargs: dict[str, Any] = {
        "response_modalities": response_modalities,
        "system_instruction": system_prompt,
        "tools": [{"function_declarations": tool_declarations}],
        "speech_config": speech_config,
    }

    if enable_audio_transcription:
        config_kwargs["output_audio_transcription"] = {}
        config_kwargs["input_audio_transcription"] = {}

    if session_resumption:
        config_kwargs["session_resumption"] = types.SessionResumptionConfig()

    return types.LiveConnectConfig(**config_kwargs)


__all__ = [
    "FunctionResponse",
    "LiveSession",
    "build_live_config",
]
