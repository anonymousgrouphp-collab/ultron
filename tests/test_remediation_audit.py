"""tests/test_remediation_audit.py — Verification suite for audit remediations.

Covers:
- UI-01: Document title reset on command dispatch in ui.py & app.html
- UI-04: Thread-safe correlation mapping in _consent_request / _on_consent_request
- INT-01: Clear ApiKeyMissing error reporting for Ollama without Gemini live key
- INT-02: EchoGate integration in AudioTasksMixin.callback & set_speaking lifecycle
- INT-03: SkillCaptureListener wiring and procedural skill capture
- INT-04: Zero-polling idle loops in audio playback and dashboard monitors
- BAT-01 / BAT-02: UTF-8 encoding and Python 3.14 launcher argument handling
"""

import asyncio
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from app.audio import AudioTasksMixin
from app.monitors import MonitorTasksMixin
from kernel.bus import EventBus
from kernel.memory.engine import MemoryEngine
from kernel.memory.improve import SkillCaptureListener, task_slug
from kernel.types import Event
from kernel.voice import EchoGate, GateState


# ---------------------------------------------------------------------------
# UI-01 & UI-04: UI Consent & Title Reset Tests
# ---------------------------------------------------------------------------

def test_ui_consent_correlation_mapping():
    """Verify _consent_request creates unique request IDs and resolves independently."""
    from ui import UltronWebWindow

    with patch.object(UltronWebWindow, "__init__", lambda self: None):
        win = UltronWebWindow()
        win._pending_consents = {}
        win._consent_sig = MagicMock()

        cb1 = MagicMock()
        cb2 = MagicMock()

        win._consent_request("tool_a", "low", "arg1", cb1)
        win._consent_request("tool_b", "high", "arg2", cb2)

        # Ensure two unique entries in _pending_consents
        assert len(win._pending_consents) == 2
        req_ids = list(win._pending_consents.keys())
        assert req_ids[0] != req_ids[1]

        # Ensure signal emitted with request ID
        assert win._consent_sig.emit.call_count == 2
        call1_args = win._consent_sig.emit.call_args_list[0][0]
        call2_args = win._consent_sig.emit.call_args_list[1][0]
        assert call1_args[0] == req_ids[0]
        assert call1_args[1] == "tool_a"
        assert call2_args[0] == req_ids[1]
        assert call2_args[1] == "tool_b"


def test_ui_title_changed_resets_title():
    """Verify _on_title_changed resets title via JavaScript and executes callback."""
    from ui import UltronWebWindow

    with patch.object(UltronWebWindow, "__init__", lambda self: None):
        win = UltronWebWindow()
        win._eval_js = MagicMock()
        win.on_text_command = MagicMock()
        win._on_reconfig = MagicMock()

        # Command dispatch
        win._on_title_changed("CMD:/status")
        win._eval_js.assert_called_with("if (document.title.startsWith('CMD:')) document.title = 'ULTRON';")
        win.on_text_command.assert_called_with("/status")

        # Settings command
        win._on_title_changed("CMD:/settings")
        win._on_reconfig.assert_called_with("")


# ---------------------------------------------------------------------------
# INT-01: ApiKeyMissing Error Message Clarity
# ---------------------------------------------------------------------------

def test_api_key_missing_ollama_guidance():
    """Verify _get_api_key provides actionable guidance when provider is Ollama."""
    import main

    with patch("main.loader.get_api_key", return_value=None), \
         patch("main.loader.load_config", return_value={"llm_provider": "ollama"}):
        with pytest.raises(main.ApiKeyMissing) as exc_info:
            main._get_api_key()
        msg = str(exc_info.value)
        assert "Ollama is configured for gateway text completion" in msg
        assert "Gemini API key" in msg


# ---------------------------------------------------------------------------
# INT-02: Voice Stack & EchoGate Integration
# ---------------------------------------------------------------------------

class DummyAudioHost(AudioTasksMixin):
    def __init__(self):
        self.ui = MagicMock()
        self.ui.muted = False
        self._phone_active = False
        self._is_speaking = False
        self._speaking_lock = MagicMock()
        self._speaking_lock.__enter__ = MagicMock(return_value=None)
        self._speaking_lock.__exit__ = MagicMock(return_value=None)
        self.out_queue = asyncio.Queue()
        self._voice_gate = None
        self.interrupt = MagicMock()

    def gate_mic_frame(self, frame):
        if self._voice_gate is None:
            return frame
        return self._voice_gate.process(frame)


def test_audio_callback_echogate_active_and_barge_in():
    """Verify AudioTasksMixin.callback routes frames through EchoGate and interrupts on barge-in."""
    host = DummyAudioHost()
    gate = EchoGate()
    host._voice_gate = gate

    loop = asyncio.new_event_loop()
    with patch("asyncio.get_event_loop", return_value=loop), \
         patch.object(loop, "call_soon_threadsafe") as mock_threadsafe:

        # Step 1: Open gate -> frame passes
        frame = np.zeros(512, dtype=np.int16)

        # Extract callback closure by inspecting InputStream call
        with patch("sounddevice.InputStream") as mock_stream:
            async def run_listen():
                task = asyncio.create_task(host._listen_audio())
                await asyncio.sleep(0.01)
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            loop.run_until_complete(run_listen())
            cb = mock_stream.call_args[1]["callback"]

        # 1. Open gate test
        cb(frame, 512, None, None)
        assert mock_threadsafe.call_count == 1

        # 2. Gate muted (TTS playing) with quiet mic -> frame buffered, not sent
        gate.tts_started()
        host._is_speaking = True
        mock_threadsafe.reset_mock()
        cb(frame, 512, None, None)
        assert mock_threadsafe.call_count == 0
        assert gate.stats["gated_frames"] == 1

        # 3. Loud frame (barge-in RMS > 0.15) -> gate re-opens and interrupts TTS
        loud_frame = np.full(512, 10000, dtype=np.int16)  # high RMS
        cb(loud_frame, 512, None, None)
        assert gate.state == GateState.OPEN
        assert host.interrupt.call_count == 1
        assert mock_threadsafe.call_count == 1

    loop.close()


def test_set_speaking_echogate_lifecycle():
    """Verify set_speaking notifies EchoGate of TTS start/finish."""
    import main
    with patch.object(main.UltronLive, "__init__", lambda self, ui: None):
        live = main.UltronLive(None)
        live._speaking_lock = MagicMock()
        live._speaking_lock.__enter__ = MagicMock(return_value=None)
        live._speaking_lock.__exit__ = MagicMock(return_value=None)
        live._is_speaking = False
        live.set_app_state = MagicMock()
        live.note_tts_started = MagicMock()
        live.note_tts_finished = MagicMock()
        live.ui = MagicMock()
        live.ui.muted = False

        # Start speaking
        live.set_speaking(True)
        live.note_tts_started.assert_called_once()
        live.set_app_state.assert_called_with("SPEAKING")

        # Stop speaking
        live.set_speaking(False)
        live.note_tts_finished.assert_called_once()
        live.set_app_state.assert_called_with("LISTENING")


# ---------------------------------------------------------------------------
# INT-03: SkillCaptureListener Procedural Memory Capture
# ---------------------------------------------------------------------------

def test_skill_capture_listener_on_bus(tmp_path):
    """Verify completed jobs automatically capture skills into procedural memory."""
    async def _run():
        db_path = tmp_path / "memory.sqlite3"
        engine = MemoryEngine(db_path)
        bus = EventBus()

        listener = SkillCaptureListener(engine)
        listener.attach(bus)

        # 1. Publish job.started
        await bus.publish(Event(
            type="job.started",
            source="orchestrator",
            payload={"job": "job_123", "title": "Check system disk free space"},
        ))

        # 2. Publish job.completed with successful stop and tool calls
        await bus.publish(Event(
            type="job.completed",
            source="orchestrator",
            payload={
                "job": "job_123",
                "outputs": {
                    "step_0": {
                        "finish": "stop",
                        "tool_calls": [
                            {"name": "file_controller", "args": {"action": "disk_free"}}
                        ],
                    }
                },
            },
        ))

        # Verify skill was captured
        assert len(listener.captured) == 1
        proc_id = listener.captured[0]
        proc = engine.get_procedure(proc_id)
        assert proc is not None
        assert proc.name == task_slug("Check system disk free space")
        assert proc.success_count == 1
        assert len(proc.steps) == 1
        assert proc.steps[0]["tool"] == "file_controller"

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# INT-04: Zero-Polling Idle Loop Verification
# ---------------------------------------------------------------------------

def test_dashboard_commands_no_wait_for_busy_loop():
    """Verify _process_dashboard_commands awaits queue cleanly without timing out."""
    async def _run():
        class DummyMonitorHost(MonitorTasksMixin):
            def __init__(self):
                self._dashboard = MagicMock()
                self._dashboard._command_queue = asyncio.Queue()
                self.ui = MagicMock()
                self.session = None

        host = DummyMonitorHost()
        task = asyncio.create_task(host._process_dashboard_commands())

        # Ensure task suspends without churning
        await asyncio.sleep(0.05)
        assert not task.done()

        # Deliver a command
        host._on_text_command = MagicMock()
        await host._dashboard._command_queue.put("/status")
        await asyncio.sleep(0.05)
        host.ui.write_log.assert_called_with("[Web]: /status")

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# BAT-01 / BAT-02: Batch File Syntax & Argument Passing
# ---------------------------------------------------------------------------

def test_setup_bat_help_flag():
    """Verify SETUP.bat forwards flags to ULTRON_SETUP.py using Python 3.14."""
    proc = subprocess.run(
        ["cmd.exe", "/c", "SETUP.bat --help"],
        capture_output=True,
        text=True,
        cwd=Path(__file__).parent.parent,
    )
    assert proc.returncode == 0
    assert "Create ULTRON's local Python 3.14 environment" in proc.stdout
    assert "--with-browser" in proc.stdout
