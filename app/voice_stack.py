"""app/voice_stack.py — the kernel voice engines, wired (Phase P1).

The kernel built the designed voice stack in P4-B (EchoGate, wake-word /
VAD / speaker-ID protocols with lazy loaders). This mixin wires it into
the live product BEHIND CONFIG FLAGS, default off, so the proven audio
path is untouched until a live-mic A/B validates the gate:

- echo_gate_enabled: EchoGate replaces the ad-hoc speaking-lock on the mic
  path (buffers gated frames, opens on loud barge-in). Requires no new
  dependency — pure numpy.
- speaker_id_enabled: the SpeechBrain engine identifies the speaker per
  turn and auto-switches the active user profile (P2 integration).
  Requires requirements-voice.txt; degrades to a logged no-op.

The wake-word lane: openwakeword (in-process) replaces wake_service.py per
the Kill List once installed + A/B'd; the launcher stays until then.
"""

from __future__ import annotations

import numpy as np

from config import loader
from kernel.voice import EchoGate


class VoiceStackMixin:
    """Expects the host to provide ui, _users (P2), and the audio path."""

    _voice_gate: EchoGate | None = None
    _speaker_engine: object | None = None

    def _setup_voice_stack(self) -> None:
        """Phase P1: build the voice engines the config asks for. Everything
        here is best-effort — a missing optional package logs and continues."""
        cfg = loader.load_config()
        if cfg.get("echo_gate_enabled", False):
            self._voice_gate = EchoGate()
            self.ui.write_log("SYS: EchoGate armed (echo_gate_enabled=true).")
        if cfg.get("speaker_id_enabled", False):
            try:
                from kernel.voice.engines import load_speechbrain
                self._speaker_engine = load_speechbrain()
                self.ui.write_log("SYS: Speaker ID armed (speaker_id_enabled=true).")
            except Exception as exc:
                self._speaker_engine = None
                self.ui.write_log(
                    f"SYS: Speaker ID unavailable ({exc}) — install "
                    "requirements-voice.txt to enable it.")

    def gate_mic_frame(self, frame: np.ndarray) -> np.ndarray | None:
        """One mic frame through the EchoGate when armed; identity when not.
        The audio callback swaps its speaking-lock check for this call."""
        if self._voice_gate is None:
            return frame
        return self._voice_gate.process(frame)

    def note_tts_started(self) -> None:
        if self._voice_gate is not None:
            self._voice_gate.tts_started()

    def note_tts_finished(self) -> None:
        if self._voice_gate is not None:
            self._voice_gate.tts_finished()

    def identify_speaker(self, audio: np.ndarray) -> str | None:
        """Phase P1×P2: identify the speaker and switch the active profile.
        Returns the user id when a switch/confirmation happened."""
        if self._speaker_engine is None or self._users is None:
            return None
        try:
            name = self._speaker_engine.identify(audio)  # engine contract
        except Exception:
            return None
        if not name:
            return None
        profile = self._users.get_user_by_speaker(name)
        if profile is not None and profile.user_id != self._active_user_id():
            self._users.set_current_user(profile.user_id)
            self.ui.write_log(f"SYS: Speaker {name} — active user {profile.display_name}.")
            return profile.user_id
        return None
