"""kernel/notify/notify.py — P2-F: ntfy.sh + Telegram senders (injected HTTP)."""

from __future__ import annotations

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["post_default", "send_briefing", "send_ntfy", "send_telegram"]

DEFAULT_TIMEOUT_S = 10.0

# (url, body_bytes, headers) -> (status, response_body)
Post = Callable[[str, bytes, dict[str, str]], "tuple[int, str]"]


def post_default(url: str, body: bytes, headers: dict[str, str]) -> tuple[int, str]:
    request = urllib.request.Request(url, data=body, headers=headers)
    with urllib.request.urlopen(request, timeout=DEFAULT_TIMEOUT_S) as response:
        return response.status, response.read().decode("utf-8", errors="replace")


def send_ntfy(topic: str, message: str, *, title: str | None = None,
              server: str = "https://ntfy.sh", post: Post | None = None) -> bool:
    """Post a message to an ntfy topic. False on any failure — never raises."""
    if not topic:
        return False
    do_post = post or post_default
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if title:
        headers["Title"] = title
    try:
        status, _ = do_post(f"{server.rstrip('/')}/{topic}",
                            message.encode("utf-8"), headers)
        return 200 <= status < 300
    except Exception:  # noqa: BLE001 — notifications must never break callers
        log.exception("ntfy send failed")
        return False


def send_telegram(token: str, chat_id: str, text: str,
                  *, post: Post | None = None) -> bool:
    """Send a message via the Telegram Bot API. False on any failure."""
    if not token or not chat_id:
        return False
    do_post = post or post_default
    body = json.dumps({"chat_id": chat_id, "text": text}).encode("utf-8")
    try:
        status, _ = do_post(f"https://api.telegram.org/bot{token}/sendMessage",
                            body, {"Content-Type": "application/json"})
        return 200 <= status < 300
    except Exception:  # noqa: BLE001
        log.exception("telegram send failed")
        return False


def send_briefing(text: str, *, ntfy_topic: str | None = None,
                  telegram_token: str | None = None,
                  telegram_chat_id: str | None = None,
                  title: str = "ULTRON briefing",
                  post: Post | None = None) -> dict[str, Any]:
    """Deliver a rendered briefing to every configured channel. Returns a
    per-channel status map: True sent / False attempted-but-failed / None
    not configured."""
    result: dict[str, Any] = {"ntfy": None, "telegram": None}
    if ntfy_topic:
        result["ntfy"] = send_ntfy(ntfy_topic, text, title=title, post=post)
    if telegram_token and telegram_chat_id:
        result["telegram"] = send_telegram(
            telegram_token, telegram_chat_id, text, post=post)
    return result
