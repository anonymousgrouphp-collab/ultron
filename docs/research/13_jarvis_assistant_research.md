# 13 — PERSONAL-JARVIS-AI-ASSISTANT deep-read (surajmaru)

**Repo:** https://github.com/surajmaru/PERSONAL-JARVIS-AI-ASSISTANT — cloned shallow to
`C:/Users/ceoha/repos/personal-jarvis` (2026-09-14, this stream).
**Provenance quirk:** the repo's `main` tip (`4ef8dc5 "Delete assistant.py"`) ships **only
README.md + guide.txt — zero code**. The code survives only in git history; the most
complete state is commit `2621801` (`assistant.py` 1,006 lines + `assistant-pt2.py` 837
lines), recovered with `git show 2621801:<file>`. The tag `jarvis` contains no code either.
Author history: initial upload (9e38622, 408-line `assistant.py` with wttr.in weather) →
`JARVIS.py`/`JARVIS-PT2.py` renames → feature growth (window mgmt, brightness, HID unlock)
→ deletions (LICENSE, assistant.py, pt2). Read here: **all 1,843 lines of the fullest tree
plus the 408-line first version** — every line of code that exists in this repo.

**One-line verdict:** a hobbyist single-file Windows voice assistant (Ollama Mistral +
edge-tts + SpeechRecognition + CustomTkinter + pyautogui + ESP32 serial) whose *architecture
choices independently validate ULTRON's stack*, but whose implementation is ~2 years behind
ULTRON on every axis. Genuine adoptables are small and few: brightness control, OS power
verbs, a raw-desktop-input tier idea, WhatsApp-Desktop automation (risky), and one genuinely
novel hardware trick — **PC unlock via ESP32 USB-HID keystroke injection at the Windows
login screen** (no software path can do that). Adopt list **PJ-01…PJ-06**.

---

## 1. What the repo actually is

Single Python process, no packaging, no tests, no separation of concerns. Two near-duplicate
files (`assistant.py` = newer, `assistant-pt2.py` = older variant — a living Kill-List #2
violation). Everything in one module: config globals, memory, TTS worker, STT listener,
~370-line keyword command router, CustomTkinter GUI, all module-level side effects.

Runtime flow:
1. `start_ollama_mistral()` — probe `127.0.0.1:11434`, if closed `Popen(["ollama","run","mistral"])`,
   poll the port up to 30 s (assistant.py L69–96).
2. Load `memory.json` (system prompt + last 20 turns), insert a system prompt if missing (L103–132).
3. Open serial `COM10 @115200` to an ESP32 (pt2: `COM5 @9600` Arduino) — lights on/off + PC
   unlock via HID (L138–153).
4. Start a daemon TTS worker thread that owns a private asyncio loop and drains a sentence
   queue through edge-tts → temp wav → sounddevice playback (L228–256).
5. Start `speech_recognition` background listener (Google Web STT, `en-IN`,
   `phrase_time_limit=5`) whose callback spawns a thread per utterance (L810–836).
6. CustomTkinter dark GUI (`#1e1e2f`), Send + ⏹ Stop buttons, streaming text inserts via
   `app.after(0, …)` (L743–913).
7. `mainloop()`. (A `PERSONALITY_PROMPT` is defined *after* mainloop at L996–1006 — dead code,
   never referenced.)

Per utterance: local command router first (`handle_local_command`, L374–737) — if it matches,
reply without the LLM; else stream `ollama.chat(model="mistral", stream=True)`, append tokens
to the GUI, split the growing buffer into complete sentences with
`re.split(r'(?<=[.!?])\s+', buffer)`, and enqueue each finished sentence to the TTS worker —
speech starts before generation finishes. Memory is appended + saved under a `threading.Lock`.

## 2. Deep-read findings (what each piece does, and what's wrong/right)

**The one genuinely excellent pattern — streaming TTS pipeline with hard stop (L160–256,
L862–911, L922–958).** Three pieces: (a) sentence-boundary queueing so first audio lands
mid-generation; (b) a global `stop_flag = threading.Event()` checked in *every* loop — the
LLM stream chunk loop, the sounddevice playback poll (50 ms), the TTS queue drain, and the
STT callback; (c) the Stop button does `stop_flag.set()` + `sd.stop()` + queue drain +
temporarily stops the SR listener (so the assistant doesn't hear itself / the user's
next sentence isn't eaten), then clears the flag and restarts SR after 0.6 s. This is a
complete, working barge-in/cancel semantics — the same shape ULTRON's `local_voice._stop_event`
+ `text_splitter.SentenceFeeder` already implement, with ULTRON's splitter being far more
correct (abbreviation/URL/decimal/initial guards vs their naive regex that would speak
"Dr." as a sentence end and split "$4.99").

**Keyword command router with pending-confirmation state machine (L374–737).** Two module
globals (`pending_close_window`, `pending_system_action`) implement yes/no confirmation for
destructive actions (close window, shutdown/restart/logoff) — the same idea as ULTRON's
ConsentGate, but as fragile globals with no timeout, no UI, and next-utterance interception.
The router itself has the classic keyword-router disease:
- `if "time" in command_lower` (L417) intercepts *any* utterance containing "time"
  ("tell me about runtime" → speaks the clock). pt2 is worse: `if "write" in command_lower`
  (pt2 L361) hijacks every sentence containing "write"/"add".
- Shutdown regex matches "shutdown" anywhere, including "how do I shut down Windows".
- pt2 L570 fixes an actual shipped bug that assistant.py L703 still has:
  `f"Pressed hotkey: {'.join(keys)'}"` — the braces contain a string literal, so it
  literally says `Pressed hotkey: '.join(keys)'`.
ULTRON's lesson here is negative: this file is a museum of why the router must stay
LLM+policy-driven with typed tools, not substring matching.

**Window management via pygetwindow + win32gui (L338–364, L540–608).** find-by-substring
title, activate/restore, minimize via `win32gui.FindWindow + ShowWindow(SW_MINIMIZE)`,
maximize, close with confirmation. No pid scoping, no allowed-set, admits *any* window by
fuzzy title. ULTRON's `kernel/computer` (UIA verbs + `spawn_app` pid-diffed window
admission + verb-layer DESTRUCTIVE consent) does the same job safely.

**Desktop/OS control via pyautogui (L612–723).** Tab hotkeys, volume media keys, brightness
via `screen_brightness_control` with `%` parsing (L637–653), mouse click/move/scroll, raw
`type`/`press`, timestamped screenshot, "play X on youtube/spotify" deep-link table,
`open <site>` fuzzy mapping with `.com` suffix fallback. Two things here ULTRON lacks:
**brightness** (grepped — zero hits in kernel/ + app/) and a **raw-input tier** (ULTRON's
`InputGateway` is UIA-only: `invoke/toggle/set_value/type_keys/press_hotkey/close_window`,
kernel/computer/act.py L40 — no mouse move/click/scroll for non-UIA surfaces).

**WhatsApp Desktop send (L284–309).** Find window titled "WhatsApp" → restore/activate →
`ctrl+f` → type contact name → down-arrow → enter → type message → enter. Works, but it's
blind typing into whatever has focus after any hiccup — a misdirected-message machine.
Pattern is worth documenting; adoption needs consent + focus guarantees (PJ-04).

**ESP32 / USB-HID unlock (L138–153, L447–465) — the novel idea.** Lights via serial
(`LIGHT_ON/OFF`), and `UNLOCK_PC` — the ESP32 is flashed as a **USB HID keyboard** that
types the Windows password *at the login screen*, where no software keyboard automation
(Credential Provider isolation) can reach. This is the only thing in the repo ULTRON
genuinely cannot do in software today; it belongs to the parked hardware stream
(HA box / EchoGate A/B, D13-adjacent).

**Memory (L103–124).** `memory.json`: system messages + last 20 turns, whole-file rewrite on
every exchange. pt2 has no lock around it (L781–803) — a race the newer file fixed with
`memory_lock`. Nothing to adopt (ULTRON: SQLite + FTS5 + vec + embedder seam + formation).

**STT (L810–836).** `recognize_google` (cloud, free tier) in `en-IN`, background listener,
per-utterance thread. ultron already has local faster-whisper + VAD + preroll — strictly
better and offline. Non-adopt (privacy + the offline goal).

**Ollama bootstrap (L69–96).** Port-probe → spawn → poll. ULTRON already ships
`.ultron/start_ollama.ps1` + `OllamaModelManager` with VRAM arbitration — skip.

**Code-quality notes (evidence, not snark):** ~40 bare `except:`/`except Exception: pass`
blocks; GUI/logic/router all in one module; pt2 is a full second implementation of the same
thing (the exact failure mode Kill List #2 bans); wttr.in weather from v1 was dropped; the
v1 weather block (9e38622 L190–201) used `wttr.in/<city>?format=3` — one HTTP GET, cute but
ULTRON's Open-Meteo tool already supersedes it.

## 3. Feature-by-feature vs ULTRON (as of main @ 1a624b2, registry 41 tools)

| Repo feature | ULTRON status | Verdict |
|---|---|---|
| Ollama local LLM + streaming | gateway Ollama adapter + OllamaModelManager VRAM arbiter | covered, better |
| Sentence-streamed TTS + Stop | `app/text_splitter.py` (SentenceFeeder) + `local_voice._stop_event` + EchoGate | covered, better |
| Voice loop (STT→LLM→TTS) | faster-whisper local loop (`app/local_voice.py`), offline | covered, better |
| Persistent chat memory | MemoryEngine + embedder seam + formation | covered, far better |
| Destructive-action confirmation | ConsentGate v2 + dashboard popup + policy engine | covered, better |
| App launching | `spawn_app` (pid-diffed allowed set) | covered, safer |
| Window find/close/min/max | `screen_describe`/`ui_tree`/`ui_act` (UIA) + DESTRUCTIVE verb consent | covered, safer |
| System info | `system_snapshot` tool | covered |
| Weather (wttr.in) | Open-Meteo tool (K9 adopt) | covered |
| Screenshot / screen read | `screen_describe` + live vision (D4) | covered |
| File read/write/create + AI "smart write" | coding tools (`read_file`/`write_file`/`run_python` self-heal) | covered |
| Lights on/off | `kernel/home` (HA/MQTT) — hardware pending | covered in design |
| Volume / media keys | `kernel/media/controller.py` (ctypes VK codes) | covered |
| GUI chat w/ streaming | web dashboard + orchestrator announcements | covered |
| **Brightness control** | **none** (grep: 0 hits) | **gap → PJ-01** |
| **OS power verbs (lock/shutdown/restart/logoff)** | only `shutdown_ultron` (own process) | **gap → PJ-02** |
| **Raw mouse input tier (move/click/scroll)** | UIA-only gateway; pixel tier exists for browser only | **gap → PJ-03** |
| **WhatsApp Desktop send** | none | **gap → PJ-04 (risky)** |
| **USB-HID login-screen unlock** | impossible in software | **hardware phase → PJ-05** |
| edge-tts `\u200b` anti-cutoff pad | n/a (no edge-tts in ULTRON) | note → PJ-06 |

## 4. Adopt list (PJ-01…PJ-06) — patterns & packages, with integration notes

- **PJ-01 · Brightness tool (adopt package).** `screen-brightness-control` exposes
  get/set per display; map into the desktop/media tool family (a `brightness` verb next to
  the existing media controller, or a tiny `desktop_env` tool). Support `set N%`, `up/down
  10` (their L637–653 handler is the right feature spec, wrong home). Verify py-3.14 wheel
  at impl time (package is pure-python over WMI/CTLA — low risk). Windows-only is fine
  (deployment target).
- **PJ-02 · OS power verbs (adopt pattern, consent-gated).** `lock_workstation` via
  `rundll32.exe user32.dll,LockWorkStation` (reversible — treat WRITE), `shutdown`/`restart`
  via `shutdown.exe /s|/r /t 5` and `sign_out` via `/l` — all DESTRUCTIVE-class through
  ConsentGate (their 5-second `shutdown /t` grace + yes/no confirmation is the right UX;
  ConsentGate + dashboard popup already provide the better version of the same). Register
  as verbs on a `system_power` tool; keep `shutdown_ultron` separate.
- **PJ-03 · Raw desktop input tier (adopt pattern, P2).** `InputGateway` is UIA-only; for
  non-accessibility surfaces (games, canvas apps, remote sessions) add a raw-input tier:
  mouse move/click/scroll (+ raw type) via `SendInput`, flag-gated, consent-gated, and
  scoped like the CU pixel tier (coordinates only within an admitted window). Their
  pyautogui surface (L690–710) is the feature spec; ULTRON's allowed-set discipline is the
  safety spec.
- **PJ-04 · WhatsApp Desktop send (document, adopt-if-demanded, P3).** The ctrl+F → type
  name → enter → type message flow (L284–309) works but is blind typing — a focus steal
  between steps sends someone else's chat. If ever adopted: consent-gated, verify the
  WhatsApp window handle before every `typewrite`, and re-check after every step. Honest
  recommendation: leave it in this report; the CU web agent can already do
  web.whatsapp.com with UIA/pixel safety.
- **PJ-05 · ESP32 USB-HID unlock + serial appliances (park with hardware stream).** The one
  thing software can't do: Credential-Provider-isolated login screen only accepts hardware
  HID. When the HA box / hardware stream (D13 area) un-parks: ESP32 flashed as HID keyboard
  + serial command channel mirrors this repo's `LIGHT_ON/UNLOCK_PC` design. Secrets stay on
  the MCU, never in ULTRON.
- **PJ-06 · Micro-patterns (notes only).** (a) edge-tts swallows the first ~100 ms — their
  `"\u200b" + text` pad (L171) is the known workaround; relevant only if edge-tts ever
  enters the TTS seam (report 10 chose Piper/Kokoro instead). (b) Their stop-flag
  semantics (checked in *every* long loop, not just at boundaries) is the correct shape for
  cancellable work — ULTRON's orchestrator/voice loops already follow it; keep it that way.

## 5. Non-adopt list (with reasons)

- **Google Web STT as the ears** — cloud dependency + privacy; ULTRON's faster-whisper
  int8 offline loop supersedes (report 11 / commit e34d841).
- **`memory.json` last-20 chat tail** — no retrieval, no formation, whole-file rewrite;
  ULTRON's SQLite memory stack is a different sport.
- **Keyword/substring command router** — see §2 interception examples; ULTRON's
  LLM→policy→typed-tools router exists precisely to avoid this class of bug.
- **CustomTkinter GUI** — second UI would be Kill-List #2; dashboard exists.
- **Ollama bootstrap via Popen+poll** — superseded by start_ollama.ps1 + VRAM arbiter (D7).
- **wttr.in weather** — superseded by Open-Meteo tool (K9 adopt).
- **The single-file god-module architecture** — `main.py`'s 658-line corpse is our own
  cautionary tale; this repo is what that path looks like at completion. Validation of the
  app/-package refactor, not a source of ideas.

## 6. Validation note

Independent builders (Leon, OVOS — report 07; ADA — report 12; now this repo) keep
converging on the same skeleton ULTRON already runs: local LLM + streamed TTS + always-on
voice + confirmation-gated system control + desktop automation. No pivot signal in this
repo; the only new *information* it carries is the HID-unlock trick (PJ-05) and small
desktop-env gaps (PJ-01/02/03).

## 7. Verification appendix (this stream's evidence)

- Clone: `git clone --depth 1 … /c/Users/ceoha/repos/personal-jarvis` — main tree = 2 files
  (`README.md` 1,940 B, `guide.txt` 352 B), both read in full.
- `git ls-remote origin` → HEAD `4ef8dc5`, tag `jarvis` `f664b6f`; `git log --oneline --all`
  → 28 commits; code lives only in history.
- Fullest tree recovery: `git ls-tree -r -l 2621801` → `assistant.py` 38,148 B /
  `assistant-pt2.py` 32,644 B; extracted via `git show` and read line-by-line (1,006 + 837
  lines). Earliest version (`git show 9e38622:assistant.py`, 408 lines) scanned with every
  `def`/feature line inspected (weather block read in full, L188–232).
- ULTRON gap checks (2026-09-14): tool names via `.tool(` decorator sites (23 grep-visible;
  registry count 41 per board/commit 1a624b2); `grep -rniE "brightness|whatsapp|shutdown|
  LockWorkStation|edge_tts" kernel/ app/` → no brightness/whatsapp/HITs, only
  `shutdown_ultron` + app/handlers `_handle_shutdown`; `kernel/computer/act.py` VERBS read
  (UIA-only, L40); `kernel/media/controller.py` read (media/volume, no brightness);
  `app/text_splitter.py` + `app/local_voice.py` stop/splitter seams read.
- Residual risk: report is docs-only (no code changed); PJ-01 wheel verification for
  py-3.14 deliberately deferred to its implementation stream.

## 8. Implementation addendum (2026-09-14, same-day pass)

User authorized a direct-main implementation pass ("implement everything you liked").
Landed (see PROGRESS.md → "PJ Adoption — research 13 adopt PJ-01..PJ-05" for the full
evidence table):

- **PJ-01** — `kernel/media/tools.py`: `brightness_get` (READ) + `brightness_set`
  (set/adjust, clamp, WRITE) over `screen-brightness-control` (lazy import, clean
  degradation); the Phase-P4 `_noop_handler` media registration replaced by the real
  `media_control` tool. py-3.14 wheel VERIFIED and installed (sbc 0.27.2 + WMI 1.5.1);
  REAL probe on the user machine: read 30% → set-to-30 → adjust+0, all ok via WMI.
- **PJ-02** — `kernel/computer/power.py` + `system_power` tool (WRITE): lock_workstation
  / shutdown / restart (5–600s abortable grace) / sign_out / abort (`shutdown /a`).
- **PJ-03** — `kernel/computer/raw_input.py` (the ONE SendInput seam) + `raw_click`/
  `raw_move`/`raw_scroll` verbs inside InputGateway (`kind="pixels"`, the lane P4-A's
  docstring anticipated): window-relative coords resolved to absolute against the
  admitted window's rect (outside → refuse), foreground raised + verified before every
  event; `raw_act` tool (EXECUTE, dry-run default) registered only behind
  `raw_input_enabled` (default OFF).
- **PJ-04** — `kernel/computer/whatsapp.py` + `whatsapp_send` tool (WRITE): pywinauto
  over the admitted WhatsApp window, foreground re-verified before EVERY keystroke
  burst — drift aborts before anything further is typed. Config `whatsapp_send_enabled`,
  default OFF (the report's "adopt-if-demanded" posture).
- **PJ-05** — `kernel/home/serial_bridge.py` (pyserial lazy, port opened ONCE — MCU
  reboots on DTR) + `hardware_cmd` tool (WRITE) behind `serial_bridge_enabled` +
  `serial_port` (default OFF). HID-unlock itself parks with the hardware stream; the
  command channel is ready.
- **PJ-06** — note-only (no edge-tts in ULTRON).

Registry 41 → **45 tools** (media_control, brightness_get, brightness_set, system_power
always on; raw_act / whatsapp_send / hardware_cmd config-gated). Suite **938 passed /
4 skipped** (from 868), ruff clean, mypy Success 105 files, killlist PASS. Requirements
pinned (win32): screen-brightness-control==0.27.2, WMI==1.5.1, pyserial==3.5.
