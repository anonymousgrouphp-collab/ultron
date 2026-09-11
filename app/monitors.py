"""app/monitors.py — background monitor/relay tasks (Phase W0).

Extracted verbatim from main.py: the emergency system monitor
(`_run_system_monitor`), the proactive bus ticker (`_run_proactive_mode`),
the phone-audio relay (`_relay_phone_audio` + `_on_phone_connected`), and
the dashboard command pump (`_process_dashboard_commands`). Mixin for
UltronLive.
"""

from __future__ import annotations

import asyncio
import base64
import time
from datetime import datetime

from kernel.types import Event


class MonitorTasksMixin:
    """Expects the host to provide: _sys_monitor, _bus, session, ui,
    _dashboard, _speaking_lock, _is_speaking, _phone_active,
    _last_user_speech, out_queue, set_app_state()."""

    async def _run_system_monitor(self) -> None:
        """Background task: emergency alerts and non-destructive suggestions."""
        emergency_active = False
        while True:
            await asyncio.sleep(2.0)
            status = await asyncio.to_thread(self._sys_monitor.check_emergency)
            is_90 = status.get("is_emergency_90", False)
            suggested_apps = status.get("suggested_apps", [])
            cpu = status.get("cpu", 0)
            ram = status.get("ram", 0)

            if is_90 and not emergency_active:
                emergency_active = True
                self.ui.set_state("EMERGENCY")
                self.ui.write_log(f"SYS_ALERT: EMERGENCY SYSTEM OVERLOAD DETECTED (CPU: {cpu}%, RAM: {ram}%)! Red alert active.")
                if self.session:
                    try:
                        await self.session.send_client_content(
                            turns={"parts": [{"text": f"[SYSTEM_ALERT] Emergency system overload! CPU/RAM at {max(cpu, ram)}%. State that red alert emergency siren is active."}]},
                            turn_complete=True,
                        )
                    except Exception:
                        pass

            elif not is_90 and emergency_active:
                emergency_active = False
                self.ui.set_state("LISTENING" if not self.ui.muted else "MUTED")
                self.ui.write_log("SYS: System usage normalized (<85%). Emergency alert deactivated.")

            if suggested_apps and self.session:
                app_names = ", ".join(suggested_apps).replace(".exe", "")
                self.ui.write_log(f"SYS_ALERT: 95%+ OVERLOAD - consider closing: {app_names}.")
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": f"[SYSTEM_ALERT] Critical system overload (>95%). Suggest the user manually close heavy applications ({app_names}); do not claim any application was closed."}]},
                        turn_complete=True,
                    )
                except Exception:
                    pass

            alert = await asyncio.to_thread(self._sys_monitor.check)
            if alert and self.session and not is_90:
                try:
                    await self.session.send_client_content(
                        turns={"parts": [{"text": alert}]},
                        turn_complete=True,
                    )
                except Exception as e:
                    print(f"[Monitor] ⚠️ Could not send alert: {e}")

    async def _run_proactive_mode(self) -> None:
        """
        Background task: publishes a `system.tick` bus event every minute. The
        kernel P4-D ProactiveEngine (kernel/proactive) evaluates its rules
        (cooldowns, hour caps, consent classes) and emits proactive.decision
        events; `_on_proactive_decision` speaks the fired ones. The legacy
        silence-timer (actions/proactive.py) is retired from production.
        """
        while True:
            await asyncio.sleep(60)

            if not self.session:
                continue

            with self._speaking_lock:
                speaking = self._is_speaking
            if speaking:
                continue

            try:
                await self._bus.publish(Event(
                    type="system.tick",
                    payload={
                        "silence_min": int(
                            (time.monotonic() - self._last_user_speech) // 60
                        ),
                        "time": datetime.now().strftime("%I:%M %p"),
                    },
                    source="live",
                ))
            except Exception as e:
                print(f"[Proactive] WARN tick publish failed: {e}")

    async def _relay_phone_audio(self) -> None:
        """Forward phone mic PCM chunks from dashboard queue into the Gemini Live session."""
        q = self._dashboard._phone_audio_queue
        while True:
            try:
                chunk = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                # No audio for 1 s → phone mic inactive, give PC mic back
                self._phone_active = False
                continue
            self._phone_active = True   # phone is streaming — silence PC mic
            with self._speaking_lock:
                speaking = self._is_speaking
            if not speaking and not self.ui.muted:
                try:
                    self.out_queue.put_nowait(chunk)
                except asyncio.QueueFull:
                    pass

    def _on_phone_connected(self) -> None:
        self.ui.write_log("SYS: Phone connected via Remote Dashboard.")
        self.ui.notify_phone_connected()

    async def _process_dashboard_commands(self) -> None:
        while True:
            try:
                item = await asyncio.wait_for(
                    self._dashboard._command_queue.get(), timeout=0.02
                )
                if not item:
                    continue

                if isinstance(item, dict) and item.get("type") == "image":
                    for _ in range(40):
                        if self.session:
                            break
                        await asyncio.sleep(0.05)
                    if self.session:
                        b64_str = item.get("data", "")
                        mime = item.get("mime", "image/jpeg")
                        img_bytes = base64.b64decode(b64_str)
                        self.ui.write_log("SYS: Image received. ULTRON analyzing image...")
                        await self.session.send_realtime_input(media={"data": img_bytes, "mime_type": mime})
                        await self.session.send_client_content(
                            turns={"parts": [{"text": "Please analyze this attached image in full detail, describe every visual element, and explain what it represents."}]},
                            turn_complete=True,
                        )
                    else:
                        print("[Dashboard] Dropped image item (no session)")
                else:
                    text = str(item).strip()
                    if text:
                        # Slash/system commands execute immediately without session.
                        # Conversational chat waits briefly for session to settle.
                        is_sys_cmd = (
                            text.startswith("/")
                            or text in ("toggle_mic", "mute", "unmute")
                        )
                        if not is_sys_cmd and not self.session:
                            for _ in range(20):
                                if self.session:
                                    break
                                await asyncio.sleep(0.05)

                        self.ui.write_log(f"[Web]: {text}")
                        if hasattr(self, "_on_text_command"):
                            self._on_text_command(text)
            except asyncio.TimeoutError:
                pass
            except Exception as e:
                print(f"[Dashboard] Command error: {e}")
                await asyncio.sleep(0.1)
