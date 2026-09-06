# 07 — Open-Source JARVIS Projects: What Exists & What to Steal

*Research doc 7 of 8, companion to `01_jarvis_feature_catalog.md`. Serves **J-20**
(holographic HUD), the persona (J-03), and general "has someone already built this?"
due diligence for every J-ID. Written 2026-09-07; sources verified Sep 2026.*

**Honest framing:** dozens of "JARVIS" repos exist on GitHub, but almost all are
demo-grade: a wake word + ChatGPT wrapper + pycaw volume tricks. The 2026 trend is
real though — local-first stacks (openWakeWord + Whisper + Ollama/Qwen + local TTS)
replaced the old `pip install SpeechRecognition` scripts. ULTRON is already *ahead* of
most of these (real duplex voice, function calling, dashboard); the value here is
(1) confirming we're not missing a better architecture, (2) stealing UI/HUD ideas,
(3) learning from the serious platforms' mistakes.

---

## 1. The serious platforms (the only ones worth architectural study)

| Project | State (Sep 2026) | Stars | License | Lessons for ULTRON |
|---|---|---|---|---|
| **Leon AI** (leon-ai/leon) | **2.0 rebuild in progress** (Mar 2026 "Road to 2.0"): agentic loop, advanced memory, self-awareness, proactive behaviors, local LLM support — Node.js+Python | ~17k | MIT | The closest public project to our roadmap. Validates: agentic loop + memory + proactivity is *the* 2026 direction for personal assistants. Watch their memory-write policy and proactive consent UX |
| **OpenVoiceOS (OVOS)** (OpenVoiceOS) | Mycroft's living successor (Mycroft the company died 2023); `pip install ovos-core`, Foundation-backed, easy installer, local-LLM integrations | ~1k+ core | Apache-2.0 | Mycroft's death = the cautionary tale: a voice platform without a sustainable model layer died; skills ecosystem lived on. Steal: their skill→intent decoupling and message-bus design (echoes our event bus) |
| **Home Assistant Assist** (home-assistant) | The most-deployed local voice pipeline in the world (wake→STT→intent→TTS), Year of the Voice artifacts | ~80k (HA core) | Apache-2.0 | Best-in-class *pipeline plumbing* and device integration; deliberately NOT an open-ended agent (intent-matched). We integrate with it (doc 08) rather than rebuild it |
| **OpenJarvis** (open-jarvis/OpenJarvis) | Research framework: "Personal AI, on personal devices," composable on-device systems, cloud optional | small/academic | — | Aligned philosophy (local-first harness); worth tracking for on-device composition ideas |
| **Reference class** (from ROADMAP §2): OpenHands, Letta/MemGPT, mem0, Open Interpreter, AutoGPT | — | — | — | Covered in `04`/`05`; listed here so this doc is the one-stop "who's who" |

**Takeaway:** nobody has shipped "JARVIS as a product" — the winning 2026 pattern is
exactly our roadmap (kernel + MCP + memory + voice front-end). No pivot needed.

## 2. JARVIS-flavored repos (demo tier — ideas only)

| Repo | What it does | Worth taking |
|---|---|---|
| [llm-guy/jarvis](https://github.com/llm-guy/jarvis) | Voice assistant, wake word + local LLM (Qwen via Ollama), fully offline | Validates our doc-02/06 local stack; its wake-word-in-session approach is common |
| [isair/jarvis](https://github.com/isair/jarvis) | 100% offline assistant; wake word mid-sentence; "stop" to interrupt; dictation mode | UX details: wake-anywhere-in-sentence + single-word interrupt + dictation mode — cheap to add to our voice UX |
| [sukeesh/Jarvis](https://github.com/sukeesh/Jarvis) | Classic CLI assistant, cross-platform | Historical; shows why hardcoded-command assistants plateau (our own actions/ dir is the same pattern, which Phase 1 retires) |
| [harsh-raj00/my-jarvis](https://github.com/harsh-raj00/my-jarvis) | React + Three.js cinematic Iron Man HUD over FastAPI + Gemini, WebSocket live | **Direct HUD reference for J-20** — same backend shape as ours (FastAPI + WS); forkable UI patterns |
| [Jarvis-CV](https://github.com/Suryansh777777/Jarvis-CV) | Next.js + Three.js + MediaPipe AR widgets (arc reactor, global net, solar array), face tracking | Widget vocabulary + face-tracking presence detection (could drive "who's at the desk" awareness) |
| [stark-systems](https://github.com/jarvis-openclaw-assistant/stark-systems) | Vanilla HTML/CSS/JS cinematic HUD, no framework | Zero-dependency HUD option if the dashboard stays lightweight |
| GitHub topics: [`jarvis-assistant`](https://github.com/topics/jarvis-assistant), [`iron-man-jarvis-assistant`](https://github.com/topics/iron-man-jarvis-assistant), [`tony-stark`](https://github.com/topics/tony-stark) | Ongoing discovery | Re-scan quarterly; the hobby tier iterates fast on HUD effects |
| [Saturday (r/golang)](https://www.reddit.com/r/golang/comments/14rd99b/project_saturday_open_source_self_hosted_jarvis/) | Self-hosted JARVIS in Go | Proof the demand is cross-stack; nothing to import |
| Community build [r/selfhosted thread](https://www.reddit.com/r/selfhosted/comments/1uw8nyc/jarvis_a_fully_selfhosted_opensource_llmnative/) | Local-only JARVIS (Whisper + wake word, 8 GB+ NVIDIA) | Hardware expectations of the target audience |

## 3. HUD (J-20) — design direction

The best HUDs converge on the same elements; our FastAPI dashboard + Qt HUD should adopt:

1. **A central reactive core** (arc-reactor) that pulses with voice activity — we already
   have audio levels in the voice loop; wiring them to a WebGL shader is a weekend job.
2. **Live telemetry rings**: CPU/GPU/RAM/network as orbiting arcs (psutil data exists in
   `system_monitor.py`).
3. **Task/fleet monitor**: background jobs (Phase 2 orchestrator) streaming over the
   existing WebSocket as status cards.
4. **Camera wall** (J-16): VLM-captioned thumbnails, event-highlighted.
5. **Waveform/interrupt bar** for barge-in feedback (users must see that JARVIS heard
   the interruption).
6. Tech choice: keep the dashboard web-based (React/Three.js like `my-jarvis`, or vanilla
   WebGL like `stark-systems`); the Qt HUD stays as the always-on-desktop minimal layer.
   Never two competing HUD stacks (kill-list rule).

## 4. What the failures teach (pattern-level)

- **Mycroft died** selling hardware with no recurring model revenue → ULTRON stays
  BYO-API-key/local, sells nothing, depends on no marketplace.
- **AutoGPT became a meme** by shipping autonomy before reliability → our eval gate
  (doc 05 §7) exists precisely for this.
- **The hobby-JARVIS plateau**: every repo that hardcoded 50 commands stalled at 50.
  The kernel/MCP architecture is the documented escape — confirm, don't deviate.
- **Leon 2.0** is re-architecting toward exactly our Phase 1–3 list; bookmark their
  write-ups as external validation and idea source (they blog at blog.getleon.ai).

## 5. Integration notes for ULTRON

- No adoption from the demo tier; steal only UX details (interrupt word, dictation
  mode, HUD widgets).
- Add Leon blog + OVOS release notes to a quarterly "platform watch" in the roadmap
  cadence.
- For J-20, prototype the reactive-core HUD on the existing dashboard before touching
  the Qt layer; keep one HUD stack.
- Presence detection via camera face-tracking (Jarvis-CV idea) only after J-16 lands
  and with the consent policy — cameras are the highest-privacy surface in the house.

*Cross-refs: `02_voice_stack.md` (the local voice stack these repos emulate) ·
`03_computer_use_vision.md` (camera/face detection for presence) ·
`05_agent_frameworks_mcp.md` (why the kernel beats the hardcoded-command tier) ·
`08_integration_automation.md` (Home Assistant integration detail).*
