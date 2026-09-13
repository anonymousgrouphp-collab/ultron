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

Local TTS lane (research adopt A1, docs/research/10_tts_research.md):
- tts_backend: none (default, zero change) | kokoro | piper.
- speak() falls back to local synthesis when the Live session is absent
  (today that path is mute); tts_prefer_local=true forces local even with
  Live up. Speech reuses audio_in_queue when a live player exists, else a
  one-shot sounddevice stream; barge-in drains/stop-event cover both.
"""

from __future__ import annotations

import asyncio
import threading

import numpy as np

from config import loader
from kernel.voice import EchoGate


class VoiceStackMixin:
    """Expects the host to provide ui, _users (P2), and the audio path."""

    _voice_gate: EchoGate | None = None
    _speaker_engine: object | None = None
    _tts = None  # TtsEngine | None (kernel/voice/tts.py)
    _tts_prefer_local: bool = False
    _tts_stop: threading.Event | None = None
    _tts_busy = threading.Lock()
    _stt = None        # SttEngine | None (kernel/voice/stt.py, report 11 S1)
    _local_voice = None  # LocalVoiceLoop | None (app/local_voice.py)

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
        self._setup_tts(cfg)
        self._setup_stt(cfg)

    def _setup_stt(self, cfg: dict) -> None:
        """Local STT + offline voice loop (research adopt S1, report 11).
        stt_backend: none (default, zero change) | faster_whisper. The loop
        only ever runs while the Live session is down (sync_local_voice), so
        arming it never touches the proven audio path."""
        backend = (cfg.get("stt_backend") or "none").strip().lower()
        if backend in ("", "none", "off"):
            return
        try:
            from kernel.voice import stt as stt_mod

            engine = stt_mod.load_from_config(cfg)
        except Exception as exc:
            self._stt = None
            self.ui.write_log(
                f"SYS: Local STT unavailable ({exc}) — install "
                "requirements-voice.txt to enable offline ears.")
            return
        try:
            from kernel.voice.engines import load_silero_vad

            vad = load_silero_vad()
        except Exception as exc:
            self._stt = None
            self.ui.write_log(
                f"SYS: Local STT unavailable ({exc}) — the voice loop needs "
                "Silero VAD (requirements-voice.txt).")
            return
        try:
            from app.local_voice import LocalVoiceConfig, LocalVoiceLoop

            self._stt = engine
            self._local_voice = LocalVoiceLoop(
                host=self,
                engine=engine,
                vad=vad,
                on_text=self._on_local_voice_text,
                on_partial=self._on_local_voice_partial,
                config=LocalVoiceConfig.from_cfg(cfg),
            )
        except Exception as exc:
            self._stt = None
            self._local_voice = None
            self.ui.write_log(f"SYS: Local voice loop failed to arm ({exc}).")
            return
        self.ui.write_log(
            f"SYS: Local STT armed ({backend}) — offline ears active when "
            "Live is down.")

    def _on_local_voice_text(self, text: str) -> None:
        """Final offline utterance → the ONE text router (both tiers)."""
        clean = str(text or "").strip()
        if not clean:
            return
        self.ui.write_log(f"You: {clean}")
        self._on_text_command(clean)

    def _on_local_voice_partial(self, text: str) -> None:
        """Stabilized partial caption from the offline loop (S2)."""
        clean = str(text or "").strip()
        if clean:
            self.ui.write_log(f"You: {clean} …")

    def sync_local_voice(self) -> None:
        """Start/stop the offline loop to match session liveness. Live up →
        the session owns the mic; Live down → the local loop is the ears."""
        loop = self._local_voice
        if loop is None:
            return
        should_run = self.session is None
        try:
            if should_run and not loop.running:
                loop.start()
                if loop.running:
                    self.ui.write_log("SYS: Offline voice loop active (listening locally).")
            elif not should_run and loop.running:
                loop.stop()
        except Exception as exc:
            self.ui.write_log(f"SYS: Local voice loop sync failed: {exc}")

    def _setup_tts(self, cfg: dict) -> None:
        """Local TTS engine (tts_backend: kokoro|piper). Default none = the
        Live session keeps speaking; a broken engine logs and never blocks."""
        backend = (cfg.get("tts_backend") or "none").strip().lower()
        if backend in ("", "none", "off"):
            return
        try:
            from kernel.voice import tts as tts_mod
            self._tts = tts_mod.load_from_config(cfg)
            self._tts_prefer_local = bool(cfg.get("tts_prefer_local", False))
            self._tts_stop = threading.Event()
            voice = cfg.get("tts_voice") or backend
            self.ui.write_log(
                f"SYS: Local TTS armed ({backend}, voice={voice}, "
                f"prefer_local={self._tts_prefer_local}).")
        except Exception as exc:
            self._tts = None
            self.ui.write_log(
                f"SYS: Local TTS unavailable ({exc}) — install "
                "requirements-tts.txt and run the voice download to enable it.")

    def tts_should_speak(self) -> bool:
        """True when speak() should use the local engine instead of the cloud
        session: engine armed AND (no session OR prefer_local)."""
        return self._tts is not None and (
            self.session is None or self._tts_prefer_local
        )

    def speak_local(self, text: str) -> None:
        """Non-blocking local synthesis: splits text, queues 24 kHz int16 PCM
        into audio_in_queue when a live player exists, else plays directly."""
        if self._tts is None or not text.strip():
            return
        threading.Thread(target=self._tts_worker, args=(text,), daemon=True).start()

    def _tts_worker(self, text: str) -> None:
        if not self._tts_busy.acquire(blocking=False):
            return  # a previous utterance is still speaking; drop new one
        self._tts_stop.clear()
        self.note_tts_started()
        try:
            from kernel.voice import tts as tts_mod

            target_rate = getattr(self, "RECEIVE_SAMPLE_RATE", 24000)
            queued = False
            for chunk in self._tts.synthesize(text):
                if self._tts_stop.is_set():
                    break
                pcm = tts_mod.resample_linear(
                    chunk.audio_int16, chunk.sample_rate, target_rate
                )
                data = pcm.tobytes()
                queue = getattr(self, "audio_in_queue", None)
                loop = getattr(self, "_loop", None)
                if queue is not None and loop is not None:
                    queued = True
                    asyncio.run_coroutine_threadsafe(
                        queue.put(data), loop
                    ).result(timeout=5)
                else:
                    self._play_direct(pcm, target_rate)
            if queued:
                # let _play_audio finish the tail before unblocking EchoGate
                import time

                time.sleep(0.15)
        except Exception as exc:
            try:
                self.ui.write_log(f"SYS: local TTS failed: {exc}")
            except Exception:
                pass
        finally:
            self.note_tts_finished()
            self._tts_busy.release()

    def _play_direct(self, pcm: np.ndarray, rate: int) -> None:
        """Fallback playback when no live player exists (Live session down)."""
        import sounddevice as sd

        with sd.RawOutputStream(
            samplerate=rate, channels=1, dtype="int16", blocksize=0
        ) as stream:
            stream.start()
            # write in ~100 ms slices so stop is responsive
            slice_len = rate // 10
            data = memoryview(pcm.tobytes())
            step = slice_len * 2  # int16 = 2 bytes
            for off in range(0, len(data), step):
                if self._tts_stop.is_set():
                    break
                stream.write(bytes(data[off : off + step]))

    def stop_local_speech(self) -> None:
        """Barge-in hook for the local TTS lane (interrupt() calls this)."""
        if self._tts_stop is not None:
            self._tts_stop.set()

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
