"""kernel/diagnostics — Phase Q3: self-diagnostics and health monitoring.

Provides health monitoring, automatic recovery, and performance profiling
for the ULTRON harness.
"""

from kernel.diagnostics.health import HealthMonitor

__all__ = ["HealthMonitor"]
