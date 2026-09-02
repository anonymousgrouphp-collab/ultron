from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from pathlib import Path

if platform.system() == "Windows":
    _WIN_HIDE: dict = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:
    _WIN_HIDE: dict = {}

# ─────────────────────────────────────────────────────────────────────────────
# ULTRON WebEngine Anti-Flicker Environment Configuration
# MUST be set BEFORE importing PyQt6 modules or creating QApplication!
# ─────────────────────────────────────────────────────────────────────────────
os.environ["QTWEBENGINE_DISABLE_NO_SANDBOX"] = "1"
os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
os.environ["QT_SCALE_FACTOR_ROUNDING_POLICY"] = "PassThrough"

_ui_chrome_flags = [
    "--enable-gpu-rasterization",
    "--enable-accelerated-2d-canvas",
    "--ignore-gpu-blocklist",
    "--disable-gpu-driver-bug-workarounds",
]
_render_mode = os.environ.get("ULTRON_RENDER_MODE", "auto").lower()
if _render_mode == "software":
    _ui_chrome_flags.append("--disable-gpu")
    os.environ["QT_OPENGL"] = "software"
elif _render_mode == "angle":
    _ui_chrome_flags.extend(["--use-gl=angle", "--use-angle=d3d11"])
elif _render_mode == "desktop_gl":
    _ui_chrome_flags.append("--use-gl=desktop")
    os.environ["QT_OPENGL"] = "desktop"

if "QTWEBENGINE_CHROMIUM_FLAGS" not in os.environ:
    os.environ["QTWEBENGINE_CHROMIUM_FLAGS"] = " ".join(_ui_chrome_flags)

try:
    from PyQt6.QtWebEngineWidgets import QWebEngineView
    from PyQt6.QtWebEngineCore import QWebEngineSettings
    _WEBENGINE_OK = True
except ImportError:
    _WEBENGINE_OK = False

from PyQt6.QtCore import (
    Qt, QUrl, pyqtSignal, QCoreApplication, QTimer,
)
from PyQt6.QtGui import (
    QColor, QPalette, QSurfaceFormat, QKeySequence, QShortcut,
)
from PyQt6.QtWidgets import (
    QApplication, QLabel, QMainWindow, QVBoxLayout, QWidget,
    QDialog, QLineEdit, QPushButton, QHBoxLayout, QMessageBox,
    QComboBox, QFormLayout, QCheckBox, QGroupBox,
)

_qt_env_initialized = False

def _setup_qt_environment():
    global _qt_env_initialized
    if _qt_env_initialized:
        return
    _qt_env_initialized = True

    os.environ["QTWEBENGINE_DISABLE_NO_SANDBOX"] = "1"

    chrome_switches = [
        "--enable-gpu-rasterization",
        "--enable-accelerated-2d-canvas",
        "--enable-zero-copy",
        "--ignore-gpu-blocklist",
        "--disable-gpu-driver-bug-workarounds",
        "--disable-direct-composition",
    ]
    for switch in chrome_switches:
        if switch not in sys.argv:
            sys.argv.append(switch)

    try:
        if hasattr(Qt.ApplicationAttribute, "AA_ShareOpenGLContexts"):
            QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts, True)
    except Exception as e:
        print(f"[ULTRON GUI] Qt attribute warning: {e}", file=sys.stderr)

    try:
        fmt = QSurfaceFormat()
        fmt.setSwapBehavior(QSurfaceFormat.SwapBehavior.DoubleBuffer)
        fmt.setSwapInterval(1)
        fmt.setDepthBufferSize(24)
        fmt.setStencilBufferSize(8)
        QSurfaceFormat.setDefaultFormat(fmt)
    except Exception as e:
        print(f"[ULTRON GUI] QSurfaceFormat warning: {e}", file=sys.stderr)

_setup_qt_environment()


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent

BASE_DIR   = _base_dir()
CONFIG_DIR = BASE_DIR / "config"
API_FILE   = CONFIG_DIR / "api_keys.json"


def _read_full_config() -> dict:
    """Read api_keys.json config dict. Returns {} on any error."""
    try:
        return json.loads(API_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_full_config(new_cfg: dict) -> None:
    """Safely save complete configuration to config/api_keys.json."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    API_FILE.write_text(json.dumps(new_cfg, indent=4), encoding="utf-8")


_PLACEHOLDER_KEY = "YOUR_GEMINI_API_KEY_HERE"


def _needs_api_key() -> bool:
    """True if using Gemini and API key is missing or placeholder."""
    cfg = _read_full_config()
    provider = str(cfg.get("llm_provider", "gemini")).strip().lower()
    if provider in ("ollama", "openai", "lmstudio", "localai", "jan", "llamacpp"):
        return False
    key = str(cfg.get("gemini_api_key", "")).strip()
    return not key or key == _PLACEHOLDER_KEY


def _write_api_key(key: str) -> None:
    """Safely save the API key."""
    cfg = _read_full_config()
    if not cfg:
        cfg = {
            "os_system": "windows",
            "morning_brief_enabled": True,
            "assistant_name": "ULTRON",
            "user_name": "",
            "ui_color": "#00ff66",
        }
    cfg["gemini_api_key"] = key.strip()
    _save_full_config(cfg)


class EngineSettingsDialog(QDialog):
    """Sleek UI for switching between Gemini, Ollama, LM Studio, and Groq."""

    def __init__(self, parent=None, error_message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("ULTRON — AI Engine & Model Settings")
        self.setFixedSize(540, 480)
        self.setModal(True)
        self.setStyleSheet("""
            QDialog { background-color: #080c16; color: #d8e0ec; }
            QLabel { color: #c4d2e8; font-size: 12px; }
            QLineEdit, QComboBox {
                background-color: #12192a; color: #00ff88;
                border: 1px solid #253350; border-radius: 4px;
                padding: 6px 8px; font-size: 12px;
            }
            QLineEdit:focus, QComboBox:focus { border: 1px solid #ff1728; }
            QComboBox QAbstractItemView {
                background-color: #12192a; color: #00ff88;
                selection-background-color: #ff1728; selection-color: white;
            }
            QGroupBox {
                border: 1px solid #202b42; border-radius: 6px;
                margin-top: 10px; padding-top: 10px;
                color: #ff4d5a; font-weight: bold; font-size: 11px;
            }
            QCheckBox { color: #c4d2e8; font-size: 12px; }
            QPushButton {
                background-color: #b00020; color: white;
                border: none; border-radius: 4px;
                padding: 8px 14px; font-size: 12px; font-weight: bold;
            }
            QPushButton:hover { background-color: #d4002a; }
            QPushButton#ghost { background-color: #1e283e; color: #c4d2e8; }
            QPushButton#ghost:hover { background-color: #2b3956; color: white; }
            QPushButton#test_btn { background-color: #184232; color: #44ffaa; }
            QPushButton#test_btn:hover { background-color: #1f5e46; }
        """)

        cfg = _read_full_config()
        self.current_cfg = cfg

        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        title = QLabel("⚙️ ULTRON AI Engine Configuration")
        title.setStyleSheet("font-size: 15px; font-weight: bold; color: #ff3344;")
        layout.addWidget(title)

        if error_message:
            err = QLabel(f"⚠️ {error_message}")
            err.setStyleSheet("color: #ff8080; font-size: 11px;")
            err.setWordWrap(True)
            layout.addWidget(err)

        # Provider Selector
        prov_box = QGroupBox("AI PROVIDER / BACKEND")
        prov_layout = QFormLayout(prov_box)
        prov_layout.setSpacing(8)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("🔴 Google Gemini (Multimodal Live Voice / Free)", "gemini")
        self.provider_combo.addItem("🦙 Ollama (100% Free / Local Offline)", "ollama")
        self.provider_combo.addItem("💻 LM Studio / LocalAI / OpenAI API (100% Free Local)", "openai")
        self.provider_combo.addItem("⚡ Groq Cloud (Free Ultra-Fast Open Models)", "groq")

        current_prov = str(cfg.get("llm_provider", "gemini")).strip().lower()
        if current_prov == "ollama":
            self.provider_combo.setCurrentIndex(1)
        elif current_prov == "openai":
            self.provider_combo.setCurrentIndex(2)
        elif current_prov == "groq":
            self.provider_combo.setCurrentIndex(3)
        else:
            self.provider_combo.setCurrentIndex(0)

        self.provider_combo.currentIndexChanged.connect(self._on_provider_changed)
        prov_layout.addRow("Select Engine:", self.provider_combo)
        layout.addWidget(prov_box)

        # Parameters Group
        param_box = QGroupBox("ENGINE PARAMETERS")
        self.param_layout = QFormLayout(param_box)
        self.param_layout.setSpacing(8)

        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.api_key_input.setText(cfg.get("gemini_api_key", ""))
        self.api_key_label = QLabel("API Key:")

        self.key_row = QHBoxLayout()
        self.key_row.addWidget(self.api_key_input)
        self.show_btn = QPushButton("Show")
        self.show_btn.setObjectName("ghost")
        self.show_btn.setCheckable(True)
        self.show_btn.setFixedWidth(60)
        self.show_btn.toggled.connect(lambda c: self.api_key_input.setEchoMode(
            QLineEdit.EchoMode.Normal if c else QLineEdit.EchoMode.Password
        ))
        self.key_row.addWidget(self.show_btn)

        self.param_layout.addRow(self.api_key_label, self.key_row)

        self.url_input = QLineEdit()
        self.url_input.setText(cfg.get("llm_url", "http://localhost:11434"))
        self.url_label = QLabel("Server Endpoint URL:")
        self.param_layout.addRow(self.url_label, self.url_input)

        self.model_input = QLineEdit()
        self.model_input.setText(cfg.get("llm_model", "gemini-2.5-flash-native-audio-preview-12-2025"))
        self.model_label = QLabel("Model Name:")
        self.param_layout.addRow(self.model_label, self.model_input)

        self.hint_label = QLabel("")
        self.hint_label.setStyleSheet("color: #7d93b8; font-size: 10px;")
        self.hint_label.setWordWrap(True)
        self.param_layout.addRow("", self.hint_label)

        layout.addWidget(param_box)

        # Preferences Group
        pref_box = QGroupBox("PREFERENCES")
        pref_layout = QFormLayout(pref_box)
        self.name_input = QLineEdit()
        self.name_input.setText(cfg.get("assistant_name", "ULTRON"))
        pref_layout.addRow("Assistant Persona Name:", self.name_input)

        self.brief_check = QCheckBox("Enable Daily Morning Briefing")
        self.brief_check.setChecked(bool(cfg.get("morning_brief_enabled", True)))
        pref_layout.addRow("", self.brief_check)
        layout.addWidget(pref_box)

        # Status Label for Test
        self.status_lbl = QLabel("")
        self.status_lbl.setStyleSheet("font-size: 11px;")
        layout.addWidget(self.status_lbl)

        # Buttons
        btn_row = QHBoxLayout()
        test_btn = QPushButton("🔍 Test Connection")
        test_btn.setObjectName("test_btn")
        test_btn.clicked.connect(self._on_test_connection)
        btn_row.addWidget(test_btn)

        btn_row.addStretch()

        cancel_btn = QPushButton("Cancel")
        cancel_btn.setObjectName("ghost")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)

        save_btn = QPushButton("Save && Apply")
        save_btn.clicked.connect(self._on_save)
        btn_row.addWidget(save_btn)

        layout.addLayout(btn_row)

        self.result_key = None
        self._on_provider_changed(self.provider_combo.currentIndex())

    def _on_provider_changed(self, index: int):
        prov = self.provider_combo.currentData()
        if prov == "gemini":
            self.api_key_label.setVisible(True)
            self.api_key_input.setVisible(True)
            self.show_btn.setVisible(True)
            self.url_label.setVisible(False)
            self.url_input.setVisible(False)
            self.model_label.setText("Model:")
            self.model_input.setText("gemini-2.5-flash-native-audio-preview-12-2025")
            self.hint_label.setText("💡 Get a 100% free Gemini API key with zero billing at aistudio.google.com/apikey")
        elif prov == "ollama":
            self.api_key_label.setVisible(False)
            self.api_key_input.setVisible(False)
            self.show_btn.setVisible(False)
            self.url_label.setVisible(True)
            self.url_input.setVisible(True)
            self.url_input.setText("http://localhost:11434")
            self.model_label.setText("Model:")
            self.model_input.setText("qwen2.5:7b" if "qwen" in self.model_input.text() else "llama3.2:3b")
            self.hint_label.setText("💡 100% Offline & Free. Requires Ollama installed (run: 'ollama run qwen2.5:7b' or 'llama3.2:3b')")
        elif prov == "openai":
            self.api_key_label.setVisible(True)
            self.api_key_input.setVisible(True)
            self.show_btn.setVisible(True)
            self.api_key_input.setPlaceholderText("not-needed (for local) or API key")
            self.url_label.setVisible(True)
            self.url_input.setVisible(True)
            self.url_input.setText("http://localhost:1234/v1")
            self.model_label.setText("Model:")
            self.model_input.setText("local-model")
            self.hint_label.setText("💡 Works with LM Studio, LocalAI, Jan, or any OpenAI-compatible local server.")
        elif prov == "groq":
            self.api_key_label.setVisible(True)
            self.api_key_input.setVisible(True)
            self.show_btn.setVisible(True)
            self.api_key_input.setPlaceholderText("gsk_...")
            self.url_label.setVisible(True)
            self.url_input.setVisible(True)
            self.url_input.setText("https://api.groq.com/openai/v1")
            self.model_label.setText("Model:")
            self.model_input.setText("llama-3.3-70b-versatile")
            self.hint_label.setText("💡 Ultra-fast free cloud inference. Get free key at console.groq.com.")

    def _on_test_connection(self):
        prov = self.provider_combo.currentData()
        self.status_lbl.setText("⏳ Testing connection...")
        self.status_lbl.setStyleSheet("color: #e0f0ff; font-size: 11px;")
        QApplication.processEvents()

        import urllib.request
        try:
            if prov == "gemini":
                key = self.api_key_input.text().strip()
                if not key:
                    self.status_lbl.setText("❌ Error: API key is empty.")
                    self.status_lbl.setStyleSheet("color: #ff6666; font-size: 11px;")
                    return
                url = f"https://generativelanguage.googleapis.com/v1beta/models?key={key}"
                req = urllib.request.Request(url)
                with urllib.request.urlopen(req, timeout=6) as res:
                    if res.status == 200:
                        self.status_lbl.setText("✅ Google Gemini API connected successfully!")
                        self.status_lbl.setStyleSheet("color: #33ff88; font-size: 11px; font-weight: bold;")
            elif prov == "ollama":
                base_url = self.url_input.text().strip().rstrip("/")
                req = urllib.request.Request(f"{base_url}/api/tags")
                with urllib.request.urlopen(req, timeout=4) as res:
                    if res.status == 200:
                        self.status_lbl.setText("✅ Ollama local engine connected successfully!")
                        self.status_lbl.setStyleSheet("color: #33ff88; font-size: 11px; font-weight: bold;")
            elif prov in ("openai", "groq"):
                base_url = self.url_input.text().strip().rstrip("/")
                key = self.api_key_input.text().strip()
                headers = {"Authorization": f"Bearer {key}"} if key else {}
                req = urllib.request.Request(f"{base_url}/models", headers=headers)
                with urllib.request.urlopen(req, timeout=6) as res:
                    if res.status == 200:
                        self.status_lbl.setText("✅ OpenAI-compatible server connected successfully!")
                        self.status_lbl.setStyleSheet("color: #33ff88; font-size: 11px; font-weight: bold;")
        except Exception as e:
            self.status_lbl.setText(f"❌ Connection failed: {str(e)[:65]}")
            self.status_lbl.setStyleSheet("color: #ff6666; font-size: 11px;")

    def _on_save(self):
        prov = self.provider_combo.currentData()
        cfg = _read_full_config()
        cfg["llm_provider"] = prov
        cfg["llm_url"] = self.url_input.text().strip()
        cfg["llm_model"] = self.model_input.text().strip()
        cfg["assistant_name"] = self.name_input.text().strip() or "ULTRON"
        cfg["morning_brief_enabled"] = self.brief_check.isChecked()

        if prov == "gemini":
            key = self.api_key_input.text().strip()
            if not key or key == _PLACEHOLDER_KEY:
                QMessageBox.warning(self, "ULTRON", "Please enter a valid Gemini API key or switch to Ollama.")
                return
            cfg["gemini_api_key"] = key
            self.result_key = key
        elif prov == "groq":
            cfg["groq_api_key"] = self.api_key_input.text().strip()
            self.result_key = cfg["groq_api_key"]
        elif prov == "openai":
            cfg["openai_api_key"] = self.api_key_input.text().strip()
            self.result_key = "local"
        else:
            self.result_key = "ollama"

        _save_full_config(cfg)
        self.accept()


# Alias for backward compatibility
ApiKeyDialog = EngineSettingsDialog


class _RootShim:
    def __init__(self, app: QApplication):
        self._app = app
    def mainloop(self):
        self._app.exec()
    def protocol(self, *_):
        pass


class UltronWebWindow(QMainWindow):
    _state_sig = pyqtSignal(str)
    _log_sig = pyqtSignal(str)
    _content_sig = pyqtSignal(str, str)
    _reconfig_sig = pyqtSignal()
    _camera_sig = pyqtSignal(bytes)

    def __init__(self, face_path: str = "face.png"):
        super().__init__()
        self.setWindowTitle("ULTRON OS — Next-Gen AI Operating System")
        self.resize(1280, 800)
        self.setMinimumSize(900, 600)

        pal = self.palette()
        pal.setColor(QPalette.ColorRole.Window, QColor("#020510"))
        self.setPalette(pal)
        self.setAutoFillBackground(True)

        self._muted = False
        self._ready = True
        self._assistant_name = _read_full_config().get("assistant_name", "ULTRON") or "ULTRON"
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None

        import time
        self._last_reload_time = 0.0

        if _WEBENGINE_OK:
            self._web = QWebEngineView(self)
            if hasattr(self._web, "page") and self._web.page():
                self._web.page().setBackgroundColor(QColor("#020510"))
                if hasattr(self._web.page(), "renderProcessTerminated"):
                    self._web.page().renderProcessTerminated.connect(self._on_render_process_terminated)
            self._web.titleChanged.connect(self._on_title_changed)
            settings = self._web.settings()
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalStorageEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.JavascriptEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.Accelerated2dCanvasEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.WebGLEnabled, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.ScrollAnimatorEnabled, True)
            
            # --- FIX: Allowing CORS and Local Files for HTML WebGL ---
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessRemoteUrls, True)
            settings.setAttribute(QWebEngineSettings.WebAttribute.LocalContentCanAccessFileUrls, True)

            self.setCentralWidget(self._web)
            
            # --- Pointing to current directory app.html ---
            target_path = BASE_DIR / "dashboard" / "static" / "app.html"

            self._web.setUrl(QUrl.fromLocalFile(str(target_path)))
        else:
            container = QWidget()
            lbl = QLabel("ULTRON OS — Loading GUI...", container)
            lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
            layout = QVBoxLayout(container)
            layout.addWidget(lbl)
            self.setCentralWidget(container)

        self._state_sig.connect(self._on_state)
        self._log_sig.connect(self._on_log)
        self._content_sig.connect(self._on_content)
        self._reconfig_sig.connect(self._on_reconfig)

        # Keyboard shortcuts for settings
        self._shortcut_settings = QShortcut(QKeySequence("Ctrl+,"), self)
        self._shortcut_settings.activated.connect(lambda: self._on_reconfig(""))
        self._shortcut_f2 = QShortcut(QKeySequence("F2"), self)
        self._shortcut_f2.activated.connect(lambda: self._on_reconfig(""))

        if _needs_api_key():
            self._ready = False
            QTimer.singleShot(300, lambda: self._on_reconfig(""))

    def _on_title_changed(self, title: str):
        if title.startswith("CMD:"):
            cmd = title[4:].strip()
            if cmd in ("__OPEN_SETTINGS__", "/settings", "settings"):
                self._on_reconfig("")
                return
            if cmd and callable(self.on_text_command):
                self.on_text_command(cmd)

    def _on_render_process_terminated(self, termination_status, exit_code):
        import time
        now = time.time()
        print(f"[ULTRON GUI WARNING] WebEngine Render Process Terminated (status: {termination_status}, exit code: {exit_code}).", file=sys.stderr)
        if hasattr(self, "_web") and self._web and (now - getattr(self, "_last_reload_time", 0) > 5.0):
            self._last_reload_time = now
            self._web.reload()

    def _eval_js(self, js_code: str):
        if _WEBENGINE_OK and hasattr(self, "_web") and self._web.page():
            try:
                self._web.page().runJavaScript(js_code)
            except Exception as e:
                print(f"[ULTRON GUI] _eval_js warning: {e}", file=sys.stderr)

    def _on_state(self, state: str):
        js = f"if (typeof updateAIState === 'function') updateAIState('{state}');"
        self._eval_js(js)

    def _on_log(self, text: str):
        escaped = json.dumps(text)
        js = f"if (typeof addMemoryLog === 'function') addMemoryLog({escaped});"
        self._eval_js(js)

    def _on_content(self, title: str, text: str):
        escaped_title = json.dumps(title)
        escaped_text = json.dumps(text)
        js = f"if (typeof addChatMessage === 'function') addChatMessage({escaped_title}, {escaped_text});"
        self._eval_js(js)

    def _on_reconfig(self, error_message: str = ""):
        dlg = EngineSettingsDialog(self, error_message)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            self._assistant_name = _read_full_config().get("assistant_name", "ULTRON") or "ULTRON"
            self._ready = True
            self._on_log(f"SYS: Engine settings updated ({_read_full_config().get('llm_provider', 'gemini')}).")
        else:
            if _needs_api_key():
                QApplication.quit()

    def _toggle_mute(self):
        self._muted = not self._muted
        state = "MUTED" if self._muted else "LISTENING"
        self._on_state(state)

    def notify_phone_connected(self):
        self._eval_js("if (typeof showToast === 'function') showToast('PHONE CONNECTED', 'Remote device paired');")

    def start_camera_stream(self):
        raise NotImplementedError("Camera stream not yet implemented")

    def stop_camera_stream(self):
        raise NotImplementedError("Camera stream not yet implemented")


class UltronUI:
    def __init__(self, face_path: str = "face.png", size=None):
        _setup_qt_environment()
        self._app = QApplication.instance() or QApplication(sys.argv)
        self._app.setStyle("Fusion")
        self._win = UltronWebWindow(face_path)
        self._win.show()
        self.root = _RootShim(self._app)

    @property
    def muted(self) -> bool:
        return self._win._muted

    @muted.setter
    def muted(self, v: bool):
        if v != self._win._muted:
            self._win._toggle_mute()

    @property
    def current_file(self) -> str | None:
        return None

    @property
    def on_text_command(self):
        return self._win.on_text_command

    @on_text_command.setter
    def on_text_command(self, cb):
        self._win.on_text_command = cb

    @property
    def on_remote_clicked(self):
        return self._win.on_remote_clicked

    @on_remote_clicked.setter
    def on_remote_clicked(self, cb):
        self._win.on_remote_clicked = cb

    @property
    def on_interrupt(self):
        return self._win.on_interrupt

    @on_interrupt.setter
    def on_interrupt(self, cb):
        self._win.on_interrupt = cb

    def notify_phone_connected(self) -> None:
        self._win.notify_phone_connected()

    def set_state(self, state: str):
        self._win._state_sig.emit(state)

    def write_log(self, text: str):
        self._win._log_sig.emit(text)

    def wait_for_api_key(self):
        while not self._win._ready:
            time.sleep(0.1)

    def show_content(self, title: str, text: str):
        """Thread-safe: display content in the UI."""
        self._win._content_sig.emit(title[:48], text[:4000])

    def prompt_reconfig(self):
        """Thread-safe: show API key setup overlay if needed."""
        self._win._ready = False
        self._win._reconfig_sig.emit()

    def show_camera_frame(self, img_bytes: bytes):
        raise NotImplementedError("Camera stream not yet implemented")

    def start_camera_stream(self) -> None:
        self._win.start_camera_stream()

    def stop_camera_stream(self) -> None:
        self._win.stop_camera_stream()

    @property
    def assistant_name(self) -> str:
        return self._win._assistant_name

    def start_speaking(self):
        self.set_state("SPEAKING")

    def stop_speaking(self):
        if not self.muted:
            self.set_state("LISTENING")


# Backward compatibility alias
JarvisUI = UltronUI