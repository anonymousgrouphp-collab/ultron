"""kernel/users/manager.py — Phase S1: multi-user support.

Manages user profiles, preferences, and per-user memory isolation.
Each user gets:
1. Their own memory space (facts, procedures, session summaries)
2. Personalized persona preferences
3. Speaker identification binding
4. Permission levels (admin, user, guest)
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

__all__ = ["UserManager", "UserProfile"]


@dataclass
class UserProfile:
    """A user's profile and preferences."""

    user_id: str
    display_name: str
    role: str = "user"  # admin, user, guest
    preferences: dict[str, Any] = field(default_factory=dict)
    speaker_name: str | None = None  # bound to speaker ID
    created_at: float = field(default_factory=time.time)
    last_seen: float = 0.0


@dataclass
class UserManager:
    """Manages user profiles and switching.

    Parameters
    ----------
    data_dir:
        Directory to store user profiles (default: .ultron/users/).
    """

    data_dir: Path | None = None
    _profiles: dict[str, UserProfile] = field(default_factory=dict)
    _current_user: str | None = None

    def __post_init__(self) -> None:
        if self.data_dir:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._load_profiles()

    def _load_profiles(self) -> None:
        """Load user profiles from disk."""
        if not self.data_dir:
            return
        profiles_file = self.data_dir / "profiles.json"
        if profiles_file.exists():
            try:
                data = json.loads(profiles_file.read_text())
                for uid, info in data.items():
                    self._profiles[uid] = UserProfile(
                        user_id=uid,
                        display_name=info.get("display_name", uid),
                        role=info.get("role", "user"),
                        preferences=info.get("preferences", {}),
                        speaker_name=info.get("speaker_name"),
                        created_at=info.get("created_at", 0),
                        last_seen=info.get("last_seen", 0),
                    )
            except Exception as exc:
                log.warning("Failed to load user profiles from disk: %s", exc)

    def _save_profiles(self) -> None:
        """Save user profiles to disk."""
        if not self.data_dir:
            return
        try:
            profiles_file = self.data_dir / "profiles.json"
            data = {}
            for uid, profile in self._profiles.items():
                data[uid] = {
                    "display_name": profile.display_name,
                    "role": profile.role,
                    "preferences": profile.preferences,
                    "speaker_name": profile.speaker_name,
                    "created_at": profile.created_at,
                    "last_seen": profile.last_seen,
                }
            profiles_file.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            log.warning("Failed to save user profiles to disk: %s", exc)

    def create_user(self, user_id: str, display_name: str, role: str = "user") -> UserProfile:
        """Create a new user profile."""
        profile = UserProfile(
            user_id=user_id,
            display_name=display_name,
            role=role,
        )
        self._profiles[user_id] = profile
        self._save_profiles()
        return profile

    def get_user(self, user_id: str) -> UserProfile | None:
        """Get a user profile by ID."""
        return self._profiles.get(user_id)

    def set_current_user(self, user_id: str) -> bool:
        """Set the current active user.

        Returns True if the user exists and was set.
        """
        if user_id in self._profiles:
            self._current_user = user_id
            self._profiles[user_id].last_seen = time.time()
            self._save_profiles()
            return True
        return False

    def get_current_user(self) -> UserProfile | None:
        """Get the current active user profile."""
        if self._current_user:
            return self._profiles.get(self._current_user)
        return None

    def list_users(self) -> list[UserProfile]:
        """List all user profiles."""
        return list(self._profiles.values())

    def bind_speaker(self, user_id: str, speaker_name: str) -> bool:
        """Bind a speaker ID to a user profile."""
        if user_id in self._profiles:
            self._profiles[user_id].speaker_name = speaker_name
            self._save_profiles()
            return True
        return False

    def get_user_by_speaker(self, speaker_name: str) -> UserProfile | None:
        """Find a user by their speaker ID binding."""
        for profile in self._profiles.values():
            if profile.speaker_name == speaker_name:
                return profile
        return None

    def delete_user(self, user_id: str) -> bool:
        """Delete a user profile."""
        if user_id in self._profiles:
            del self._profiles[user_id]
            if self._current_user == user_id:
                self._current_user = None
            self._save_profiles()
            return True
        return False
