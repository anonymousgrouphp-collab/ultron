import re

with open(r'e:\ultronmain\main.py', 'r', encoding='utf-8') as f:
    content = f.read()

# 1. Extract TOOL_DECLARATIONS
match = re.search(r'TOOL_DECLARATIONS\s*=\s*\[.*?^\]\n', content, re.DOTALL | re.MULTILINE)
if match:
    tool_decls = match.group(0)
    import os
    os.makedirs(r'e:\ultronmain\core', exist_ok=True)
    with open(r'e:\ultronmain\core\tool_declarations.py', 'w', encoding='utf-8') as out:
        out.write(tool_decls)
    content = content.replace(tool_decls, 'from core.tool_declarations import TOOL_DECLARATIONS\n')

# 2. Add class methods and TOOL_REGISTRY before `async def _execute_tool`
methods_code = """
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

    async def _handle_send_message(self, args, loop):
        r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
        return r or f"Message sent to {args.get('receiver')}."

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
            import time, os
            time.sleep(1)
            os._exit(0)
        import threading
        threading.Thread(target=_shutdown, daemon=True).start()
        return "Done."

    TOOL_REGISTRY = {
        "open_app": _handle_open_app,
        "weather_report": _handle_weather_report,
        "browser_control": _handle_browser_control,
        "file_controller": _handle_file_controller,
        "send_message": _handle_send_message,
        "reminder": _handle_reminder,
        "youtube_video": _handle_youtube_video,
        "screen_process": _handle_screen_process,
        "close_camera": _handle_close_camera,
        "computer_settings": _handle_computer_settings,
        "desktop_control": _handle_desktop_control,
        "code_helper": _handle_code_helper,
        "dev_agent": _handle_dev_agent,
        "web_search": _handle_web_search,
        "file_processor": _handle_file_processor,
        "computer_control": _handle_computer_control,
        "game_updater": _handle_game_updater,
        "flight_finder": _handle_flight_finder,
        "system_status": _handle_system_status,
        "shutdown_ultron": _handle_shutdown,
        "shutdown_jarvis": _handle_shutdown,
    }

    async def _execute_tool"""

content = content.replace('    async def _execute_tool', methods_code)

old_try_block = r'''        try:
            if name == "open_app":
                r = await loop.run_in_executor(None, lambda: open_app(parameters=args, response=None, player=self.ui))
                result = r or f"Opened {args.get('app_name')}."

            elif name == "weather_report":
                r = await loop.run_in_executor(None, lambda: weather_action(parameters=args, player=self.ui))
                result = r or "Weather delivered."

            elif name == "browser_control":
                r = await loop.run_in_executor(None, lambda: browser_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "file_controller":
                r = await loop.run_in_executor(None, lambda: file_controller(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "send_message":
                r = await loop.run_in_executor(None, lambda: send_message(parameters=args, response=None, player=self.ui, session_memory=None))
                result = r or f"Message sent to {args.get('receiver')}."

            elif name == "reminder":
                r = await loop.run_in_executor(None, lambda: reminder(parameters=args, response=None, player=self.ui))
                result = r or "Reminder set."

            elif name == "youtube_video":
                r = await loop.run_in_executor(None, lambda: youtube_video(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "screen_process":
                import time as _t_mod
                _now = _t_mod.monotonic()
                _cooldown = 4.0  # seconds — covers echo window after speaking ends
                if self._vision_busy or (_now - self._vision_last_time) < _cooldown:
                    _wait = max(0, _cooldown - (_now - self._vision_last_time))
                    print(f"[Vision] ⏳ Cooldown active ({_wait:.1f}s remaining) — ignoring duplicate call")
                    result = "Vision is still processing the previous request. I will not call this again."
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
                    result = (
                        f"[VISION_ACTIVE] {_stall.capitalize()} captured. "
                        f"Immediately say ONE short natural sentence in the user's own language, "
                        f"telling them you are looking at their {_stall} right now. "
                        f"Do NOT describe or guess content — the actual image arrives in the NEXT message."
                    )

            elif name == "close_camera":
                self.ui.stop_camera_stream()
                result = "Camera closed."

            elif name == "computer_settings":
                r = await loop.run_in_executor(None, lambda: computer_settings(parameters=args, response=None, player=self.ui))
                result = r or "Done."

            elif name == "desktop_control":
                r = await loop.run_in_executor(None, lambda: desktop_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "code_helper":
                r = await loop.run_in_executor(None, lambda: code_helper(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "dev_agent":
                r = await loop.run_in_executor(None, lambda: dev_agent(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "web_search":
                r = await loop.run_in_executor(None, lambda: web_search_action(parameters=args, player=self.ui))
                result = r or "Done."
                # Mirror results to the on-screen content panel
                _mode = args.get("mode", "search")
                if r and not r.startswith("No results") and not r.startswith("Search failed"):
                    _query = args.get("query") or ", ".join(args.get("items", []))
                    _label = f"{_mode.upper()} — {_query[:38]}" if _query else _mode.upper()
                    self.ui.show_content(_label, r)
            elif name == "file_processor":
                if not args.get("file_path") and self.ui.current_file:
                    args["file_path"] = self.ui.current_file
                r = await loop.run_in_executor(
                    None,
                    lambda: file_processor(parameters=args, player=self.ui, speak=self.speak)
                )
                result = r or "Done."

            elif name == "computer_control":
                r = await loop.run_in_executor(None, lambda: computer_control(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "game_updater":
                r = await loop.run_in_executor(None, lambda: game_updater(parameters=args, player=self.ui, speak=self.speak))
                result = r or "Done."

            elif name == "flight_finder":
                r = await loop.run_in_executor(None, lambda: flight_finder(parameters=args, player=self.ui))
                result = r or "Done."

            elif name == "system_status":
                r = await loop.run_in_executor(None, get_system_status)
                result = str(r)

            elif name == "shutdown_ultron" or name == "shutdown_jarvis":
                self.ui.write_log("SYS: Shutdown requested.")
                self.speak("Goodbye, sir.")
                def _shutdown():
                    import time, os
                    time.sleep(1)
                    os._exit(0)
                threading.Thread(target=_shutdown, daemon=True).start()

            else:
                result = f"Unknown tool: {name}"

        except Exception as e:'''

new_try_block = '''        try:
            handler = self.TOOL_REGISTRY.get(name)
            if handler:
                result = await handler(self, args, loop)
            else:
                result = f"Unknown tool: {name}"

        except Exception as e:'''

if old_try_block in content:
    content = content.replace(old_try_block, new_try_block)
else:
    print("Could not find try block to replace!")

with open(r'e:\ultronmain\main.py', 'w', encoding='utf-8') as f:
    f.write(content)
print("Done modifying main.py")
