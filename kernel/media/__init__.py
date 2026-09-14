"""kernel/media — Phase P4 + PJ-01: media & brightness control.

Media playback (play/pause/next/previous/volume) + display brightness —
kernel tools via kernel/media/tools.py (the ONE registration path).
"""

from kernel.media.controller import MediaController
from kernel.media.tools import build_media_tools

__all__ = ["MediaController", "build_media_tools"]
