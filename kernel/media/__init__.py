"""kernel/media — Phase P4: media control via MCP.

Provides media playback control (play, pause, next, previous, volume)
as MCP-compatible tools that can be mounted via the kernel's MCP client.
"""

from kernel.media.controller import MediaController

__all__ = ["MediaController"]
