"""kernel/voice/speaker.py — Phase P1: speaker identification for live sessions.

Wraps the SpeakerIdEngine contract to provide:
1. Speaker enrollment (learn a new voice)
2. Speaker identification (who's talking)
3. Per-speaker memory profiles
4. Personalized greetings

This is the app-layer wiring for the P4-B voice contracts — it makes
speaker identification usable from the live voice session.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

from kernel.voice.engines import EngineUnavailable, load_speechbrain

log = logging.getLogger(__name__)

__all__ = ["SpeakerManager", "SpeakerProfile"]


@dataclass
class SpeakerProfile:
    """A known speaker's profile."""

    name: str
    enrollments: int = 0
    last_seen: float = 0.0
    preferences: dict[str, Any] = field(default_factory=dict)


@dataclass
class SpeakerManager:
    """Manages speaker identification for the live voice session.

    Usage::

        manager = SpeakerManager(data_dir=Path(".ultron/speakers"))
        manager.enroll("Fatih", audio_samples)
        speaker = manager.identify(audio_chunk)
        if speaker:
            print(f"Speaking: {speaker.name}")
    """

    data_dir: Path | None = None
    _engine: Any = None
    _profiles: dict[str, SpeakerProfile] = field(default_factory=dict)
    _threshold: float = 0.75

    def __post_init__(self) -> None:
        if self.data_dir:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            self._load_profiles()

    def _load_profiles(self) -> None:
        """Load speaker profiles from disk."""
        if not self.data_dir:
            return
        profile_file = self.data_dir / "profiles.json"
        if profile_file.exists():
            try:
                import json
                data = json.loads(profile_file.read_text())
                for name, info in data.items():
                    self._profiles[name] = SpeakerProfile(
                        name=name,
                        enrollments=info.get("enrollments", 0),
                        last_seen=info.get("last_seen", 0.0),
                        preferences=info.get("preferences", {}),
                    )
            except Exception as exc:
                log.warning("Failed to load speaker profiles: %s", exc)

    def _save_profiles(self) -> None:
        """Save speaker profiles to disk."""
        if not self.data_dir:
            return
        try:
            import json
            profile_file = self.data_dir / "profiles.json"
            data = {}
            for name, profile in self._profiles.items():
                data[name] = {
                    "enrollments": profile.enrollments,
                    "last_seen": profile.last_seen,
                    "preferences": profile.preferences,
                }
            profile_file.write_text(json.dumps(data, indent=2))
        except Exception as exc:
            log.warning("Failed to save speaker profiles: %s", exc)

    def _ensure_engine(self) -> None:
        """Lazily load the SpeechBrain speaker ID engine."""
        if self._engine is None:
            try:
                self._engine = load_speechbrain()
            except EngineUnavailable:
                raise

    def enroll(self, name: str, audio: np.ndarray) -> bool:
        """Enroll a new speaker with audio samples.

        Returns True if enrollment succeeded.
        """
        try:
            self._ensure_engine()
        except EngineUnavailable:
            log.warning("Speaker enrollment unavailable: SpeechBrain not installed")
            return False

        try:
            self._engine.enroll(name, audio)
            if name not in self._profiles:
                self._profiles[name] = SpeakerProfile(name=name)
            self._profiles[name].enrollments += 1
            self._save_profiles()
            return True
        except Exception as exc:
            log.error(f"Enrollment failed for {name}: {exc}")
            return False

    def identify(self, audio: np.ndarray) -> SpeakerProfile | None:
        """Identify a speaker from audio.

        Returns the matching SpeakerProfile or None if unknown.
        """
        try:
            self._ensure_engine()
        except EngineUnavailable:
            return None

        try:
            import time
            name = self._engine.identify(audio)
            if name and name in self._profiles:
                self._profiles[name].last_seen = time.time()
                self._save_profiles()
                return self._profiles[name]
            return None
        except Exception as exc:
            log.warning("Speaker identification error: %s", exc)
            return None

    def get_profile(self, name: str) -> SpeakerProfile | None:
        """Get a speaker's profile by name."""
        return self._profiles.get(name)

    def list_speakers(self) -> list[str]:
        """List all enrolled speaker names."""
        return list(self._profiles.keys())

    def get_greeting(self, name: str | None) -> str:
        """Generate a personalized greeting based on the speaker."""
        if name is None:
            return "Hello, sir."
        profile = self._profiles.get(name)
        if profile and profile.preferences.get("greeting"):
            return profile.preferences["greeting"]
        return f"Good to see you, {name}."
