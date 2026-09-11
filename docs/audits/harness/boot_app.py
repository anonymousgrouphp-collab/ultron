"""pUI-audit harness: boot the REAL ULTRON app in-process + audit helper.

Replicates main.main() exactly (single-instance lock, UltronUI mainloop on
the main thread, UltronLive+asyncio on a daemon thread) and adds an
AUDIT-ONLY helper HTTP server on 127.0.0.1:39153 (loopback, dies with the
process). It changes no repo code; it only calls the app's own methods:

  GET /mint      -> ultron._make_remote_key()  (same call the Qt Remote
                    Control button makes) -> {url,key,auto_login,manual}
  GET /state     -> observable HUD/app state snapshot (muted, ui state,
                    log tail, session presence, phone_active, speaking)
  GET /history   -> server-side WS broadcast history (dashboard._history)
  POST /hud_text -> ultron._on_text_command(text)  (the Qt HUD text-box path)

Usage:  py -3.14 -u docs/audits/harness/boot_app.py  (Ctrl+C or kill to stop)
"""

from __future__ import annotations

import json
import socket
import sys
import threading

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

HELPER_PORT = 39153
_holder: dict = {"app": None, "ui_state": None, "ui_log": []}


def _wrap_ui(ui) -> None:
    orig_state, orig_log = ui.set_state, ui.write_log

    def set_state(state: str) -> None:
        _holder["ui_state"] = state
        try:
            orig_state(state)
        except RuntimeError:
            pass  # HUD window closed by the user — keep serving

    def write_log(text: str) -> None:
        _holder["ui_log"].append(text)
        del _holder["ui_log"][:-40]
        try:
            orig_log(text)
        except RuntimeError:
            pass

    ui.set_state, ui.write_log = set_state, write_log


def _harden_window(ui_module) -> None:
    """AUDIT-ONLY: user closing the HUD window must not kill the app.
    hide() instead of close(); quitOnLastWindowClosed off."""
    base = ui_module.UltronWebWindow

    class AuditWindow(base):
        def closeEvent(self, event):
            event.ignore()
            self.hide()

    ui_module.UltronWebWindow = AuditWindow


class _Helper(BaseHTTPRequestHandler):
    def log_message(self, *a):  # keep audit log clean
        pass

    def _json(self, obj, code: int = 200) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        app = _holder["app"]
        u = urlparse(self.path)
        if u.path == "/healthz":
            return self._json({"ok": True, "app": app is not None})
        if app is None:
            return self._json({"ok": False, "error": "app not booted yet"}, 503)
        if u.path == "/mint":
            made = app._make_remote_key()
            if not made:
                return self._json({"ok": False, "error": "mint failed"}, 500)
            url, key, auto_login, manual = made
            return self._json({"ok": True, "url": url, "key": key,
                               "auto_login": auto_login, "manual": manual})
        if u.path == "/state":
            ui = app.ui
            dash = getattr(app, "_dashboard", None)
            return self._json({
                "muted": bool(ui.muted),
                "ui_state": _holder["ui_state"],
                "log_tail": list(_holder["ui_log"])[-15:],
                "session_alive": bool(getattr(app, "session", None)),
                "speaking": bool(getattr(app, "_is_speaking", False)),
                "phone_active": bool(getattr(app, "_phone_active", False)),
                "active_user": (lambda f: f() if f else None)(
                    getattr(app, "_active_user_id", None)),
                "dashboard_up": dash is not None,
                "ws_clients": len(dash._clients) if dash else 0,
            })
        if u.path == "/history":
            q = parse_qs(u.query)
            n = int(q.get("n", ["200"])[0])
            dash = app._dashboard
            hist = list(dash._history)[-n:] if dash else []
            return self._json({"count": len(hist), "messages": hist})
        return self._json({"error": "not found"}, 404)

    def do_POST(self):
        app = _holder["app"]
        u = urlparse(self.path)
        if app is None:
            return self._json({"ok": False, "error": "app not booted yet"}, 503)
        if u.path == "/hud_text":
            text = parse_qs(u.query).get("text", [""])[0]
            if not text:
                return self._json({"ok": False, "error": "text required"}, 400)
            app._on_text_command(text)
            return self._json({"ok": True, "dispatched": text})
        if u.path == "/publish":
            q = parse_qs(u.query)
            etype = q.get("type", [""])[0]
            payload = json.loads(q.get("payload", ["{}"])[0])
            if not etype:
                return self._json({"ok": False, "error": "type required"}, 400)
            import asyncio
            from kernel.types import Event
            fut = asyncio.run_coroutine_threadsafe(
                app._bus.publish(Event(type=etype, payload=payload,
                                       source="audit")),
                app._loop)
            fut.result(timeout=5)
            return self._json({"ok": True, "published": etype})
        return self._json({"error": "not found"}, 404)


def main() -> None:
    import main as ultron_main
    import ui as ui_module
    from ui import UltronUI

    _harden_window(ui_module)

    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.bind(("127.0.0.1", 39152))
        sock.close()
    except OSError:
        print("[pUI-audit] another ULTRON instance holds the lock — aborting.")
        sys.exit(1)

    helper = ThreadingHTTPServer(("127.0.0.1", HELPER_PORT), _Helper)
    threading.Thread(target=helper.serve_forever, daemon=True).start()
    print(f"[pUI-audit] helper on http://127.0.0.1:{HELPER_PORT}")

    ui = UltronUI("face.png")
    ui._app.setQuitOnLastWindowClosed(False)
    _wrap_ui(ui)

    def _on_quit():
        import faulthandler
        print("[pUI-audit] QApplication.aboutToQuit — thread dump follows",
              flush=True)
        faulthandler.dump_traceback()

    ui._app.aboutToQuit.connect(_on_quit)

    def runner():
        ui.wait_for_api_key()
        app = ultron_main.UltronLive(ui)
        _holder["app"] = app
        import asyncio
        import traceback
        try:
            asyncio.run(app.run())
        except KeyboardInterrupt:
            print("\n[pUI-audit] shutting down...")
        except BaseException as exc:
            print(f"[pUI-audit] app.run crashed: {exc}", flush=True)
            traceback.print_exc()

    threading.Thread(target=runner, daemon=True).start()
    ui.root.mainloop()


if __name__ == "__main__":
    main()
