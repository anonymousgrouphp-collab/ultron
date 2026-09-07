"""kernel/notify — P2-F: outbound notification channels (ntfy + Telegram).

Both senders are fire-and-forget-safe: they return False on ANY failure and
never raise — a notification outage must not break a briefing. The HTTP post
is injectable for tests; keys/tokens are passed in BY THE WIRING LAYER (from
config.loader) — the kernel never reads config.
"""

from kernel.notify.notify import send_briefing, send_ntfy, send_telegram

__all__ = ["send_briefing", "send_ntfy", "send_telegram"]
