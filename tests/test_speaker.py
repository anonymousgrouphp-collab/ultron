from pathlib import Path
from unittest.mock import MagicMock

import numpy as np

from kernel.voice.engines import EngineUnavailable
from kernel.voice.speaker import SpeakerManager, SpeakerProfile


def test_speaker_profile_defaults():
    profile = SpeakerProfile(name="Alice")
    assert profile.name == "Alice"
    assert profile.enrollments == 0
    assert profile.last_seen == 0.0
    assert profile.preferences == {}


def test_speaker_manager_load_and_save(tmp_path: Path):
    data_dir = tmp_path / "speakers"
    manager = SpeakerManager(data_dir=data_dir)

    assert manager.list_speakers() == []
    profile = SpeakerProfile(name="Bob", enrollments=2, last_seen=123.0, preferences={"greeting": "Welcome back, Bob."})
    manager._profiles["Bob"] = profile
    manager._save_profiles()

    # Re-instantiate to test loading from disk
    manager2 = SpeakerManager(data_dir=data_dir)
    assert manager2.list_speakers() == ["Bob"]
    bob = manager2.get_profile("Bob")
    assert bob is not None
    assert bob.enrollments == 2
    assert bob.preferences["greeting"] == "Welcome back, Bob."


def test_speaker_manager_greetings(tmp_path: Path):
    manager = SpeakerManager(data_dir=tmp_path)
    # None speaker
    assert manager.get_greeting(None) == "Hello, sir."
    # Unknown speaker
    assert manager.get_greeting("Charlie") == "Good to see you, Charlie."
    # Custom greeting in preferences
    manager._profiles["Charlie"] = SpeakerProfile(
        name="Charlie", preferences={"greeting": "Greetings, Captain Charlie!"}
    )
    assert manager.get_greeting("Charlie") == "Greetings, Captain Charlie!"


def test_speaker_manager_enroll_and_identify(tmp_path: Path):
    manager = SpeakerManager(data_dir=tmp_path)
    mock_engine = MagicMock()
    manager._engine = mock_engine

    mock_audio = np.zeros(1600, dtype=np.float32)

    # Enroll
    success = manager.enroll("Diana", mock_audio)
    assert success is True
    mock_engine.enroll.assert_called_once_with("Diana", mock_audio)
    diana = manager.get_profile("Diana")
    assert diana is not None
    assert diana.enrollments == 1

    # Identify match
    mock_engine.identify.return_value = "Diana"
    identified = manager.identify(mock_audio)
    assert identified is not None
    assert identified.name == "Diana"
    assert identified.last_seen > 0

    # Identify unknown
    mock_engine.identify.return_value = "Unknown"
    unknown = manager.identify(mock_audio)
    assert unknown is None


def test_speaker_manager_engine_unavailable(tmp_path: Path, monkeypatch):
    manager = SpeakerManager(data_dir=tmp_path)

    def mock_load():
        raise EngineUnavailable("speechbrain", "speechbrain")

    monkeypatch.setattr("kernel.voice.speaker.load_speechbrain", mock_load)

    mock_audio = np.zeros(1600, dtype=np.float32)
    assert manager.enroll("Eve", mock_audio) is False
    assert manager.identify(mock_audio) is None
