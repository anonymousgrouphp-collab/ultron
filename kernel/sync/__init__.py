"""kernel/sync — Phase S2: multi-device synchronization.

Provides cross-device context sharing and state sync.
"""

from kernel.sync.context import CrossDeviceContext

__all__ = ["CrossDeviceContext"]
