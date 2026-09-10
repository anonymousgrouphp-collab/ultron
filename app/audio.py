"""app/audio.py — the four Gemini Live audio pump tasks (Phase W0).

Extracted verbatim from main.py: mic capture (`_listen_audio`), the send
queue pump (`_send_realtime`), the response pump that also drives tool
calls + vision injection + transcript logging (`_receive_audio`), and
speaker playback with 50 ms barge-in slicing (`_play_audio`).

Kept as a mixin: `UltronLive` inherits these so existing call sites and the
characterization tests keep working unchanged. No behavior edits — the
extraction is the change (main.py must shrink, ROADMAP §0 rule).
"""

from __future__ import annotations

import asyncio
import base64
import time
import traceback
from datetime import datetime

import sounddevice as sd

from app.text import clean_transcript as _clean_transcript


class AudioTasksMixin:
    """The audio I/O half of a Gemini Live session.

    Expects the host (UltronLive) to provide: session, out_queue,
    audio_in_queue, ui, _speaking_lock, _is_speaking, _interrupted,
    _phone_active, _turn_done_event, _pending_vision, _vision_busy,
    _vision_cam_active, _vision_close_pending, _last_user_speech,
    _session_user_messages, _session_assistant_responses,
    _session_tool_calls, _dashboard, _asst_name, set_app_state(),
    set_speaking(), _execute_tool().
    """

    SEND_SAMPLE_RATE = 16000
    RECEIVE_SAMPLE_RATE = 24000
    CHANNELS = 1
    CHUNK_SIZE = 512

    async def _send_realtime(self):
        while True:
            msg = await self.out_queue.get()
            await self.session.send_realtime_input(media=msg)

    async def _listen_audio(self):
        print("[ULTRON] 🎤 Mic started")
        loop = asyncio.get_event_loop()

        def callback(indata, frames, time_info, status):
            with self._speaking_lock:
                ultron_speaking = self._is_speaking
            if not ultron_speaking and not self.ui.muted and not self._phone_active:
                data = indata.tobytes()
                loop.call_soon_threadsafe(
                    self.out_queue.put_nowait,
                    {"data": data, "mime_type": "audio/pcm"}
                )

        try:
            with sd.InputStream(
                samplerate=self.SEND_SAMPLE_RATE,
                channels=self.CHANNELS,
                dtype="int16",
                blocksize=self.CHUNK_SIZE,
                callback=callback,
            ):
                print("[ULTRON] 🎤 Mic stream open")
                while True:
                    await asyncio.sleep(0.02)
        except Exception as e:
            print(f"[ULTRON] ❌ Mic: {e}")
            raise

    async def _receive_audio(self):
        print("[ULTRON] 👂 Recv started")
        out_buf, in_buf = [], []

        try:
            while True:
                async for response in self.session.receive():

                    if response.data:
                        if self._interrupted:
                            pass  # discard: interrupted
                        else:
                            if self._turn_done_event and self._turn_done_event.is_set():
                                self._turn_done_event.clear()
                            # Split into ~50 ms chunks so interrupt() stops audio within 50 ms
                            # (24000 Hz × 2 bytes/sample × 0.05 s = 2400 bytes per slice)
                            _audio_data = response.data
                            _SLICE = 2400
                            for _i in range(0, len(_audio_data), _SLICE):
                                self.audio_in_queue.put_nowait(_audio_data[_i : _i + _SLICE])

                    if response.server_content:
                        sc = response.server_content

                        if sc.output_transcription and sc.output_transcription.text:
                            txt = _clean_transcript(sc.output_transcription.text)
                            if txt and txt != (out_buf[-1] if out_buf else ""):
                                out_buf.append(txt)

                        if sc.input_transcription and sc.input_transcription.text:
                            txt = _clean_transcript(sc.input_transcription.text)
                            if txt:
                                in_buf.append(txt)
                                self._last_user_speech = time.monotonic()
                                self.set_app_state("THINKING")

                        if sc.turn_complete:
                            if self._turn_done_event:
                                self._turn_done_event.set()

                            # If this turn_complete ends an interrupted response, clear the
                            # flag and skip all further processing for that turn.
                            if self._interrupted:
                                self._interrupted = False
                                in_buf  = []
                                out_buf = []
                                continue

                            full_in = " ".join(in_buf).strip()
                            if full_in:
                                self.ui.write_log(f"You: {full_in}")
                                self._session_user_messages.append(full_in)  # Phase I3: track for session summary
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "user",
                                        "text": full_in,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            in_buf = []

                            full_out = " ".join(out_buf).strip()
                            if full_out:
                                self.ui.write_log(f"{self._asst_name}: {full_out}")
                                self._session_assistant_responses.append(full_out)  # Phase I3
                                if self._dashboard:
                                    asyncio.create_task(self._dashboard.broadcast({
                                        "type": "log", "speaker": "ultron",
                                        "text": full_out,
                                        "ts": datetime.now().isoformat(),
                                    }))
                            out_buf = []

                            # Vision injection: model finished tool-response turn → now send the image
                            if self._pending_vision and self.session:
                                img_b, mime_t, question, angle = self._pending_vision
                                self._pending_vision = None
                                b64 = base64.b64encode(img_b).decode("ascii")
                                print(f"[Vision] 📤 {len(img_b):,} bytes (angle={angle}) → main session")
                                await self.session.send_client_content(
                                    turns={"parts": [
                                        {"inline_data": {"mime_type": mime_t, "data": b64}},
                                        {"text": question},
                                    ]},
                                    turn_complete=True,
                                )
                                # Mark next turn_complete behaviour depending on angle
                                if self._vision_cam_active:
                                    # Camera: keep busy until ULTRON finishes speaking the answer
                                    self._vision_cam_active    = False
                                    self._vision_close_pending = True
                                else:
                                    # Screen-only: no camera to close; release busy flag now
                                    self._vision_busy = False
                            elif self._vision_close_pending:
                                # This turn_complete IS the vision answer — close camera + release busy flag
                                self._vision_close_pending = False
                                self._vision_busy = False
                                async def _cam_close():
                                    await asyncio.sleep(2.0)
                                    self.ui.stop_camera_stream()
                                asyncio.create_task(_cam_close())

                    if response.tool_call:
                        fn_responses = []
                        for fc in response.tool_call.function_calls:
                            print(f"[ULTRON] 📞 {fc.name}")
                            self._session_tool_calls.append(fc.name)  # Phase I3: track for session summary
                            fr = await self._execute_tool(fc)
                            fn_responses.append(fr)
                        await self.session.send_tool_response(
                            function_responses=fn_responses
                        )
        except Exception as e:
            print(f"[ULTRON] ❌ Recv: {e}")
            traceback.print_exc()
            raise

    async def _play_audio(self):
        print("[ULTRON] 🔊 Play started")

        stream = sd.RawOutputStream(
            samplerate=self.RECEIVE_SAMPLE_RATE,
            channels=self.CHANNELS,
            dtype="int16",
            blocksize=self.CHUNK_SIZE,
        )
        stream.start()

        try:
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        self.audio_in_queue.get(),
                        timeout=0.02
                    )
                except asyncio.TimeoutError:
                    if (
                        self._turn_done_event
                        and self._turn_done_event.is_set()
                        and self.audio_in_queue.empty()
                    ):
                        self.set_speaking(False)
                        self._turn_done_event.clear()
                    continue
                self.set_speaking(True)
                try:
                    await asyncio.to_thread(stream.write, chunk)
                except (RuntimeError, asyncio.CancelledError, Exception) as write_err:
                    if isinstance(write_err, (RuntimeError, asyncio.CancelledError)):
                        break   # executor shutting down — exit cleanly
                    # PortAudio / device write failure during stream pause or interrupt
                    pass
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[ULTRON] ❌ Play: {e}")
        finally:
            self.set_speaking(False)
            try:
                if stream.active:
                    stream.stop()
            except Exception:
                pass
            try:
                stream.close()
            except Exception:
                pass
