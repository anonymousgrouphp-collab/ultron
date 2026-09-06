# JARVIS Feature Catalog — Cinematic → Buildable

*Written 2026-09-07. The master feature list for "simulate Iron Man's JARVIS". Every
feature has an ID (J-xx) that phases in `docs/ROADMAP.md` and the other research docs
in this folder reference. Status legend: ✅ exists today · 🔶 partial/broken today ·
⬜ not started.*

**Current ULTRON baseline (what we already have):** Gemini Live voice loop with
barge-in interrupt, wake-word launcher, 19 hardcoded action tools, JSON memory,
system monitor, FastAPI dashboard + phone relay, one-shot dev_agent, OS-scheduler
reminders, startup greeting.

---

## Tier 1 — The Core JARVIS Experience (voice + intelligence)

| ID | Feature (in the films) | Buildable equivalent | Status | Approach / Research |
|----|------------------------|----------------------|--------|---------------------|
| J-01 | Always listening, hears from across the room, answers mid-sentence | Local wake word ("Hey JARVIS") + VAD + full-duplex turn-taking; in-session wake (not process-launch like today) | 🔶 | Wake: openWakeWord/Porcupine. Duplex: realtime speech APIs. → `02_voice_stack.md` |
| J-02 | Recognizes *who* is speaking, addresses them by name | Speaker identification/diarization → per-user memory & persona | ⬜ | pyannote / speechbrain embeddings. → `02_voice_stack.md` |
| J-03 | Wit, sarcasm, personality ("Sometimes you have to run before you can walk") | Persistent persona system: style directives + personality state, not one hardcoded prompt | 🔶 | Persona module in kernel; personality evals. → `01` (this doc §Persona) |
| J-04 | Total recall of everything ever discussed ("You mentioned in March…") | Memory engine: episodic + semantic + vector retrieval across sessions | ⬜ (2,200-char JSON today) | Roadmap Phase 3. → `04_memory_systems.md` |
| J-05 | Instant research & synthesis of anything | Agentic web research: search → read → cite → summarize → save to memory/files | 🔶 (web_search is shallow) | Roadmap Phase 2 (research subagent). → `05_agent_frameworks_mcp.md` |
| J-06 | "What am I looking at, sir?" — reads any screen/document | Screen understanding: screenshot → OmniParser/VLM → grounded GUI actions | 🔶 (vision path broken) | Fix in Phase 0, rebuild Phase 4. → `03_computer_use_vision.md` |
| J-07 | Operates the whole computer on command | Real GUI agent: plan → click/type → verify → recover, with consent & dry-run | 🔶 (fragile scripts today) | Roadmap Phase 4. → `03_computer_use_vision.md` |
| J-08 | Morning briefing: news, schedule, overnight events | True briefing pipeline: calendar + email + weather + news + system events + memory highlights | 🔶 (hardcoded greeting today) | Briefing pipeline in kernel (Phase 2). → `08_integration_automation.md` |
| J-09 | Monitors house systems, announces faults before asked | Real diagnostics: health monitoring, anomaly alerts, self-healing (disk, updates, background bloat) | 🔶 (monitor crashes on its emergency path) | Fix Phase 0; rebuild as event-driven kernel service (Phase 4) |
| J-10 | Reminders, appointments, "tell me when…" | Unified scheduler: in-kernel cron + OS calendar integration (replaces schtasks hack) | 🔶 | Roadmap Phase 1 (kernel scheduler) |
| J-11 | Codes for Tony: builds, runs, fixes | Coding subagent: plan → write → execute sandboxed → test → fix loop | 🔶 (dev_agent is one-shot) | Roadmap Phase 2. → `05_agent_frameworks_mcp.md` |
| J-12 | Understands context across days; never repeats itself | Conversation continuity: working memory + session summaries injected at recall time | ⬜ | Phase 3 memory. → `04_memory_systems.md` |
| J-13 | Speaks any language, switches mid-sentence | Multilingual ASR/TTS + language detection per utterance | 🔶 (prompt-fragile) | Whisper-class STT + XTTS/Kokoro. → `02_voice_stack.md` |

## Tier 2 — The Stark Mansion (house, devices, presence)

| ID | Feature | Buildable equivalent | Status | Approach |
|----|---------|----------------------|--------|----------|
| J-14 | Controls lights, climate, music, doors ("House settings") | **Home Assistant integration via MCP** — the single biggest "mansion" multiplier | ⬜ | HA + MCP server. → `08_integration_automation.md` |
| J-15 | Music with taste ("Play something by the Bee Gees") | Media control: Spotify/YouTube Music/local library; mood-aware selection | ⬜ | Media MCP tools. → `08_integration_automation.md` |
| J-16 | Sees everything in the house (cameras) | RTSP/webcam feeds → VLM scene description, person/package detection, event clips | ⬜ | Local VLM (Qwen-VL class). → `03_computer_use_vision.md`, `06_local_models.md` |
| J-17 | Security mode / "battle mode": threat awareness | Network monitoring, motion alerts, face recognition on door cam, incident log | ⬜ | Frigate/NVIDIA-style detection + kernel events. → `08_integration_automation.md` |
| J-18 | Follows you: phone, suit, car, workshop | Multi-endpoint: phone relay (✅ exists), watch/tv clients, roaming sessions | 🔶 | Dashboard v2 (Phase 6) |
| J-19 | "I took the liberty, sir." — acts without being asked | Event-driven proactive engine: time/calendar/system/memory triggers, with consent policy | 🔶 (silence timer mislabeled) | Rebuild honestly, Phase 4 |
| J-20 | Holographic HUD everywhere | Voice-reactive HUD dashboard: 3D-ish core visual, telemetry graphs, task monitor, camera wall | 🔶 (basic dashboard) | Web HUD (Three.js/WebGL). → `07_open_source_jarvis_projects.md` (HUD repos) |

## Tier 3 — The Legion (autonomy & self-improvement)

| ID | Feature | Buildable equivalent | Status | Approach |
|----|---------|----------------------|--------|----------|
| J-21 | Iron Legion: many units working simultaneously | Subagent fleet: parallel background agents with scoped tools, fleet dashboard | ⬜ | Orchestrator, Phase 2. → `05_agent_frameworks_mcp.md` |
| J-22 | "House Party Protocol" — deploy the whole fleet | One command fans out N agents (research, files, monitoring), aggregates results | ⬜ | Same orchestrator; queue + aggregation |
| J-23 | Runs simulations & experiments ("synthesize the new element") | Sandboxed code/data workspace: run, measure, iterate, report | ⬜ | Sandbox + coding subagent (Phase 2/4). → `03`, `06` |
| J-24 | Self-diagnostics; JARVIS patches itself | Eval harness + procedural memory: failed tasks become skills; regression-gated releases | ⬜ | Roadmap Phase 5 |
| J-25 | Learns your patterns and preferences over time | Behavioral memory: routines, preferences, predictive suggestions (traffic, meetings) | ⬜ | Memory engine + proactive (Phase 3/4) |

## Tier 4 — Honest Frontier (not on the roadmap, by design)

| ID | Feature | Reality check |
|----|---------|---------------|
| J-26 | Dum-E (robot arm) | Hobby robotics possible (e.g. SO-101 arm + kernel tools) but a project of its own — park it |
| J-27 | The suit | No. |
| J-28 | Actual AGI | The harness *measures* capability and reliability; it doesn't guarantee intelligence. Build the runtime; let the models improve underneath it. |

---

## The persona spec (J-03 detail — what makes it feel like JARVIS, not a chatbot)

1. **Voice**: deep, calm, deliberate; British-dry wit; never gushing, never robotic.
   Implemented as a persona layer (voice + style directives + example dialogues), not
   prompt sprinkles — one source of truth in the kernel, versioned, eval-able.
2. **Address & memory**: "sir" + known people with roles; per-person memory (J-02, J-04).
3. **Anticipation**: states what it did before being asked, once, briefly — and always
   within consent policy (J-19).
4. **Barge-in etiquette**: stops instantly when spoken to, resumes or abandons
   intelligently (J-01).
5. **Honesty about limits**: "I'm afraid I can't do that yet, sir" beats hallucination.

## Priority order (CEO view)

**Next 90 days:** J-01 (in-session wake + duplex) · J-04 (memory) · J-06 (screen
understanding, fixed properly) · J-08 (real briefing) · J-14 (Home Assistant — the
cheapest "wow, it's JARVIS" moment that exists).
**Then:** J-07, J-11, J-19, J-21.
**The rest** queue behind measured reliability on the 50-task benchmark.

> Research companions: `02_voice_stack.md` · `03_computer_use_vision.md` ·
> `04_memory_systems.md` · `05_agent_frameworks_mcp.md` · `06_local_models.md` ·
> `07_open_source_jarvis_projects.md` · `08_integration_automation.md` ·
> Strategy: `../ROADMAP.md`
