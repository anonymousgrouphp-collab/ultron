# ULTRON Deep Audit Report: Integration, User Interface, and Batch Scripts

**Audit Date**: 2026-09-12  
**Auditor**: Antigravity Assistant (`/boost` Deep Audit Mode)  
**Target Branch**: `main` @ tip  
**Runtime**: System Python 3.14.7 (CPython 64-bit on Windows 11)  
**Methodology**: Dynamic Introspection, AST Static Analysis, Contract Tracing, Process Lifecycles, and Multi-layer Integration Verification  
**Scope**: 
1. **Windows Batch Scripts** (`SETUP.bat`, `START_ULTRON.bat`, `Start_ULTRON_Wake_Word.bat`, `ULTRON_SETUP.py`, `wake_service.py`)
2. **User Interfaces** (Desktop GUI `ui.py`, Web Dashboard `dashboard/server.py`, `app.html`, `login.html`, WebGL / HUD)
3. **Subsystem Integration** (`main.py` Composition Root, `app/*` Mixins, `kernel/*` Subsystems, EventBus, ToolRegistry, Voice Stack, Memory Formation)

---

## 1. Executive Summary

A deep architectural and code-level audit was conducted across the three requested target areas: **Integration**, **UI**, and **Batch (.bat) Scripts**.

### Key Statistics
- **Total Test Suite**: 637 passed, 4 skipped (`pytest -q` in 31.5s).
- **Kill-List Compliance**: PASS (all 6 scopes clean).
- **Registered Tools**: 33 declared and active tools with verified schemas and risk ratings.
- **Identified Findings**: **16 findings** across 3 domains (2 Critical S1, 6 High S2, 5 Medium S3, 3 Low S4).

### Top 5 Critical Takeaways
1. **Broken Bootstrap (`SETUP.bat:15` vs `ULTRON_SETUP.py:18` / S1)**: `SETUP.bat` hardcodes `py -3.13 ULTRON_SETUP.py`, while `ULTRON_SETUP.py` validates `SUPPORTED_PYTHON = (3, 14)` and raises `RuntimeError` on 3.13. Furthermore, on this Python 3.14 system, `py -3.13` fails immediately because 3.13 is not installed. `SETUP.bat` is 100% non-functional.
2. **The "Ollama / Multi-Provider Trap" (`main.py:81, 518` / S1)**: `ui.py` allows selecting Ollama without an API key (`_needs_api_key()` returns `False`), but `main.py`'s `_get_api_key()` unconditionally demands a `gemini_api_key` for `LiveSession`. Selecting Ollama or local LLMs traps the user in an inescapable infinite loop of API key error dialogs.
3. **UI Command Dropping on Duplicate Titles (`ui.py:550` & `app.html:743` / S2)**: Desktop WebEngine commands bridge via `document.title = 'CMD:' + t`. Because neither `app.html` nor `ui.py` resets `document.title` back to the default title, consecutive identical commands (e.g. double-clicking "Open browser", typing `/cost` twice) do NOT trigger Chromium's `titleChanged` signal and are silently dropped.
4. **Voice Stack Integration Void (`app/voice_stack.py` / S2)**: Despite `PROGRESS.md` logging Phase P1 as complete and wired, `app/audio.py`'s mic callback never invokes `gate_mic_frame()`, `note_tts_started()`, `note_tts_finished()`, or `identify_speaker()`. `EchoGate` and `SpeakerIdEngine` are 100% uncalled dead code in production.
5. **Web Dashboard Consent Blindness (`dashboard/server.py` & `app/consent.py` / S2)**: When commands requiring user consent (`RiskClass.WRITE` or `RiskClass.EXECUTE`) are triggered via the web dashboard, the approval dialog opens strictly as a `QMessageBox` on the desktop Qt window. The web interface has no consent UI or WebSocket events; remote requests hang for 45 seconds and fail closed.

---

## 2. Findings Register

| ID | Domain | Severity | File:Line | Finding Summary | Impact |
| :--- | :---: | :---: | :--- | :--- | :--- |
| **BAT-01** | BAT | **S1** | `SETUP.bat:10, 15` | `SETUP.bat` invokes `py -3.13` contradicting `ULTRON_SETUP.py` (requires 3.14) | Setup fails immediately on both 3.13 and 3.14 systems |
| **BAT-02** | BAT | **S2** | `START_ULTRON.bat:6-12` | Hardcodes `.venv` path; fails to launch via system Python 3.14 | Cannot launch after user-ordered `.venv` retirement |
| **BAT-03** | BAT | **S3** | `START_ULTRON.bat:14` | Missing `%*` CLI argument forwarding | Arguments like `--provider` or `--wake-word` silently dropped |
| **BAT-04** | BAT | **S3** | `*.bat` (all) | Missing `chcp 65001` UTF-8 code page configuration | Unicode emojis and status logs cause encoding errors in CMD |
| **BAT-05** | BAT | **S2** | `Start_ULTRON_Wake_Word.bat` | Drives legacy out-of-process stack using removed `audioop` | Fails on Python 3.13+; does nothing if ULTRON is running |
| **UI-01** | UI | **S2** | `ui.py:550`, `app.html:743` | `document.title` never resets after CMD dispatch | Consecutive duplicate commands silently ignored by WebEngine |
| **UI-02** | UI | **S2** | `dashboard/server.py:430`, `app/consent.py:25` | Web dashboard has zero consent seam integration | Web/phone commands requiring consent hang 45s and fail |
| **UI-03** | UI | **S3** | `app.html:1156` | Three.js loaded from external `cdn.jsdelivr.net` CDN | WebGL HUD breaks when offline; violates local-first policy |
| **UI-04** | UI | **S3** | `ui.py:588-603` | Single-variable `_consent_callback` overwritten on concurrent calls | Race condition drops callbacks under concurrent agent actions |
| **UI-05** | UI | **S3** | `ui.py:376-408` | Synchronous `urlopen` blocks Qt main UI loop during engine test | UI freezes for up to 6 seconds; Windows reports "Not Responding" |
| **UI-06** | UI | **S4** | `ui.py:524-531` | Missing fallback interface when `PyQt6-WebEngine` unavailable | Permanent frozen "Loading GUI..." splash screen |
| **INT-01** | INT | **S1** | `main.py:81, 518`, `ui.py:132` | `main.py` unconditionally requires Gemini key even when Ollama is selected | Infinite API key prompt loop; offline LLMs impossible to use |
| **INT-02** | INT | **S2** | `app/voice_stack.py:51-83`, `app/audio.py:58` | `VoiceStackMixin` completely uncalled by live mic callback | `EchoGate` and `SpeakerIdEngine` are 100% dormant dead code |
| **INT-03** | INT | **S2** | `kernel/memory/improve.py:186`, `main.py` | `SkillCaptureListener` and `ImprovementService` not wired to bus | Completed orchestrator jobs never deposit procedural skills |
| **INT-04** | INT | **S2** | `app/monitors.py:145`, `app/audio.py:253` | Tight `asyncio.wait_for(..., timeout=0.02)` busy-polling loops | Generates 100 TimerHandles/sec; unnecessary CPU churn |
| **INT-05** | INT | **S3** | `app/commands.py:100` | Non-slash typed commands discarded during session reconnection | Local tools fail to execute when Gemini Live stream is reconnecting |
| **INT-06** | INT | **S4** | `main.py:499`, `dashboard_bridge.py:62` | `BusDashboardBridge.detach()` not invoked on session teardown | Lingering event listeners accumulate on EventBus across reconnects |

---

## 3. Chapter 1: Deep Audit of Windows Batch Scripts

### 1.1 `SETUP.bat` Analysis
```cmd
7: where py >nul 2>nul
8: if errorlevel 1 (
9:     echo Python Launcher was not found.
10:     echo Install CPython 3.13 for Windows, including the Python Launcher, then run this again.
11:     pause
12:     exit /b 1
13: )
14: 
15: py -3.13 ULTRON_SETUP.py %*
```
#### Defects Identified:
1. **Runtime Contradiction with `ULTRON_SETUP.py`**:
   - `SETUP.bat` line 15 explicitly executes `py -3.13`.
   - `ULTRON_SETUP.py` line 18 specifies `SUPPORTED_PYTHON = (3, 14)` and line 47 enforces:
     `raise RuntimeError(f"ULTRON requires Python {expected}; found Python {actual}.")`
   - `tests/test_setup.py:35-41` explicitly test-pins that Python 3.13 is rejected:
     `with pytest.raises(RuntimeError, match="Python 3.14"): setup.validate_python_version((3, 13))`
   - If Python 3.13 is invoked, `ULTRON_SETUP.py` aborts with `RuntimeError`.
   - If Python 3.14 is the only installed version (as on this machine: `py -0` -> `Python 3.14 (64-bit)`), `py -3.13` fails immediately with:
     `Requested Python version (3.13) is not installed, quitting.`
2. **Missing `python` Fallback**:
   - If the `py.exe` launcher is not installed (e.g. Python installed via MS Store or Scoop/Conda), `where py` fails even if `python.exe` is in `PATH`.

---

### 1.2 `START_ULTRON.bat` Analysis
```cmd
6: set "ULTRON_PYTHON=.venv\Scripts\python.exe"
7: 
8: if not exist "%ULTRON_PYTHON%" (
9:     echo ULTRON is not set up yet. Creating the supported Python 3.13 environment...
10:     call SETUP.bat
11:     if errorlevel 1 exit /b %ERRORLEVEL%
12: )
13: 
14: "%ULTRON_PYTHON%" main.py
```
#### Defects Identified:
1. **Hardcoded `.venv` vs Retired Environment**:
   - Per `PROGRESS.md:3`: *"Runtime: system Python 3.14 (user order; .venv retired)"*.
   - If `.venv` does not exist, `START_ULTRON.bat` calls `SETUP.bat` (which fails due to BAT-01).
   - It fails to probe system Python `python -c "import main"` or use `py -3.14`.
2. **Missing CLI Argument Forwarding**:
   - Line 14 executes `"%ULTRON_PYTHON%" main.py` without `%*`.
   - Arguments such as `--wake-word`, `--provider`, or `--headless` cannot be passed.
3. **Outdated Status Output**:
   - Line 9 claims `Creating the supported Python 3.13 environment...` (stale 3.13 reference).

---

### 1.3 `Start_ULTRON_Wake_Word.bat` and `wake_service.py` Analysis
```cmd
14: "%ULTRON_PYTHON%" -c "import pyaudio" >nul 2>nul
15: if errorlevel 1 (
16:     echo The experimental wake-word service requires PyAudio, which is not part of the supported core install.
17:     echo Install a compatible PyAudio wheel into .venv, then run this launcher again.
18:     pause
19:     exit /b 1
20: )
```
#### Defects Identified:
1. **Python 3.14 Incompatibility (`audioop` removed)**:
   - In Python 3.13+, `audioop` was removed from the standard library (PEP 594).
   - In `wake_service.py:172`, `import audioop` fails, breaking VAD energy pre-filtering.
2. **PyAudio Wheel Deficit**:
   - Pre-built PyAudio wheels do not exist for Python 3.14 on Windows; pip requires MSVC C++ Build Tools to compile PortAudio, causing setup to stall.
3. **Architectural Non-Operation**:
   - In `wake_service.py:208`:
     `if _is_ultron_running(): _log("✅ ULTRON is already running — no action needed.")`
   - When ULTRON is running, `wake_service.py` is completely inert. It does not unmute the assistant, inject a wake event into EventBus, or activate listening.

---

## 4. Chapter 2: Deep Audit of User Interfaces (Qt & Web Dashboard)

### 2.1 Desktop GUI (`ui.py`)
#### Finding UI-01: `document.title` Never Resets After CMD Dispatch
- **Mechanism**:
  In `app.html:743`:
  ```javascript
  window.sendBackendCommand = t => {
    if (!t) return;
    document.title = 'CMD:' + t;
    if (local) return;
    ...
  ```
  In `ui.py:550-558`:
  ```python
  def _on_title_changed(self, title: str):
      if title.startswith("CMD:"):
          cmd = title[4:].strip()
          if cmd in ("__OPEN_SETTINGS__", "/settings", "settings"):
              self._on_reconfig("")
              return
          if cmd and callable(self.on_text_command):
              self.on_text_command(cmd)
  ```
- **Root Cause**: Qt WebEngine / Chromium only fires `titleChanged` when `new_title != old_title`. If a user types `/cost` or clicks a quick action button twice in succession, `document.title` remains `'CMD:/cost'`, and the second click triggers no event.
- **Remediation**:
  In `ui.py`, immediately reset the title via `self._eval_js("document.title = 'ULTRON // Holographic Command Interface';")` after consuming the command, or append an incremental nonce in `app.html`.

#### Finding UI-04: Race Condition in `ConsentGate` Single Callback
- **Mechanism**:
  In `ui.py:588-596`:
  ```python
  def _consent_request(self, tool_name: str, risk: str, args_summary: str, callback) -> None:
      self._consent_callback = callback
      self._consent_sig.emit(tool_name, risk, args_summary)
  ```
- **Root Cause**: `self._consent_callback` stores only a single callback reference. If two tools request consent in rapid succession (e.g. an orchestrator executing parallel steps), the second invocation overwrites `self._consent_callback`. The first dialog response will invoke the second callback, and the second dialog response will find `_consent_callback is None`, denying the request.
- **Remediation**: Use a thread-safe correlation map (`dict[str, Callable]`) with a unique `request_id` passed through `_consent_sig`.

#### Finding UI-05: Synchronous Network Probe in Qt GUI Thread
- **Mechanism**:
  In `EngineSettingsDialog._on_test_connection()` (`ui.py:376-408`), `urllib.request.urlopen(req, timeout=6)` runs directly on the UI thread.
- **Root Cause**: If the host is unreachable or lagging, the entire UI thread blocks synchronously for up to 6 seconds, freezing window rendering and causing OS "Not Responding" warnings.
- **Remediation**: Execute `_on_test_connection` inside a `QThread` or `threading.Thread` with a completion signal.

---

### 2.2 Web Dashboard (`dashboard/server.py`, `app.html`)
#### Finding UI-02: Zero Web Consent Seam Integration
- **Mechanism**:
  When a web user types a command that triggers a `WRITE` or `EXECUTE` tool (e.g. `desktop_control`, `file_controller`, `spawn_app`), the backend invokes `ConsentGate.request()`.
  `ConsentGate` dispatches only to `self._ui._win._consent_request`, popping a dialog on the host desktop.
- **Root Cause**: `dashboard_bridge.py` does not forward consent requests to the dashboard WebSocket, and `dashboard/server.py` has no endpoint to submit approval.
- **Remediation**: Add `consent.request` and `consent.response` events to `dashboard_bridge.py` and `dashboard/server.py` with an interactive approval card in `app.html`.

#### Finding UI-03: External CDN Dependency in Offline Architecture
- **Mechanism**:
  `app.html:1156`:
  `<script type="importmap">{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.1/build/three.module.js","three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.1/examples/jsm/"}}</script>`
- **Root Cause**: Three.js is loaded from `jsdelivr.net` at runtime rather than vendored locally in `dashboard/static/`.
- **Remediation**: Vendor Three.js and post-processing passes into `dashboard/static/vendor/three/` and serve locally via FastAPI.

---

## 5. Chapter 3: Deep Audit of Subsystem Integration

### 3.1 The "Ollama / Multi-Provider Trap" (Finding INT-01)
- **Mechanism**:
  1. User selects `Ollama` in `EngineSettingsDialog`.
  2. `ui._needs_api_key()` checks `provider == "ollama"` and returns `False`.
  3. `ui.py` saves configuration without requiring a Gemini API key.
  4. `main.py:516-520` attempts to start `LiveSession`:
     ```python
     live = LiveSession(
         settings=GatewaySettings.from_config(loader.load_config()),
         api_key=_get_api_key(),
     )
     ```
  5. `_get_api_key()` calls `loader.get_api_key("gemini_api_key")`, which returns `None` and raises:
     `ApiKeyMissing("config/api_keys.json is missing, invalid, or has no real Gemini key")`
  6. `main.py:588-596` catches `ApiKeyMissing` and displays an error message, reopening the settings dialog.
- **Root Cause**: `main.py` is architecturally locked to Gemini Live audio. While `kernel/gateway` supports Ollama for text completion and agent planning, `LiveSession` requires a Gemini API key. The UI advertises 100% offline Ollama operation, but the composition root crashes if Gemini credentials are absent.
- **Remediation**: When `llm_provider != "gemini"` and no Gemini key is provided, `main.py` should disable `LiveSession` and enter a standalone Text/Agent loop mode (using microphone STT + TTS or text-only console/dashboard).

---

### 3.2 Voice Stack Dormancy (Finding INT-02)
- **Mechanism**:
  `VoiceStackMixin` (`app/voice_stack.py`) was introduced in Phase P1:
  - `_voice_gate = EchoGate()`
  - `_speaker_engine = load_speechbrain()`
  - `gate_mic_frame(frame)`
  - `note_tts_started()` / `note_tts_finished()`
  - `identify_speaker(audio)`
- **Discrepancy**:
  In `app/audio.py:58-67`:
  ```python
  def callback(indata, frames, time_info, status):
      with self._speaking_lock:
          ultron_speaking = self._is_speaking
      if not ultron_speaking and not self.ui.muted and not self._phone_active:
          data = indata.tobytes()
          loop.call_soon_threadsafe(
              _safe_put,
              {"data": data, "mime_type": "audio/pcm"}
          )
  ```
  `callback` never calls `self.gate_mic_frame()`.
  In `app/audio.py:266` and `main.py:263` (`set_speaking`): neither calls `self.note_tts_started()` or `self.note_tts_finished()`.
- **Root Cause**: Phase P1 implemented the mixin methods, but the call-site migration in `app/audio.py` was never completed.
- **Remediation**: Wire `gate_mic_frame` into the audio callback and hook `note_tts_started/finished` into `set_speaking()`.

---

### 3.3 Procedural Memory Self-Improvement Dormancy (Finding INT-03)
- **Mechanism**:
  `kernel/memory/improve.py:186-243` defines `SkillCaptureListener`:
  ```python
  class SkillCaptureListener:
      def attach(self, bus: EventBus) -> None:
          bus.subscribe("job.started", self._on_started)
          bus.subscribe("job.completed", self._on_completed)
  ```
  `kernel/memory/improvement_service.py` defines `ImprovementService`.
- **Discrepancy**: Neither class is ever imported, instantiated, or attached to `self._bus` in `main.py`.
- **Remediation**: In `UltronLive.__init__`, instantiate `SkillCaptureListener(self._memory).attach(self._bus)`.

---

### 3.4 20ms Polling Busy Loops (Finding INT-04)
- **Locations**:
  1. `app/monitors.py:146`: `item = await asyncio.wait_for(self._dashboard._command_queue.get(), timeout=0.02)`
  2. `app/audio.py:254`: `chunk = await asyncio.wait_for(self.audio_in_queue.get(), timeout=0.02)`
- **Root Cause**: `wait_for(0.02)` was used as a workaround to allow periodic loop cancellation and state checks. However, creating 100 `asyncio.TimerHandle` objects per second degrades event loop responsiveness and prevents low-power CPU idle.
- **Remediation**: Use `await queue.get()` directly, or use an `asyncio.Event` listener to wake up on turn completion without timer polling.

---

## 6. Actionable Remediation Diffs

### Diff 1: Fix `SETUP.bat` to Support Python 3.14 Runtime
```diff
--- a/SETUP.bat
+++ b/SETUP.bat
@@ -7,14 +7,20 @@
 where py >nul 2>nul
 if errorlevel 1 (
-    echo Python Launcher was not found.
-    echo Install CPython 3.13 for Windows, including the Python Launcher, then run this again.
-    pause
-    exit /b 1
+    where python >nul 2>nul
+    if errorlevel 1 (
+        echo Python was not found in PATH.
+        echo Install CPython 3.14 for Windows, then run this again.
+        pause
+        exit /b 1
+    )
+    set "PY_CMD=python"
+) else (
+    set "PY_CMD=py -3.14"
 )
 
-py -3.13 ULTRON_SETUP.py %*
+%PY_CMD% ULTRON_SETUP.py %*
 set "SETUP_EXIT=%ERRORLEVEL%"
```

### Diff 2: Fix `START_ULTRON.bat` Argument Passing and Python 3.14 Fallback
```diff
--- a/START_ULTRON.bat
+++ b/START_ULTRON.bat
@@ -6,12 +6,17 @@
 set "ULTRON_PYTHON=.venv\Scripts\python.exe"
 
 if not exist "%ULTRON_PYTHON%" (
-    echo ULTRON is not set up yet. Creating the supported Python 3.13 environment...
-    call SETUP.bat
-    if errorlevel 1 exit /b %ERRORLEVEL%
+    where py >nul 2>nul
+    if not errorlevel 1 (
+        set "ULTRON_PYTHON=py -3.14"
+    ) else (
+        set "ULTRON_PYTHON=python"
+    )
 )
 
-"%ULTRON_PYTHON%" main.py
+chcp 65001 >nul
+%ULTRON_PYTHON% main.py %*
 set "ULTRON_EXIT=%ERRORLEVEL%"
```

### Diff 3: Fix `ui.py` Command Dropping by Resetting Title
```diff
--- a/ui.py
+++ b/ui.py
@@ -555,6 +555,8 @@
                 self._on_reconfig("")
                 return
             if cmd and callable(self.on_text_command):
+                self._eval_js("document.title = 'ULTRON // Holographic Command Interface';")
                 self.on_text_command(cmd)
```

### Diff 4: Wire `SkillCaptureListener` into `main.py`
```diff
--- a/main.py
+++ b/main.py
@@ -162,6 +162,8 @@
         self._proactive.attach(self._bus)
         self._bus.subscribe("proactive.decision", self._on_proactive_decision)
+        from kernel.memory.improve import SkillCaptureListener
+        self._skill_listener = SkillCaptureListener(self._memory)
+        self._skill_listener.attach(self._bus)
```

---

## 7. Verification and Test Invariants

All findings were verified against current code and execution state:
1. `SETUP.bat` fails when run with `py -3.13` (Python 3.13 not installed; `ULTRON_SETUP.py` rejects 3.13).
2. Full test suite: **637 passed, 4 skipped in 31.5s**.
3. Kill-list checker: **PASS (6 scope entries clean)**.
4. AST and grep inspection confirms 0 callers for `gate_mic_frame`, `note_tts_started`, `note_tts_finished`, and `identify_speaker`.
5. AST inspection confirms `document.title` in `app.html` only set at line 743 and never reset.

---
*Report generated for repository: anonymousgrouphp-collab/ultron*
