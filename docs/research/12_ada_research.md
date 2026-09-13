# 12 — ADA Deep-Dive: nazirlouis/ada · ada_local · ada_v2

*Written 2026-09-14 after the ADA (Advanced Digital Assistant) research sweep
(the YouTube "JARVIS" build: https://www.youtube.com/watch?v=7ffF3fumhcQ).
Cloned to `C:/Users/ceoha/repos/ada-research/` — ada @ 7cf0c0f (2025-09-13),
ada_local @ f74ca2f (2026-02-01), ada_v2 @ d005af7 (2025-12-23). Every repo
read line-by-line on the load-bearing paths (~30k LOC surveyed: ada 4,034 /
ada_local 13,722 / ada_v2 12,532; per-file counts in §7). Sources verified
Sep 2026. Complements `03_computer_use_vision.md` (web agent tier),
`07_open_source_jarvis_projects.md` (same genre) and reports 10/11 (voice).*

## 0. Executive verdict

Same author, three generations of one product: **ada** = Gemini Live tutorials
→ a Live-only desktop assistant (PySide6, vision frames, ElevenLabs
stream-input TTS); **ada_local** = the fully-offline sibling (Ollama + a
fine-tuned 270M "FunctionGemma" intent router + Piper/RealtimeSTT + QFluent
GUI); **ada_v2** = the flagship (Electron+React over a FastAPI/Socket.IO
backend running **Gemini 2.5 native-audio Live** with function calling into
four real agents: a **Gemini-2.5-Computer-Use web agent**, a **self-healing
CAD code-exec agent** (build123d → STL → OrcaSlicer → Moonraker/OctoPrint),
Kasa smart home, and **MediaPipe face auth**).

**ULTRON is already ahead of ada_local** (our memory/routing/evals/security
beat it clearly) and **matches ada_v2's voice loop** (Live tier, barge-in,
transcription deltas, offline fallback — all landed in reports 10/11). The
gaps ada_v2 exposes in ULTRON are four, and all four are adoptable patterns
rather than packages:

1. **No computer-use web agent** — ada_v2's `web_agent.py` is a clean,
   working implementation of Gemini 2.5 Computer Use over Playwright
   (D1) — the exact "pixel fallback" tier report 03 named but never built.
2. **No human-in-the-loop tool gate** — per-tool `tool_permissions` +
   UI confirmation popups + deny-as-function-response (D2). Feeds the
   PolicyEngine directly.
3. **No self-healing code-execution tool** — generate → run → feed stderr
   back → retry ×3, with the script kept as the iteration artifact (D3).
4. **Live-tier polish we can port for free** — VAD-gated single-frame vision
   (D4), NON_BLOCKING tools with completion notifications (D5),
   reconnect-with-context-restore (D6), Ollama VRAM arbitration (D7),
   one-call `system_info` state snapshot (D8).

The FunctionGemma fine-tuned router (D15) is a *parked* alternative to our
A5 embedder routing policy — it's the "no-training" directive that keeps it
parked, same as MiniMind paths 2–3. Face auth (D13) and the 3D-printing
pipeline (D14) are hardware-parked. Adopt list D1–D15 in §5.

## 1. ada — the Live tutorial repo (4,034 LOC, 15 files)

`ada.py` (888L) is the whole product; `Tutorials/1–11` are its genesis
decomposed into steps (basic reply → system instructions → streaming → chat
→ RealtimeSTT → ElevenLabs → Live API → PySide6 GUI → google_search grounding
→ code_execution → function calling). The Tutorials README documents each;
only the composites matter:

- **Live + tools in one session** (`ada.py:642-657`): one `client.aio.live.connect`
  with `tools=[google_search, code_execution, function_declarations[…]]` —
  search grounding URLs (`grounding_metadata.grounding_chunks[].web.uri`),
  executed-code + output (`part.executable_code/.code_execution_result`), and
  custom function calls (`session.send_tool_response`) all arrive on the same
  receive loop. ULTRON's Live adapter already does this shape; nothing to port.
- **Live vision** (`stream_video_to_gui` / `send_frames_to_gemini`,
  `ada.py:753-803`): webcam via cv2 or **screen share via `PIL.ImageGrab`**,
  GUI gets every frame, Gemini gets 1 fps JPEG thumbnails (≤1024px) —
  superseded by ada_v2's smarter VAD-gated variant (D4).
- **ElevenLabs stream-input WS TTS** (`tts()`, `ada.py:875-908`): one
  websocket per utterance; text chunks are pushed as they arrive from Live and
  PCM chunks (`pcm_24000`) come back — speech starts before generation ends.
  Protocol reference only (ElevenLabs = cloud dependency; ULTRON's local TTS
  seam is the product decision, report 10).
- **"SYSTEM ACTIVITY" side panel** (`ada.py:1182-1230`): one GUI region that
  morphs between search sources / code-exec output / file listings per turn —
  a JARVIS-style tool-activity affordance worth stealing for the dashboard
  (D9-adjacent, see §4).
- **System-instruction hygiene** (`ada.py:646-657`): "Ignore both the webcam
  and screen content unless the user explicitly asks" — the anti-distraction
  rule we should mirror in ULTRON's Live tier whenever frames are attached.
- **3D avatar** (`AIAnimationWidget`, `ada.py:446-533`): rotating point-sphere
  drawn with plain QPainter + pulse-on-speak. Pure cosmetics; noted for the
  HUD (07's widget set), zero deps needed.

## 2. ada_local — the offline sibling (13,722 LOC, 77 files)

### 2.1 The FunctionGemma router (the repo's whole thesis)

`core/router.py` (421L) + `core/router_original.py` (351L, base-model
control) + `generate_training_data.py` (1,031L) + `train_function_gemma.py`
(273L) + HF-hosted weights (`nlouis/pocket-ai-router`, auto-snapshot-download
in `ensure_model_available`):

- Base model: **`google/functiongemma-270m-it`** — a 270M function-calling
  Gemma. Fine-tuned with TRL `SFTTrainer` + LoRA (r=16, α=32, dropout .05,
  all proj modules; 8 epochs, bs1×ga4, lr 2e-5, bf16, max_len 768 —
  "optimized for 8GB VRAM"), then adapter-merged and shipped as safetensors.
- **9 functions**: 6 actions (`control_light`, `set_timer`, `set_alarm`,
  `create_calendar_event`, `add_task`, `web_search`), 1 context
  (`get_system_info`), 2 passthrough (`thinking` / `nonthinking`). The
  passthrough pair is the clever bit: the router also decides *whether the
  big LLM should think*, toggling Ollama's `think: true/false`.
- Route call = chat-template with tools → greedy decode, `max_new_tokens=100`
  → parse Gemma's native `call:name{key:<escape>val<escape>}` format via
  regex, with **argument-fallback** (unparseable args → stuff the raw user
  prompt into the function's main arg) and whole-route fallback to
  `nonthinking` (~50 ms on GPU, ~200 ms CPU).
- Training data = ~50 handwritten examples per function (450–500 rows,
  OpenAI messages+tools schema), no augmentation pipeline to speak of.

**ULTRON mapping:** our A5 routing policy (embedder seam, 19/19 eval) already
covers intent routing *without a trained model*. FunctionGemma is the stronger
long-term design (a real trained head beats cosine similarity on ambiguous
utterances), but it's exactly MiniMind-path-2 territory → **parked under the
no-training directive** (D15). Two portable *ideas* regardless: (a) the
**argument-fallback** pattern for robust tool-arg repair, (b) routing as a
`thinking`/`nonthinking` bit for the local tier.

### 2.2 Model lifecycle under VRAM pressure

`core/model_manager.py` (86L) + `core/model_persistence.py` (173L) +
`preload_models()` in `core/llm.py`:

- `unload` = Ollama `/generate` with `keep_alive: 0`; list loaded via `/ps`;
  `ensure_exclusive_qwen(target)` unloads every other loaded model matching a
  family prefix before a big model loads (`browser_agent.py:37-39` unloads
  the chat model *by name* before the VLM loads).
- `QwenModelManager`: lazy `ensure_loaded()` (warm ping `"hi"` with
  `num_predict:1`, `keep_alive:"5m"`), `mark_used()` timestamp refresh, a
  10 s monitor thread that unloads after `QWEN_TIMEOUT_SECONDS` idle (300 s),
  all under a lock. `preload_models()` warms router+responder+TTS in parallel
  threads at boot.
- ULTRON runs Ollama + faster-whisper + (opt-in) bge-m3 embeddings on one
  GPU — this exact arbitration pattern is D7, a small `app/local_models.py`.

### 2.3 Voice pipeline (`voice_assistant.py` 365L, `stt.py` 171L, `tts.py` 324L)

Wake word "jarvis" via RealtimeSTT's `wakeword_backend="pvporcupine"` →
`recorder.text()` blocking loop → strip wake word → **router → executor →
Qwen-with-context → Piper**. Everything here ULTRON already owns better
(EchoGate→VAD→preroll→faster-whisper→AgentLoop→TTS, e34d841) except three
portable details:

- `SentenceBuffer` (`tts.py:568-598`): regex `([.!?])\s+|([.!?])$` splitter
  feeding a per-sentence speech queue — the same contract as our ported
  splitter (report 10 A5); no action.
- **Function-result narration**: after an action executes, the raw result is
  wrapped as context (`"Function control_light executed. Success: True.
  Result: …\n\nUser asked: …\n\nRespond naturally and concisely."`) so the
  LLM *speaks* the outcome instead of the executor's canned string
  (`voice_assistant.py:193-243`). Nice, cheap; our AgentLoop already renders
  tool results, but the *structured `{success, message, data}` contract*
  (§2.4) is what makes this clean.
- Piper via pre-built Windows exe + `--output-raw` + sounddevice, with
  kill-process barge-in — anti-adopt for us (piper-tts 1.8.0 in-process is
  cleaner and already merged).

### 2.4 Function executor + the `get_system_info` snapshot

`core/function_executor.py` (581L): every routed function returns
**`{success: bool, message: str (human/spoken), data: any (raw)}`** — one
contract across GUI toast, voice narration, and history. Inside:

- `ActiveTimer` dataclass (remaining/format helpers) + regex duration parser
  ("1 hour 30 minutes" → s, bare number → minutes) + time normalizer
  ("7am"→"07:00", am/pm edge cases) + date parser (today/tomorrow/weekday
  names with "next" rollover). ULTRON's orchestrator timers are the durable
  version; the *parsers* are handy reference for utterance→args repair.
- Kasa fuzzy device matching (substring both ways) + **rediscovery retry**
  when cache misses; toggle reads live state before flipping
  (`_control_light`, `function_executor.py:166-260`).
- **`_get_system_info()`** (`function_executor.py:564-640`): one call that
  aggregates time + timers + alarms + today's calendar + pending tasks +
  device states + weather + top news into a single snapshot dict the LLM
  reasons over ("what's on my schedule?", "are any lights on?"). This is
  **D8** — a cheap, high-demo-value tool for our registry (31 tools today)
  composing stores we already have.

### 2.5 Browser agent — the DIY Qwen-VL loop (superseded)

`core/agent/browser_agent.py` (205L) + `browser_controller.py` (190L) +
`vlm_client.py` (220L): Playwright Chromium → JPEG q70 screenshot → Ollama
`qwen3-vl:4b` with `think:true` → parse `<tool_call>` JSON → 1000×1000
coordinate space scaled to viewport → act → "Action executed. Here is the new
screen." → loop. Notables: auto-reprompt when the model reasons without
emitting a tool call; a string-aware brace-depth JSON extractor
(`vlm_client.py:550-585`) tolerant of smart quotes and tool-calls buried in
thinking; headless + stealth UA. **ada_v2 replaces this entire approach with
Gemini 2.5 Computer Use (D1) — read §3.3; this one is the anti-pattern
comparison** (small local VLM + hand-rolled prompt = fragile).

### 2.6 The rest of core (news, weather, tasks, calendar, settings, history)

- `news.py` (167L): **AI-curated briefing** — DuckDuckGo news ×3 categories →
  minified `{id,title,source,category}` list → LLM "expert News Editor"
  selects 6, rewrites titles <10 words, assigns categories → **merge back**
  onto the original objects (URLs/images preserved) → 15 min TTL cache,
  raw fallback if AI fails. This curation loop is D9's payload.
- `weather.py` (160L): Open-Meteo current + hourly + computed high/low + WMO
  code→text map. ULTRON's K9-adoption weather is a superset — anti-adopt.
- `tasks.py` (139L) / `calendar_manager.py` (95L) / `history.py` (167L):
  SQLite stores (tasks+alarms, events, sessions+messages with pin/rename).
  ULTRON's memory engine is the superior backbone; the **pinned-session
  sidebar** is a UX nicety for our dashboard.
- `settings_store.py` (142L): dot-path settings (`get("models.chat")`) +
  deep-merge defaults + change signal. Fine pattern; our config layer covers it.
- `speed_test.py` (291L): mini model benchmark harness — 20 ground-truth
  QA pairs with accepted-answer sets, per-model latency + accuracy +
  RAM/VRAM via psutil/pynvml/Ollama `/ps`, `session.trust_env=False` proxy
  bypass. A thought-provoking micro-pattern next to our live-benchmark.

### 2.7 GUI (PySide6 + QFluentWidgets, ~5.5k LOC)

`app.py` (356L) FluentWindow with **lazy tabs** (`LazyTab` factory defers
construction until first navigation), system monitor embedded in the title
bar (CPU/RAM/GPU/VRAM + loaded Ollama models, `system_monitor.py`), voice
indicator overlay. `handlers.py` (618L) ChatWorker-on-QThread with signals +
**100 ms buffered UI flush** (`ui_throttle_timer` → `_flush_ui_buffers`) instead
of per-token repaints — the classic streaming-UI throttle, directly portable
to any rich-text dashboard feed. Components: Gemini-style collapsible
**ThinkingExpander** (spinner + expander), **SearchIndicator**, **toast
notifications on tool results**, message bubbles with markdown/pygments,
planner (timer/alarm/schedule cards), dashboard with greeting header +
time/weather bubbles + clickable stat cards + "Home Scenes" macros
(Focus=all-off, Relax=dim-40%) + "System Intelligence" feed + "Upcoming
Priority" next-event gradient card. Tests are mock-heavy but real
(`test_fuzzy_light.py` exercises the executor with a mock Kasa manager).

**Dashboard takeaways (D10/D9-adjacent):** lazy-tab loading, throttled
streaming flush, tool-result toasts, thinking/search indicators, scene
macros, and the *next-upcoming-event* card are all cheap wins for ULTRON's
web dashboard; the Qt code itself is not portable (we're web-based).

## 3. ada_v2 — the flagship (12,532 LOC, 64 files)

Electron 28 + React 18 + three.js frontend ⇄ Socket.IO ⇄ FastAPI backend;
one `AudioLoop` (Gemini Live native-audio) owns the session and dispatches
tools to four agents. `.env` GEMINI key; `settings.json` holds **tool
permissions, printers, kasa devices**.

### 3.1 `backend/ada.py` (1,300L) — Live session with production teeth

- **NON_BLOCKING tools** (`ada.py:14-52, 181`): `generate_cad`,
  `run_web_agent`, `iterate_cad` are declared with `"behavior":
  "NON_BLOCKING"` — the Live API doesn't stall the voice turn waiting for
  them; dispatch is `asyncio.create_task`, and completion is injected back as
  a plain text turn: `session.send("System Notification: CAD generation is
  complete! …", end_of_turn=True)` so the model *announces* it. Failure path
  symmetrical ("CAD generation failed"). **D5 — the right pattern for any
  long tool on a voice tier** (pairs with our orchestrator jobs).
- **Per-tool confirmation gate** (`ada.py:716-780`): `self.permissions`
  (from settings, default-allow) checked per call; if required, a uuid-keyed
  `asyncio.Future` lands in `_pending_confirmations`, the UI gets
  `tool_confirmation_request {id, tool, args}`; deny produces a normal
  function response `"User denied the request to use this tool."` so the
  model gracefully narrates refusal. **D2 — exactly the human-in-the-loop
  seam our PolicyEngine needs, proven end-to-end on a voice tier.**
- **VAD-gated vision (D4)** (`listen_audio`, `ada.py:350-469`): mic chunks
  double as a crude VAD (inline RMS via struct.unpack — audioop is
  py3.13-removed, they reimplemented it). Continuous frames are NOT sent;
  the *latest* captured frame is held (`send_frame` from the frontend) and
  exactly **one frame is pushed into the out-queue at speech onset**
  (RMS>800), reset after 0.5 s silence. Bandwidth/quota-cheap "see what I
  see" for a Live tier.
- **Transcription deltas + barge-in** (`receive_audio`, `ada.py:640-714`):
  `input_audio_transcription` + `output_audio_transcription` enabled;
  cumulative→delta diffing (`transcript.startswith(last)`); **user-speech
  delta → `clear_audio_queue()`** (kills playback mid-utterance) — the same
  barge-in semantics we built; their delta-diff guard is a nice addition
  against duplicate events.
- **Reconnect with context restore** (`run`, `ada.py:1180-1275`): outer
  while + TaskGroup; on session death → exponential backoff (1s→10s cap) →
  on reconnect, pull last 10 chat-log entries and inject "Connection was
  lost… here is the recent chat history… acknowledge the reconnection". **D6.**
- Project memory injection on `switch_project`: `get_project_context()`
  (file walk + text-file contents ≤10 KB) sent as a silent System
  Notification turn. Chat buffer flushes to per-project
  `chat_history.jsonl` on speaker change (`flush_chat`).

### 3.2 `backend/server.py` (989L) — the event bridge

Socket.IO ↔ AudioLoop wiring: every callback becomes an emit
(`audio_data`, `cad_data`, `browser_frame`, `transcription`,
`tool_confirmation_request`, `cad_status`, `cad_thought`, `project_update`,
`device_update`, `print_status_update`); client events cover session
(start/stop/pause/resume, `user_input`, `video_frame`), agents
(`generate_cad`, `iterate_cad`, `prompt_web_agent`, kasa discover/control,
printer discover/add/print/profiles), memory (`save_memory` → transcript
dump to `long_term_memory/*.txt`; `upload_memory` → file contents injected
into the Live session as context), settings/permissions sync. **Face auth
gates `start_audio`**: if `face_auth_enabled` and not authenticated →
`error 'Authentication Required'`. A 2 s printer status monitor loop pushes
`print_status_update` continuously. Windows note at top:
`WindowsProactorEventLoopPolicy` set before imports (asyncio subprocess on
win32) — same class of fix we document for py-3.14.

### 3.3 `backend/web_agent.py` (318L) — Gemini 2.5 Computer Use, done right

The official computer-use protocol over Playwright, headless:

- Model `gemini-2.5-computer-use-preview-10-2025`, `tools=[Tool(computer_use=
  ComputerUse(environment=ENVIRONMENT_BROWSER))]`,
  `thinking_config(include_thoughts=True)`; viewport 1440×900; 1000×1000
  normalized coords denormalized on execution.
- Loop: prompt + initial **PNG** screenshot → model → split parts
  (thought / text / function_call) → execute actions (`navigate`,
  `click_at`, `type_text_at` with clear-before + press-enter, `hover_at`,
  `drag_and_drop`, `key_combination`, `scroll_document/scroll_at`,
  `go_back/forward`, `search`, `wait_5_seconds`) → 1 s settle → new PNG →
  respond with `FunctionResponse(id=call_id, response={url, ...}, parts=
  [FunctionResponsePart(inline_data=FunctionResponseBlob(png))])` — **the
  screenshot travels *inside* the function response**, which is the CU
  contract. `MAX_TURNS = 20` hard cap; `safety_decision`
  `require_confirmation` args are acknowledged and surfaced; thoughts +
  screenshots stream to the UI per turn; final text = the agent's summary.
- **D1.** Our J-05 decision (report 05: browser-use package, P2-D delivered
  `web_read`+`research_report_plan` instead; Playwright reserved as P4-A
  substrate) gets its missing tier here: a *cloud-vision* web agent. It's
  gateway-honest (all LLM calls through Gemini = the gateway provider),
  stateless between turns except page state, and complements UIA-first
  control (03) rather than competing with it.

### 3.4 `backend/cad_agent.py` (444L) — the self-healing code-exec loop (D3)

"Text → parametric CAD" as a **code-as-artifact** loop:

1. System instruction encodes a mini-DSL contract for `build123d`
   (assign `result_part`, export `output.stl`, lowercase builder ops,
   conservative fillet radii, centered mm dimensions) + one exemplar script.
2. `gemini-3-pro-preview` streams code **with visible thinking**
   (`include_thoughts=True` → `on_thought` UI stream); code extracted from
   ```python fences with a heuristic fallback.
3. Script written to `current_design.py` (persisted!), output path injected
   by string-replace (Windows backslash escaping), run via
   `[sys.executable, script]` in `asyncio.to_thread`.
4. On crash: last stderr lines → status emit `{status:"retrying", attempt,
   max_attempts, error}` → **prompt = failure stderr + "fix and return the
   full corrected script"** → retry (max 3). Success → STL b64'd to the
   three.js viewer + artifact archived into the project
   (`save_cad_artifact` = timestamped prompt-slug filename).
5. `iterate_prototype` reads `current_design.py`, **sanitizes prior absolute
   output paths back to 'output.stl'** (prevents path-escape drift across
   iterations), rewrites with the change request — the script *is* the
   design's source of truth; every iteration is a fresh full-file rewrite.

That's a complete micro-architecture for any ULTRON "write & run code"
tool: contract-rich system prompt → run → stderr-feedback retry cap →
artifact + provenance. Directly informs our code-exec story (J-06-adjacent).

### 3.5 `backend/printer_agent.py` (1,033L) — hardware-parked, architecture-reusable (D14)

mDNS discovery (`_octoprint/_moonraker/_klipper/_http._tcp` via zeroconf)
→ **type probing** of unknown hosts (GET `/printer/info` = Moonraker;
`/api/version` 200/401/403 = OctoPrint) → camera-url probing
(mjpg-streamer paths) → manual-add fallback. Slicing via **OrcaSlicer CLI**
(`--slice 0 --outputdir … --load-settings machine;process --load-filaments`)
with a **score-based profile matcher** (vendor map + term scoring +
word-boundary penalty so "K1" doesn't match "K1C" + generic-PLA preference);
`plate_*.gcode` rename handling; PrusaSlicer CLI fallback. Upload+start:
OctoPrint `/api/files/local` (+`print:true`), Moonraker
`/server/files/upload` + `/printer/print/start` with an OctoPrint-compat
fallback (Creality K1 needs it). Status: job+temps normalized into a
`PrintStatus` dataclass; error-tracker to stop log spam. Everything I'd
keep if ULTRON ever gets a printer; parked until hardware exists.

### 3.6 Face auth + gestures (hardware-parked, D13)

`authenticator.py` (221L): MediaPipe **Face Landmarker** (478×(x,y,z)
landmarks, float16 .task auto-download) → flatten → **cosine similarity**
against a single `reference.jpg` (threshold 0.15) → `authenticated`.
Every-other-frame processing, camera fallback index 0→1, frames b64'd to the
lock-screen UI. Honest assessment: raw-landmark cosine is a *demo-grade*
biometric (pose/lighting-sensitive, spoofable by photo) — ULTRON's existing
consent/speaker-ID gating is the more defensible seam; revisit only with a
real embedder (e.g. a face-recognition ONNX) behind our biometric-gate
pattern. Gesture control (pinch=click, fist=drag-window, palm=release) lives
in the React frontend via MediaPipe tasks-vision + an in-browser hand
landmarker (`App.jsx`, `hand_gesture_test.py` prototype) — spectacular demo,
zero ULTRON value until HUD maturity.

### 3.7 Frontend (React/Electron, ~2.5k LOC)

`App.jsx` (1,693L): lock screen (face auth) → main shell; mic/speaker/webcam
device pickers persisted to localStorage; audio visualizers for mic + AI
playback (canvas bars fed by `audio_data` emissions); **modular floating
windows** with z-order + gesture drag; ConfirmationPopup binds the
`tool_confirmation_request` flow; CadWindow = three.js `STLLoader` + wireframe
overlay + retry-status banner + streaming CAD thoughts; PrinterWindow =
live progress/temps; BrowserWindow = screenshot + action log; MemoryPrompt =
save/load transcript. `electron/main.js` (190L) is standard shell + IPC
window controls. Same dashboard takeaways as §2.7; nothing to port code-wise.

### 3.8 Tests + packaging hygiene

`tests/` (pytest): tool-schema shape tests, `test_authenticator`
(landmark-compare math), `test_kasa_agent`/`test_printer_agent` (resolve +
upload with mocked aiohttp), `test_web_agent` (action executor +
denormalization), `test_ada_tools`, `test_runner` — mock-first, no API
calls. Hygiene lapses worth not copying: committed `settings.json` with
real device IPs, `.DS_Store`/log/trace junk in both repos, duplicate
`router_original.py`, committed SQLite DBs in ada_local.

## 4. Cross-repo synthesis — what actually transfers

| ULTRON gap | ADA proof | Adopt |
|---|---|---|
| No computer-use web agent (J-05 pixel tier) | ada_v2 `web_agent.py`: Gemini-2.5-CU + Playwright end-to-end | **D1** |
| No human-in-the-loop tool gate | per-tool permissions + Future + UI popup + deny-response | **D2** |
| No code-exec tool with repair loop | CadAgent: contract prompt → run → stderr retry ×3 → artifact | **D3** |
| Live tier sees nothing (no frames) | VAD-gated single-frame vision (RMS onset + latest-frame hold) | **D4** |
| Long tools block the voice turn | NON_BLOCKING behavior + "System Notification" completion turns | **D5** |
| Live drop = cold restart | backoff reconnect + history re-injection + acknowledge | **D6** |
| Ollama/STT/embedder VRAM contention | exclusive-unload + idle-timeout + warm-ping preload | **D7** |
| "What's up?" needs N tool calls | one `system_info` snapshot tool composing stores | **D8** |
| Briefing = fetch only (no curation) | DDG news → LLM editor → merge-back → TTL cache | **D9** |
| Dashboard streaming UX | 100 ms flush throttle, thinking/search indicators, tool toasts, next-event card, scene macros, title-bar monitor | **D10** |
| Session context = global soup | ProjectManager: per-project folders + artifacts + jsonl log + context injection | **D11** |
| Memory export | transcript → `long_term_memory/*.txt` → reinject on demand | **D12** |
| Biometric gating demo | MediaPipe face-landmark cosine unlock (weak biometric) | **D13 park** |
| Maker tier absent | mDNS+probe+slicer+Moonraker/OctoPrint pipeline | **D14 park** |
| Router = embedder policy only | FunctionGemma-270M trained intent head + thinking/nonthinking bit + arg-fallback | **D15 park (no-training)** |

**Where ULTRON is already ahead** (no action): memory engine + recall evals
(ada has none), routing policy with eval evidence, security posture (binds,
consent seam, killlist CI), test discipline (807 tests vs their ~10 mock
files), offline voice loop (ada_local's is cruder), orchestrator/durable jobs
(ada has none), multi-user. ADA's genuine edge is *product surface*: they
shipped the four agent tiers (web, CAD, home, print) on one voice session —
that breadth, plus the confirmation UX, is the lesson.

## 5. Adopt list D1–D15 (sequenced, honest about deps)

- **D1 — Computer-use web agent (HIGH, Phase-4-A input).** New tool
  `run_web_agent(prompt)` in the live registry behind the existing
  web-research consent gate; engine = Gemini CU protocol exactly as
  ada_v2 implements it (PNG-in-FunctionResponse, MAX_TURNS cap, thoughts to
  the bus). Playwright is already the pinned P4-A substrate; gateway stays
  the only model-string owner. Prior art to reuse from ada_v2: action
  executor + coordinate denormalization + settle-wait. Dep: gateway Gemini
  adapter (exists); open question for the board row: cost guard (turn cap +
  `/cost` integration via CostTracker).
- **D2 — Tool confirmation gate (HIGH, feeds PolicyEngine).** Port the
  semantics, not the Qt: policy returns `require_confirmation` → bus event
  `tool.confirmation_request {id, tool, args}` → dashboard popup → resolve →
  deny renders as the tool's result ("User denied…") so the brain narrates
  refusal. Map: write/exec/print-class tools default-confirm in settings;
  read-class default-allow. This is the J-catalog "confirm destructive
  actions" row made concrete.
- **D3 — Self-healing code-exec tool (HIGH).** `run_python` tool with
  ada_v2's loop shape: persistent script per workspace, stderr-feedback
  retry (cap 3, configurable), artifact + provenance saved, thoughts/status
  to the bus. Sandbox per report 03's ladder before it ever runs untrusted.
- **D4 — VAD-gated vision frames (MEDIUM, Live tier).** When frames are
  enabled: hold latest screen/webcam frame, push exactly one on speech
  onset (our EchoGate/VAD already computes the onset signal), none
  otherwise; system instruction gets ada's "ignore video unless asked"
  line. Zero new deps (mss already pinned in 03).
- **D5 — NON_BLOCKING tool dispatch on the voice tier (MEDIUM).** Declare
  long tools non-blocking; completion/failure re-enters as a system
  notification turn (pairs with orchestrator jobs for durability).
- **D6 — Live reconnect-with-context (MEDIUM).** Backoff + last-N-turns
  re-injection + forced acknowledgment turn in the Live adapter.
- **D7 — Ollama VRAM arbitration (MEDIUM).** `app/local_models.py`:
  exclusive-unload before big loads, idle-timeout unload (keep_alive=0),
  boot warm-ping, `/ps` status for the dashboard monitor. Small, testable.
- **D8 — `system_status` snapshot tool (MEDIUM, cheap wow).** One tool
  composing time/timers/jobs/tasks/devices/weather into one dict for the
  brain. Registry +N=1.
- **D9 — Briefing curation loop (MEDIUM, Phase-2 briefing row).** Add the
  select+rewrite+merge-back LLM editor step + raw fallback to briefing-v1;
  dashboard "Intel" widget.
- **D10 — Dashboard streaming/UX patterns (LOW, UI stream).** 100 ms flush
  throttle, thinking/search indicators, tool-result toasts, next-event
  card, scene macros, title-bar system monitor. Pick per widget; no Qt.
- **D11 — Project workspace context (LOW).** Per-project artifact folders +
  chat jsonl + `get_project_context` injection; maps onto our session/user
  layer; decide after multi-user settles.
- **D12 — Transcript memory export (LOW).** Save/load conversation files;
  trivial atop the memory engine; do with the dashboard export button.
- **D13 — Face auth (PARKED — hardware + weak biometric).** Revisit only
  with a real face embedder behind the biometric-gate pattern; the
  landmark-cosine trick is demo-grade.
- **D14 — 3D printing pipeline (PARKED — hardware).** Keep report §3.5 as
  the build doc (mDNS+probe+slicer CLI+upload); revisit when a printer
  exists on the network.
- **D15 — FunctionGemma-style trained router (PARKED — no-training
  directive, like MiniMind paths 2–3).** Keep as the named alternative when
  the directive lifts: 270M function-calling head, passthrough
  thinking/nonthinking bit, arg-fallback repair. Until then A5's embedder
  policy stands.

**Recommended order:** D2 → D8 → D3 → D1 → D4/D5/D6 (one voice-tier pass) →
D7 → D9/D10 → D11/D12. D2 and D8 are sub-day rows; D1/D3 are the two real
builds.

## 6. Anti-adopt (with reasons)

- **ElevenLabs / cloud TTS+STT** — contradicts the local-first decision
  (reports 02/10/11); our seams already cover it.
- **PySide6/QFluentWidgets monolith GUI** — ULTRON is web-dashboard; also
  the 800-line handler files are the god-module smell we just killed.
- **ada_local's DIY Qwen-VL browser loop** — superseded by D1's CU
  protocol; hand-rolled coordinate prompting is the fragile path.
- **Whole-function-call dispatch in the Live receive loop** (ada_v2's
  300-line if/elif) — our registry+policy engine is the right seam; don't
  copy the dispatch shape.
- **Conda/PyAudio/RealtimeSTT package drag** — py-3.14 system runtime is
  settled; voice capture already owned.
- **Committed DBs/settings with real IPs, log/trace junk, duplicate
  router files** — hygiene anti-patterns; our killlist CI exists for this.
- **Face-data-in-repo (`reference.jpg`)** — biometric template in git is a
  security anti-pattern; any future D13 work stores references outside the
  repo with consent logging.

## 7. Evidence

- Clones: `C:/Users/ceoha/repos/ada-research/{ada,ada_local,ada_v2}` —
  SHAs ada `7cf0c0f` (2025-09-13), ada_local `f74ca2f` (2026-02-01),
  ada_v2 `d005af7` (2025-12-23); single-commit shallow clones (upstream
  history is effectively one snapshot per repo).
- Read line-by-line (load-bearing paths): ada `ada.py` 888/888 +
  Tutorials/README 221 + repo README 161; ada_local README 444, config 142,
  main 33, router 421, router_original 351, model_manager 86,
  model_persistence 173, function_executor 581, llm 147, voice_assistant 365,
  stt 171, tts 324, history 167, news 167, weather 160, tasks 139,
  calendar_manager 95, settings_store 142, kasa_control 131, agent/* 615,
  generate_training_data 1,031, train_function_gemma 273, app 356,
  handlers 618, dashboard 749, briefing 145, browser-tab 177, speed_test 291,
  demo head + system_monitor/thinking_expander/voice_indicator/
  search_indicator heads, tests (3 files), requirements 66; ada_v2 README 437,
  tools 75, project_manager 166, capture_face 47, kasa_agent 221,
  authenticator 221, ada 1,300, server 989 (structure grep + key sections),
  web_agent 318, cad_agent 444, printer_agent 1,033, tests/conftest +
  test_ada_tools heads, package.json, electron/main.js (structure grep),
  App.jsx (structure grep, 1,693L), Visualizer/CadWindow/gesture heads.
  Skimmed-only: remaining Qt widgets (toast/timer/alarm/schedule/
  news_card/message_bubble/toggle_switch/styles/chat/settings/planner/
  home_automation tabs — pure layout), demo.py body (redundant with
  voice_assistant), committed binaries/logs (chat_history.db, trace.txt,
  import_error.log, .DS_Store — junk, not code).
- Totals: ~30,288 LOC surveyed across 156 files; no code changed in ULTRON —
  this stream ships docs only (report + index + board).
