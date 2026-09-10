"""kernel/production/error_handler.py — Phase S4: error handling and recovery.

Provides graceful degradation and error recovery for the ULTRON harness:
1. Structured error classification (transient, permanent, recoverable)
2. Automatic retry with backoff for transient errors
3. Graceful degradation when components fail
4. Error reporting to the health monitor
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

log = logging.getLogger(__name__)

__all__ = ["ErrorHandler", "ErrorSeverity", "RecoveryAction"]


class ErrorSeverity(str, Enum):
    """Error severity levels."""

    LOW = "low"           # Minor issue, no user impact
    MEDIUM = "medium"     # Issue that degrades functionality
    HIGH = "high"         # Issue that breaks a feature
    CRITICAL = "critical" # Issue that breaks the entire system


class RecoveryAction(str, Enum):
    """Recovery actions for different error types."""

    RETRY = "retry"              # Retry the operation
    FALLBACK = "fallback"        # Use a fallback implementation
    SKIP = "skip"                # Skip the failed operation
    DEGRADE = "degrade"          # Degrade gracefully
    RESTART = "restart"          # Restart the affected component
    ALERT = "alert"              # Alert the user


@dataclass
class ErrorRecord:
    """A recorded error."""

    component: str
    error_type: str
    message: str
    severity: ErrorSeverity
    recovery: RecoveryAction
    timestamp: float = field(default_factory=time.time)
    retry_count: int = 0


@dataclass
class ErrorHandler:
    """Handles errors with graceful degradation.

    Usage::

        handler = ErrorHandler()
        handler.register_recovery("gateway", RecoveryAction.RETRY, max_retries=3)
        result = handler.safe_execute("gateway", some_function, fallback_value="default")
    """

    _records: list[ErrorRecord] = field(default_factory=list)
    _recovery_configs: dict[str, dict[str, Any]] = field(default_factory=dict)

    def register_recovery(
        self,
        component: str,
        action: RecoveryAction,
        max_retries: int = 3,
        backoff_s: float = 1.0,
        fallback_value: Any = None,
    ) -> None:
        """Register a recovery strategy for a component."""
        self._recovery_configs[component] = {
            "action": action,
            "max_retries": max_retries,
            "backoff_s": backoff_s,
            "fallback_value": fallback_value,
        }

    def handle_error(
        self,
        component: str,
        error: Exception,
        severity: ErrorSeverity = ErrorSeverity.MEDIUM,
    ) -> RecoveryAction:
        """Handle an error and determine the recovery action.

        Returns the recovery action to take.
        """
        config = self._recovery_configs.get(component, {})
        action = config.get("action", RecoveryAction.SKIP)

        record = ErrorRecord(
            component=component,
            error_type=type(error).__name__,
            message=str(error)[:200],
            severity=severity,
            recovery=action,
        )
        self._records.append(record)

        log.warning(f"[{component}] {severity.value}: {error} → {action.value}")

        return action

    def safe_execute(
        self,
        component: str,
        func: Callable,
        *args: Any,
        fallback_value: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Execute a function with error handling and retry.

        Returns the function result or fallback_value on failure.
        """
        config = self._recovery_configs.get(component, {})
        max_retries = config.get("max_retries", 1)
        backoff = config.get("backoff_s", 1.0)

        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                self.handle_error(component, exc)
                if attempt < max_retries - 1:
                    time.sleep(backoff * (2 ** attempt))  # Exponential backoff

        return fallback_value

    async def safe_execute_async(
        self,
        component: str,
        func: Callable,
        *args: Any,
        fallback_value: Any = None,
        **kwargs: Any,
    ) -> Any:
        """Execute an async function with error handling and retry."""
        import asyncio

        config = self._recovery_configs.get(component, {})
        max_retries = config.get("max_retries", 1)
        backoff = config.get("backoff_s", 1.0)

        for attempt in range(max_retries):
            try:
                return await func(*args, **kwargs)
            except Exception as exc:
                self.handle_error(component, exc)
                if attempt < max_retries - 1:
                    await asyncio.sleep(backoff * (2 ** attempt))

        return fallback_value

    def get_error_summary(self) -> dict[str, int]:
        """Get error counts by component."""
        summary: dict[str, int] = {}
        for record in self._records:
            summary[record.component] = summary.get(record.component, 0) + 1
        return summary

    def get_recent_errors(self, n: int = 10) -> list[ErrorRecord]:
        """Get the N most recent errors."""
        return self._records[-n:]
