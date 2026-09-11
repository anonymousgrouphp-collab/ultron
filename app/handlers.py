"""app/handlers.py — the 20 legacy tool handlers + memory helpers (Phase W0).

Extracted verbatim from main.py. These are the P1-F migration seam: each
`_handle_*` bridges a legacy `actions/` function into the kernel-executed
LegacyToolRuntime. The characterization tests pin the exact
`_handle_<name>` attribute names on UltronLive, so they stay methods on
the host class via this mixin.

Action imports live here (not in main.py) so main.py's import block can
shrink with the handler block.
"""

from __future__ import annotations

import threading

from actions.browser_control   import browser_control
from actions.code_helper       import code_helper
from actions.computer_control  import computer_control
from actions.computer_settings import computer_settings
from actions.desktop           import desktop_control
from actions.dev_agent         import dev_agent
from actions.file_controller   import file_controller
from actions.file_processor    import file_processor
from actions.flight_finder     import flight_finder
from actions.game_updater      import game_updater
from actions.open_app          import open_app
from actions.reminder          import reminder
from actions.screen_processor  import _capture_camera, _capture_screen
from actions.system_monitor    import get_system_status
from actions.weather_report    import weather_action
from actions.web_search        import web_search as web_search_action
from actions.youtube_video     import youtube_video

from kernel.memory import migrate_long_term_json


class LegacyHandlersMixin:
    """Expects the host to provide: ui, _memory, _vision_busy,
    _vision_last_time, _vision_cam_active, _pending_vision, speak(), and
    BASE_DIR (module attr on main)."""

    async def _handle_open_app(self, args, loop):
        r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
        return r or f"Opened {args.get('app_name')}."

    async def _handle_weather_report(self, args, loop):
        r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
        return r or "Weather delivered."

    async def _handle_browser_control(self, args, loop):
        r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_file_controller(self, args, loop):
        r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_reminder(self, args, loop):
        r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
        return r or "Reminder set."

    async def _handle_youtube_video(self, args, loop):
        r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
        return r or "Done."

    async def _handle_screen_process(self, args, loop):
        import time as _t_mod
        _now = _t_mod.monotonic()
        _cooldown = 4.0  # seconds — covers echo window after speaking ends
        if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
            _wait = max(0, _cooldown - (_now - self._vision_last_time))
            print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
            return "Vision is still processing the previous request. I will not call this again."
        else:
            self._vision_busy      = True
            self._vision_last_time = _now
            angle     = args.get("angle", "screen").lower()
            user_text = args.get("text", "What do you see?")
            if angle == "camera":
                img_b, mime_t = await loop.run_in_executor(None, _capture_camera)
                self.ui.start_camera_stream()
                self._vision_cam_active = True
                print(f"[Vision] 📷 Camera: {len(img_b):,} bytes")
                _stall = "camera"
            else:
                img_b, mime_t = await loop.run_in_executor(None, _capture_screen)
                print(f"[Vision] 🖥️  Screen: {len(img_b):,} bytes")
                _stall = "screen"
            self._pending_vision = (img_b, mime_t, user_text, angle)
            return (
                f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                f"Immediately say ONE short natural sentence in the user's own language, "
                f"telling them you are looking at their {_stall} right now. "
                f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
            )

    async def _handle_close_camera(self, args, loop):
        self.ui.stop_camera_stream()
        return "Camera closed."

    async def _handle_computer_settings(self, args, loop):
        r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
        return r or "Done."

    async def _handle_desktop_control(self, args, loop):
        r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_code_helper(self, args, loop):
        r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
        return r or "Done."

    async def _handle_dev_agent(self, args, loop):
        r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
        return r or "Done."

    async def _handle_web_search(self, args, loop):
        r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
        result = r or "Done."
        # Mirror results to the on-screen content panel
        _mode = args.get("mode", "search")
        if r and not r.startswith("No results") and not r.startswith("Search failed"):
            _query = args.get("query") or ", ".join(args.get("items", []))
            _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
            self.ui.show_content(_label, r)
        return result

    async def _handle_file_processor(self, args, loop):
        if not args.get("file_path") and self.ui.current_file:
            args["file_path"] = self.ui.current_file
        r = await loop.run_in_executor(
            None,
            lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
        )
        return r or "Done."

    async def _handle_computer_control(self, args, loop):
        r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_game_updater(self, args, loop):
        r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
        return r or "Done."

    async def _handle_flight_finder(self, args, loop):
        r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
        return r or "Done."

    async def _handle_system_status(self, args, loop):
        r = await loop.run_in_executor(None, get_system_status)
        return str(r)

    async def _handle_shutdown(self, args, loop):
        self.ui.write_log("SYS: Shutdown requested.")
        self.speak("Goodbye, sir.")
        def _shutdown():
            import time
            import os
            time.sleep(1)
            os._exit(0)
        threading.Thread(target=_shutdown, daemon=True).start()
        return "Done."

    async def _handle_save_memory(self, args, loop):
        category = args.get("category", "notes")
        key = args.get("key", "")
        value = args.get("value", "")
        if not key or not value:
            return "Memory was not saved because key or value was missing."
        self._memory.remember(
            str(value).strip(),
            entity=str(category).strip() or "notes",
            topic=str(key).strip(),
            source_ref="voice-live",
        )
        print(f"[Memory] saved {category}/{key}")
        return "Memory saved."

    def _memory_prompt(self) -> str:
        """Render the kernel MemoryEngine's most recent facts for the system
        prompt — the production replacement for the legacy 2,200-char JSON
        `format_memory_for_prompt` (Phase R3)."""
        hits = self._memory.page(limit=40)
        if not hits:
            return ""
        lines = []
        for h in hits:
            label = (h.entity or h.topic or "note").replace("_", " ").title()
            lines.append(f"  - {label}: {h.content}")
        result = (
            "[WHAT YOU KNOW ABOUT THIS PERSON — use naturally, "
            "never recite like a list]\n" + "\n".join(lines)
        )
        if len(result) > 2000:
            result = result[:1997] + "…"
        return result + "\n"

    def _migrate_legacy_memory(self) -> None:
        """One-shot, idempotent long_term.json → MemoryEngine migration at
        boot (kernel.memory.migration; re-runs add 0). The legacy file stays
        in place; production no longer reads it (Phase R3)."""
        from main import BASE_DIR
        legacy = BASE_DIR / "memory" / "long_term.json"
        if not legacy.exists():
            return
        try:
            added = migrate_long_term_json(self._memory, legacy)
            print(f"[Memory] migrated {added} legacy fact(s) -> kernel engine")
        except Exception as e:
            print(f"[Memory] WARN long_term.json migration skipped: {e}")
