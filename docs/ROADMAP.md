# ULTRON → General Agent Harness: CEO/CTO Strategy & Roadmap

*Written 2026-09-07. Audience: the maintainers. Voice: candid. The goal stated by the
founder: "build something close to an AGI harness." This document is the honest answer
to that goal.*

---

## 0. The Executive Verdict (CEO)

**ULTRON today is a working demo, not a harness. It is roughly 1% of the way to the
goal — but the 1% that exists is the wrong 1% to scale.**

What exists: a Windows voice assistant locked to a single Gemini Live model, with 19
handwritten action scripts, a JSON-file memory capped at 2,200 characters, a FastAPI
dashboard with serious security holes, zero tests, zero CI, and three named crash bugs
sitting on the flagship features.

The founder's metaphor is right, and it points at the right conclusion: **you cannot
build an ocean by pouring more drops into a cup.** Every hour spent adding a 20th
action script (a 36th Steam AppID, another GUI-automation hack) makes the cup slightly
fuller and the ocean no closer. The ocean is not more features — the ocean is an
architecture: a runtime in which *any* capability can be plugged in, planned over,
remembered, tested, and safely executed. That architecture does not exist yet. It must
be built, and the existing demo becomes its first skill pack — not its foundation.

**The strategic decision this document commits to:**

1. **Freeze feature work.** No new action modules, no new UI polish, until the Kernel exists.
2. **Re-architect, don't rewrite blind.** The working parts (Gemini Live audio loop,
   dashboard relay, wake word) are wrapped and preserved; the parts that block scale
   (god-module `main.py`, string-result tool protocol, JSON memory) are replaced.
3. **Adopt MCP as the tool protocol.** This is the single highest-leverage bet — it
   converts "we have 19 tools" into "we can use every tool the ecosystem has."
4. **Memory is the moat.** For a personal autonomous assistant, retrieval quality and
   consolidation quality are the product. Budget accordingly.
5. **Evals or it isn't real.** An agent harness with no benchmark suite cannot tell
   improvement from churn. Evals ship in Phase 1, not "later."

---

## 1. Honest Audit — What Actually Exists (CTO)

~14.4k LOC Python. Architecture: Qt HUD on main thread, asyncio loop in a daemon
thread, Gemini Live native-audio session with real function calling, 7 background
tasks in a reconnecting TaskGroup, in-process FastAPI dashboard, wake-word launcher.

### Scorecard (harness subsystems)

| Subsystem | Status today | Grade |
|---|---|---|
| Agent loop | Single-turn tool calls inside a voice session; one hardcoded planner (`dev_agent.py`) | **D** |
| Tool kernel | 19 one-off scripts, string-in/string-out, duck-typed signatures, per-tool lambdas in `main.py:298-441` | **D−** |
| Model layer | Hard-locked to one Gemini model; magic model strings scattered across 8+ modules; `core/llm_client.py` (Ollama/OpenAI) is **dead code** — imported by nothing | **D** |
| Memory | One JSON file, whole-file dump into prompt, 2,200-char cap, no embeddings, no retrieval; `cmr_manager.py` and `reminder_manager.py` are dead code | **F** |
| Perception | Screen/vision path is **broken** in the live session (see bugs below); two competing vision stacks, one dead | **F** |
| Orchestration | No subagents, no task queue, no checkpoints, no background jobs | **F** |
| Safety/permissions | `exec()` of LLM code (`desktop.py:87`), pip-installs LLM-chosen packages (`dev_agent.py:248`), auto-opens firewall + flips network profile to Private (`dashboard/server.py:100-233`), kills user processes by name, TLS private keys committed to git | **F** (actively dangerous) |
| Evals/tests | None. Not one test file | **F** |
| UX/voice | Genuinely good: real-time audio, 50 ms barge-in interrupt, wake word, phone relay, dashboard | **B+** |

The one strong subsystem is the voice UX. That's the demo. It is not the harness.

### The three bugs sitting on flagship features

1. **Screen vision crashes every time.** `main.py:346` calls `_capture_screen`, which
   is never imported (only `_capture_camera`, `main.py:43`) → `NameError` on every
   screen-angle vision request. The camera branch calls `ui.start_camera_stream()`
   (`main.py:341`), which **raises NotImplementedError** (`ui.py:573-576`).
2. **The emergency monitor crashes the session.** `system_monitor.py:154` uses
   `os.getpid()` without importing `os` → `NameError` the first time the ≥95% CPU
   auto-kill path triggers, propagating out of `asyncio.to_thread` (`main.py:739`)
   and tearing down the TaskGroup into a reconnect loop.
3. **Startup race.** `ui.py:634` uses `time.sleep` while `time` is only imported
   method-locally elsewhere (`ui.py:465, 525`) → `NameError` in the startup thread.

### The seven structural sins (why this can't scale by addition)

1. **God module.** `main.py` is config + prompts + dispatch + audio I/O + relay +
   monitors in one 1,060-line file with 20 near-identical `_handle_*` lambdas.
2. **String-result protocol.** Every tool returns a human string that is
   simultaneously the FunctionResponse, the log line, and something spoken aloud —
   raw exception text gets dictated to the user (`main.py:227-230`). Tool output is
   injected back into the session as a **user-role** message (`speak()`,
   `main.py:216-225`), so tool speech and real user input are indistinguishable to
   the model. This alone caps agent reliability.
3. **Duplication everywhere.** Four parallel config-access paths to one file; two
   reminder systems; two vision stacks; two TTS stacks (`core/tts.py` unused); two
   STT stacks (`core/stt.py` unused); two `organize_desktop` implementations that
   both move the user's files; two shutdown tools; JARVIS/ULTRON/HUNNY naming churn.
4. **Hidden second-tier LLM calls.** Tools make their own one-shot Gemini calls with
   hardcoded model names (`"gemini-3.5-flash-lite"`, `"gemini-3.6-flash"`), invisible
   to the outer loop, unbudgeted, unlogged.
5. **Free-form tool parameters.** `browser_control` takes a 20-value action string
   (`tool_declarations.py:167`); invalid actions surface at runtime. `core/prompt.txt`
   references an `agent_task` tool that is declared nowhere.
6. **Security posture of a malware sample.** Dashboard binds 0.0.0.0, elevates via
   UAC to open the firewall and flip the network profile Public→Private, keeps
   unpruned bearer tokens, accepts plaintext commands (encryption is opt-in),
   leaves local WebSockets authless, and ships TLS private keys in git.
7. **No falsifiability.** No tests, no lint, no CI, no type checking. Every change is
   a vibe. This is the root cause that let bugs 1–3 ship on flagship paths.

---

## 2. What We Are Actually Building (definition discipline)

"Close to AGI" is not a spec. The buildable, falsifiable version of the goal is:

> **A general agent runtime** — a local-first kernel that lets an LLM plan and act
> across a user's whole computer and the web, with any model, any tool (MCP),
> persistent memory, safe sandboxed execution, and a measured task-success rate
> that goes up every release.

Concretely, the harness has **eight subsystems**, each with an interface, each
replaceable, each testable:

```
┌────────────────────────────────────────────────────────────────┐
│                        ULTRON KERNEL                            │
│  ┌─────────────┐  ┌──────────────┐  ┌───────────────────────┐  │
│  │ Model        │  │ Agent Loop   │  │ Memory Engine         │  │
│  │ Gateway      │◄─┤ plan→act→    ├─►│ working/episodic/     │  │
│  │ (any vendor) │  │ observe→     │  │ semantic/procedural   │  │
│  └─────────────┘  │ reflect      │  └───────────────────────┘  │
│                    └──────┬───────┘                             │
│  ┌─────────────┐  ┌──────▼───────┐  ┌───────────────────────┐  │
│  │ Perception   │  │ Tool Bus     │  │ Policy & Sandbox      │  │
│  │ screen/voice │  │ (MCP native) │  │ permissions, audit,   │  │
│  │ files/events │  │ local+remote │  │ isolation, dry-run    │  │
│  └─────────────┘  └──────────────┘  └───────────────────────┘  │
│  ┌──────────────────────────────┐  ┌───────────────────────┐   │
│  │ Task Orchestrator            │  │ Eval Harness          │   │
│  │ subagents, queues, jobs,     │  │ scenarios, regressions│   │
│  │ checkpoints, scheduler       │  │ success metrics       │   │
│  └──────────────────────────────┘  └───────────────────────┘   │
│              Event Bus connects everything (typed pub/sub)      │
└────────────────────────────────────────────────────────────────┘
        ▲                                    ▲
   Voice/HUD app (today's demo)         Dashboard / remote
   becomes "client #1" of the kernel    becomes "client #2"
```

**The reframe that matters:** today's `main.py` + UI *becomes a client of the
kernel*, not the kernel itself. The voice app is one front-end among several.

### Reference class (know your competitors, steal their lessons)

- **OpenHands, SWE-agent** — proved plan→act→observe loops with sandboxes and evals work.
- **Letta/MemGPT, mem0** — memory as a first-class subsystem with paging/consolidation.
- **AutoGPT** — the cautionary tale: autonomy without reliability metrics collapsed
  into a meme. We will not repeat this; evals ship before autonomy.
- **Open Interpreter / OpenAI Operator / Claude computer-use** — the computer-control bar.
- **MCP ecosystem** — the tool-distribution rail. Riding it is a force multiplier;
  inventing a private protocol is a tax.

---

## 3. The Five Strategic Bets

1. **MCP, both directions.** ULTRON's 19 actions get wrapped as an MCP server
   (ULTRON *serves* tools), and the kernel gets an MCP client (ULTRON *consumes* the
   ecosystem's servers: GitHub, filesystem, browsers, anything). One bet collapses
   our biggest weakness (few tools) and our biggest risk (proprietary protocol).
2. **Provider-agnostic gateway with one interface.** Text streaming, tool calls,
   vision, and audio-as-optional-modality, behind one Python interface. Gemini, Claude,
   OpenAI, Ollama/LM Studio. No module outside the gateway may name a model string —
   enforced in code review and by a lint rule.
3. **Memory as a product, not a file.** SQLite + vector index (sqlite-vec or LanceDB),
   typed stores (episodic events, semantic facts, procedural skills), write policies
   (what deserves to be remembered), retrieval with citations back into context, and
   a recall-quality eval. This is the component users cannot get from a raw LLM.
4. **Safety is a subsystem, not a vibe.** Every tool gets a risk class
   (read / write / execute / destructive), a consent policy, a dry-run mode, an
   audit log. Anything model-authored runs in a sandbox (Windows Job Objects +
   restricted token minimum; container if available). The firewall-flipping and
   exec() behavior is deleted, not improved.
5. **Eval-driven development.** A 50-task scenario suite (file ops, web, coding,
   multi-step recall, recovery-from-failure) with an LLM-judged + scripted-verifier
   score. CI gate: success rate may not drop. This is what turns "many issues" into
   a monotonic improvement machine.

---

## 4. Roadmap

Each phase has an **acceptance gate** — do not start the next phase until it passes.

### Phase 0 — Stabilize & De-Risk (1–2 weeks, do now)
*Goal: stop the bleeding. The demo becomes safe and falsifiable.*

- Fix the three crash bugs (§1) and the `proactive.py` silence math bug (`proactive.py:54-55`
  computes `now - last_triggered + min_silence` instead of `now - last_user_speech`).
- **Security triage, this week:** remove TLS keys from git (rotate them — removal
  from history is required, they are burned), make dashboard bind 127.0.0.1 by
  default with 0.0.0.0 as explicit opt-in, delete the Public→Private network-profile
  flip and the auto-firewall UAC script, make encryption mandatory, prune tokens,
  auth local WebSockets. Delete `exec()` path in `desktop.py`; gate `dev_agent`'s
  pip-install behind an allowlist prompt.
- Single config source of truth: one `config/loader.py`, every other access path deleted.
- Kill dead code: `core/tts.py`, `core/stt.py`, `cmr_manager.py`,
  `reminder_manager.py`, `_VisionSession` in `screen_processor.py`, duplicate
  `organize_desktop`, `JarvisUI`/`JarvisLive` aliases (decide the name once).
- CI scaffold: pytest + ruff + mypy (advisory) on GitHub Actions. First 20 tests are
  characterization tests around the current tool handlers so refactors stop being blind.
- Wire `core/llm_client.py` in or delete it (currently the advertised provider
  switching in `ui.py:214-228` calls nothing that exists in the live path).

**Gate:** CI green; no known crash paths; security holes closed; dead code gone.

### Phase 1 — Kernel v0 (3–5 weeks)
*Goal: the eight subsystems exist as thin, typed interfaces with a minimal loop.*

- **Event bus:** typed async pub/sub; every subsystem talks through it, nothing
  imports `main.py` ever again.
- **Tool kernel:** `Tool` protocol = JSON-schema parameters, structured
  `ToolResult` (ok/error/data/artifacts/risk), timeout, retry budget, risk class,
  permission scope. Replace the 20 lambdas with an auto-registering decorator.
  Delete the string-result protocol; define what the model sees vs. what the user
  hears as two different renderings.
- **Model gateway v0:** one interface; Gemini + Ollama implementations; the Live
  audio loop becomes a *modality adapter* over the same kernel, not a separate world.
- **Agent loop v0:** text-mode plan→act→observe with max-N steps, visible trace,
  abort/replan on repeated failure. Voice stays a thin client.
- **Policy engine v0:** risk classes enforced, consent prompts, audit log (SQLite).
- Port all 19 actions to the Tool kernel (mostly mechanical; keep their logic,
  fix their interfaces).

**Gate:** a text session can complete 10 scripted multi-step tasks end-to-end using
the new kernel, with the voice demo unchanged on top of it.

### Phase 2 — MCP + Orchestration (3–5 weeks)
- Ship ULTRON tools as an MCP server; add an MCP client to the Tool Bus.
- **Task Orchestrator:** durable task queue, background jobs, checkpoints/resume,
  hierarchical planning with subagents (each with scoped tools + own context
  window), parallel tool execution where risk class allows.
- Replace `dev_agent` with a proper coding subagent using the orchestrator
  (plan → write → run → observe → fix loops, sandboxed, no ambient pip-install).

**Gate:** "research X and write a report to Desktop" runs as a background job with
checkpoints, using ≥3 tools including one external MCP server, and survives a
kernel restart mid-task.

### Phase 3 — Memory Engine (3–4 weeks)
- SQLite + vector store; episodic (events per session), semantic (extracted facts),
  procedural (successful tool sequences → replayable skills).
- Write policy: LLM proposes memory writes with justification; consolidation job
  deduplicates/decays; retrieval is relevance-ranked with recency and importance.
- Replace the 2,200-char prompt dump with retrieved context assembly; the old
  `long_term.json` is migrated once, automatically.

**Gate:** recall eval — 40 questions about things the user said in past sessions —
goes from near-random (today) to >80% correct, measured, in CI.

### Phase 4 — Perception & Autonomy (4–6 weeks)
- Fix vision properly: one vision stack, screen capture through the kernel as a
  tool with consent, live screen-read loop for computer-use actions.
- Rebuild proactive behavior honestly: event-driven (time, reminders, system
  events, scheduled jobs) instead of a mislabeled silence timer.
- Permission-aware computer control with dry-run preview of what will be clicked/typed.

**Gate:** 20 computer-use scenarios pass at >70% (measured), with zero destructive
actions executed without consent.

### Phase 5 — Eval Harness Maturity & Self-Improvement (ongoing)
- Full 50-task suite, regression dashboard, per-subsystem scorecards in CI.
- Procedural memory closes the loop: failures produce skills that make later runs
  pass ("the harness that improves itself is the ocean filling up").

### Phase 6 — Scale-Out (later, deliberately unprioritized)
Multi-device sync, team deployments, plugin/marketplace story. **Explicitly not
started before Phase 5.** This is where CEOs usually get seduced; we pre-commit to
not going there early.

---

## 5. The Kill List (what we stop doing, permanently)

- No new action modules under `actions/` in the old style — new capabilities are
  Tools (Phase 1) or MCP servers.
- No feature that exists only for a demo video (35 hardcoded Steam AppIDs,
  blind GUI-automation message sending to Instagram, form-spam with fake personas —
  the last one is also an ethics/liability problem; delete it).
- No second implementation of anything. One config path, one reminder system,
  one vision stack, one TTS choice, one name (pick "ULTRON", purge the rest).
- No LLM call outside the gateway. No model name outside the gateway.
- No raw exception text spoken aloud to the user, ever.

## 6. North-Star Metrics & Operating Cadence

- **NSM: 50-task benchmark success rate** (autonomy × reliability). Secondary:
  recall accuracy (memory), p95 first-action latency (voice), consent-prompt rate
  (safety UX), tasks completed unattended (orchestration).
- Weekly: review benchmark deltas; anything that drops a metric gets fixed or
  reverted before new work. Monthly: one phase gate review, brutally honest.
- Solo-dev reality check (CEO): this roadmap is ~4–6 months of focused solo work.
  Cut scope, not gates. If forced to choose between Phase 3 (memory) and Phase 4
  (autonomy), memory wins — reliability compounds, demos don't.

## 7. Risk Register

| Risk | Mitigation |
|---|---|
| Re-architecture stalls and demo regresses | Phase 0 characterization tests; voice demo stays on old path until Phase 1 gate passes |
| Gemini Live lock-in / API changes | Gateway abstraction is Phase 1 priority #1 |
| Security incident from current holes | Phase 0 security triage is this week, not this quarter; rotate committed keys |
| Scope creep ("just one more feature") | Kill List + frozen feature budget until Phase 1 |
| Solo burnout | Phase gates are small; every phase ships something visible |
| AutoGPT trap (autonomy without reliability) | Evals in CI from Phase 0; autonomy features gated on measured success rates |

---

*The ocean is an architecture, not a pile of drops. Build the kernel; the water
will follow.*
