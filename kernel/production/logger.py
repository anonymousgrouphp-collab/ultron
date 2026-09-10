"""kernel/production/logger.py — Phase S4: structured logging.

Provides JSON-formatted structured logging for production use:
1. Structured log entries with context (component, user, session)
2. Log levels mapped to severity
3. Performance timing logs
4. Audit trail for tool executions
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

__all__ = ["StructuredLogger"]


@dataclass
class StructuredLogger:
    """Structured JSON logger for production.

    Usage::

        logger = StructuredLogger("gateway")
        logger.info("Request completed", model="gemini", tokens=500, latency_ms=120)
        logger.error("Request failed", model="gemini", error="timeout")
    """

    component: str
    _log_level: str = "INFO"

    def _log(self, level: str, message: str, **kwargs: Any) -> None:
        """Log a structured entry."""
        entry = {
            "level": level,
            "component": self.component,
            "message": message,
            "ts": time.time(),
        }
        if kwargs:
            entry["data"] = kwargs

        # Use stdlib logging with JSON format
        logger = logging.getLogger(self.component)
        log_func = getattr(logger, level.lower(), logger.info)
        log_func(json.dumps(entry, default=str))

    def debug(self, message: str, **kwargs: Any) -> None:
        """Log a debug entry."""
        self._log("DEBUG", message, **kwargs)

    def info(self, message: str, **kwargs: Any) -> None:
        """Log an info entry."""
        self._log("INFO", message, **kwargs)

    def warning(self, message: str, **kwargs: Any) -> None:
        """Log a warning entry."""
        self._log("WARNING", message, **kwargs)

    def error(self, message: str, **kwargs: Any) -> None:
        """Log an error entry."""
        self._log("ERROR", message, **kwargs)

    def critical(self, message: str, **kwargs: Any) -> None:
        """Log a critical entry."""
        self._log("CRITICAL", message, **kwargs)

    def timing(self, operation: str, duration_ms: float, **kwargs: Any) -> None:
        """Log a performance timing entry."""
        self._log("INFO", f"timing:{operation}", duration_ms=duration_ms, **kwargs)

    def audit(self, action: str, user: str = "", **kwargs: Any) -> None:
        """Log an audit trail entry."""
        self._log("INFO", f"audit:{action}", user=user, **kwargs)


@dataclass
class TimingContext:
    """Context manager for timing operations.

    Usage::

        with TimingContext("gateway_request", logger) as timing:
            result = await gateway.complete(messages)
        # Automatically logs the timing
    """

    operation: str
    logger: StructuredLogger
    _start: float = 0.0
    _extra: dict[str, Any] = field(default_factory=dict)

    def __enter__(self) -> "TimingContext":
        self._start = time.monotonic()
        return self

    def __exit__(self, *args: Any) -> None:
        duration_ms = (time.monotonic() - self._start) * 1000
        self.logger.timing(self.operation, round(duration_ms, 2), **self._extra)
