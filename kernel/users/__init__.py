"""kernel/users — Phase S1: multi-user support.

Provides user profiles, preferences, and per-user memory isolation.
"""

from kernel.users.manager import UserManager

__all__ = ["UserManager"]
