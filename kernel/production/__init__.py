"""kernel/production — Phase S4: production hardening.

Provides error handling, structured logging, and operational utilities.
"""

from kernel.production.error_handler import ErrorHandler
from kernel.production.logger import StructuredLogger

__all__ = ["ErrorHandler", "StructuredLogger"]
