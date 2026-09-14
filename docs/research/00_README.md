# ULTRON Research Dossier — Index & Synthesis

*Written 2026-09-07 after the full research sweep (7 deep-dive docs + catalog).
Start here. Feature IDs (J-xx) refer to `01_jarvis_feature_catalog.md`; phases refer
to `../ROADMAP.md`. Sources in each doc verified Sep 2026.*

## The docs

| Doc | Topic | One-line verdict |
|---|---|---|
| [01_jarvis_feature_catalog.md](01_jarvis_feature_catalog.md) | Master feature list J-01…J-28 | The contract — every other doc maps to it |
| [02_voice_stack.md](02_voice_stack.md) | Wake word, STT, TTS, duplex, speaker ID | openWakeWord (free "hey jarvis" model) + Silero VAD + faster-whisper/RealtimeSTT local; Gemini Live stays flagship adapter; Kokoro+Chatterbox for the JARVIS voice; move wake in-process |
| [03_computer_use_vision.md](03_computer_use_vision.md) | Screen understanding, GUI control, cameras, sandbox | **UIA-first hybrid** (pywinauto/uiautomation), OmniParser v2 + Qwen3-VL 8B as pixel fallback, one vision stack, sandbox ladder (Job Objects → Windows Sandbox → e2b) |
| [04_memory_systems.md](04_memory_systems.md) | The moat: recall, consolidation, skills | Own a **SQLite backbone** (FTS5 + sqlite-vec + RRF + bge-reranker), mem0-style extraction/consolidation jobs, Generative-Agents scoring, procedural skills à la Voyager; 40-question recall eval gates Phase 3 |
| [05_agent_frameworks_mcp.md](05_agent_frameworks_mcp.md) | Agent loop, MCP, orchestration, evals | MCP won (≈9.6k servers): **FastMCP both directions**. Build the thin kernel loop, steal LangGraph/OpenAI-Agents patterns; SQLite durable queue (no Temporal/Redis on desktop); DeepEval+promptfoo in CI; browser-use for J-05 |
| [06_local_models.md](06_local_models.md) | Runtimes & model menu per hardware tier | Ollama as the only local server; Qwen3 family + gpt-oss-20b menu; tier table N/S/M/L probed by `ULTRON_SETUP.py`; structured outputs via Ollama JSON-schema |
| [07_open_source_jarvis_projects.md](07_open_source_jarvis_projects.md) | Who's who + HUD ideas | No pivot needed — Leon 2.0 & OVOS validate our direction; steal UX details (interrupt word, dictation) + HUD widget set for J-20 |
| [08_integration_automation.md](08_integration_automation.md) | Home, media, briefing, cameras, proactive | **HA via MCP-Assist** (~95% token cut) is the mansion moment; ytmusicapi → Spotify; Frigate+MQTT for cameras; ntfy/Telegram reach; briefing as a scheduled job |
| [09_k9_repo_analysis.md](09_k9_repo_analysis.md) | K9 (parmarth-kumar) repo deep-read | Adopt: Open-Meteo weather, search cascade + TTL cache, recency recall re-scoring, entity/pronoun layer, anti-echo guards, bus backpressure |
| [10_tts_research.md](10_tts_research.md) | TTS deep-read: piper1-gpl, rhasspy/piper, Kokoro (+space), Cartesia Sonic | ULTRON has NO local TTS — add `TtsEngine` seam + piper-tts 1.8.0 (py-3.14 verified) + kokoro-onnx ack voice; port kokoro.js TextSplitterStream for sentence-streamed speech; pronunciation lexicon; code-level adopt list A1–A8 |
| [11_stt_research.md](11_stt_research.md) | STT deep-read: RealtimeSTT, AssemblyAI SDK (streaming v3 + dictation), Deepgram SDK (listen v1/v2) | ULTRON has NO local STT and no STT seam — only Gemini Live `input_transcription`. Adopt: `SttEngine` seam + faster-whisper (py-3.14 verified) for the offline voice loop; port RealtimeSTT's zero-dep text stabilizer + numpy boundary detector; speculative finalize + pre-roll trim + VAD cascade patterns; adopt list S1–S10 (both cloud SDKs = protocol reference only, voice_agent/LLM-gateway anti-adopt) |
| [12_ada_research.md](12_ada_research.md) | ADA deep-read (nazirlouis ada · ada_local · ada_v2 — the YouTube JARVIS build) | Same product, 3 generations: Live tutorials → offline Ollama+FunctionGemma-270M router → flagship (Electron + Live native-audio + 4 agents). ULTRON's 4 real gaps, all adoptable as patterns not packages: Gemini-2.5-Computer-Use web agent over Playwright (D1), per-tool human-confirmation gate (D2), self-healing code-exec loop w/ stderr-feedback retries (D3), VAD-gated single-frame Live vision (D4); plus NON_BLOCKING voice tools (D5), reconnect-with-context (D6), Ollama VRAM arbitration (D7), system-snapshot tool (D8), briefing curation (D9), dashboard streaming UX (D10); adopt list D1–D15 (face-auth / 3D-printing / FunctionGemma-router parked — hardware + no-training directive) |
| [13_jarvis_assistant_research.md](13_jarvis_assistant_research.md) | PERSONAL-JARVIS-AI-ASSISTANT deep-read (surajmaru — ships NO code on main; 1,843 lines recovered from git history and read line-by-line) | Independent validation, not a source of ideas: hobbyist single-file Ollama+edge-tts+pyautogui assistant whose good patterns (sentence-streamed TTS, global stop semantics, confirmations) ULTRON already does better (text_splitter, ConsentGate, kernel/computer UIA). Small real gaps: brightness control (PJ-01), OS power verbs (PJ-02), raw desktop-input tier (PJ-03), WhatsApp-Desktop send (PJ-04, risky), ESP32 **USB-HID login-screen unlock** — the one thing software can't do (PJ-05, parked with hardware); edge-tts first-word-cutoff `\u200b` pad (PJ-06 note). Non-adopt: cloud STT, memory.json, keyword router (substring interception bugs documented as negative evidence) |

## Cross-doc decisions (the 2026 stack, in one place)

- **Voice:** openWakeWord → Silero VAD → faster-whisper (int8) → gateway (Gemini Live
  flagship / Ollama fallback) → Kokoro (acks) + Chatterbox (signature voice);
  SpeechBrain ECAPA for "who's speaking" → per-person memory. Local STT via the
  `SttEngine` seam (report 11 S1) gives the Ollama path ears — TTS fallback
  already exists (`kernel/voice/tts.py`, report 10 A1–A6).
- **Perception:** mss capture → UIA tree first → OmniParser/Qwen3-VL only when pixels
  are needed → consent + dry-run. Cameras: OpenCV/YOLO gate → VLM describe-on-event.
  Web-agent pixel tier (report 12 D1): Gemini 2.5 Computer Use over Playwright —
  same gateway provider, no second brain; complements UIA-first, doesn't replace it.
- **Kernel:** own thin loop + FastMCP client/server + SQLite durable queue + policy
  engine. No framework runtime, no second anything (kill-list).
- **Memory:** SQLite + FTS5 + sqlite-vec, typed stores, idle-time consolidation,
  recall eval in CI.
- **Local tier:** Ollama; model per hardware tier; embeddings always local.

## Prioritized build order (maps to ROADMAP phases)

**Phase 0 (now):** the three crash bugs, security triage, dead code, CI scaffold —
unchanged. Nothing in the research changes this.
**Phase 1 (kernel):** tool kernel + FastMCP wrapper (mechanical win) + gateway v0
(Gemini + Ollama adapters) + policy engine. Voice loop becomes a modality adapter.
**Phase 2 (orchestration):** SQLite durable queue, subagents, browser-use research
jobs, briefing-v1 (ICS + Open-Meteo + RSS + ntfy/Telegram).
**Phase 3 (memory):** the §04 architecture; migrate `long_term.json`; recall eval gate.
**Phase 4 (perception/autonomy):** UIA-first computer control w/ dry-run preview,
in-process wake + duplex polish (J-01), HA via MCP-Assist (J-14 — the demo moment),
Frigate/MQTT + proactive engine rebuild (J-17/J-19).
**Phase 5 (self-improvement):** DeepEval suites, procedural memory loop, quarterly
calibration vs OSWorld/LOCOMO/Terminal-Bench.

**The cheapest wow-moments, in order:** HA-via-MCP ("house settings, sir") → real
briefing → speaker-recognition greeting → camera captions on the HUD. All land inside
Phases 2–4 with zero new architecture.

## Watchlist (re-check quarterly)

- Leon 2.0 blog & OVOS releases (platform validation), Microsoft Agent Framework 1.x
  maturity, Qwen3/gpt-oss model refreshes, sqlite-vec/LanceDB releases,
  open-model grounding scores (ScreenSpot-Pro open ~47% vs cloud ~93% — when open
  closes the gap, local-only computer use becomes viable).
