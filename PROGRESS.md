# PROGRESS.md — ULTRON Live Task Board

*Last updated: 2026-09-07 (restructure: all phases divided into streams; P0-A
completed & signed by zcode-p0a). Read `AGENTS.md` first. Append-only except your own rows.*

## How to use (STRICT — every agent, every chat)

1. **Claim** a stream: Status → 🔶 + Owner → your chat name + date.
2. **Update your row in the SAME commit as your code.** Board updates are part of
   Definition of Done. A commit without its board update is an incomplete commit.
3. **Sign-off:** when a task is done, set Status → ✅ and fill the Sign-off column:
   `✍ <chat-name> <date>` (+ evidence ref in Notes). Signing = "I verified this."
4. **DEP rule (dependencies):** every row has a `DEP` column:
   - `🔓 START NOW` → proceed, zero dependencies.
   - `DEP: <ID>` → **check that ID on this board.** If it is ✅ **and signed** → proceed
     (mind merge state in Notes). If still ⬜/🔶 → **STOP and tell the user**:
     *"[task] is blocked by [ID], not signed off yet — pick a 🔓 stream or wait."*
     Never "quickly do the dependency yourself" — that causes conflicts.
5. Phase gates apply (roadmap §4): Phase 1 streams unlock when the Phase 0 gate is
   verified & logged. Claims stale >48h with no commits may be taken over (note in Findings).

Legend: ⬜ open · 🔶 in-progress · ✅ done+signed · 🚫 blocked (reason in Notes)

---

## Phase 0 — Stabilize & De-Risk
**Gate:** CI green · no known crash paths · security holes closed · dead code gone.

### P0-A — Crash bugs · owns: `main.py`, `actions/system_monitor.py`, `ui.py`, `actions/proactive.py` — **complete, on branch `p0-crash-bugs` pending merge review**
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-A1 | `main.py:346` `_capture_screen` NameError — screen vision path fixed (J-06) | 🔓 | ✅ | zcode-p0a | ✍ zcode-p0a 2026-09-07 — E2E screenshot 106,981 B verified |
| P0-A2 | Camera branch NotImplementedError — feature-flagged off; live preview → Phase 4 (J-16) | 🔓 | ✅ | zcode-p0a | ✍ zcode-p0a 2026-09-07 — probe: start emits preview-unavailable log, stop/show no-op; `grep NotImplementedError ui.py` → 0 hits |
| P0-A3 | `system_monitor.py:154` missing `import os` | 🔓 | ✅ | zcode-p0a | ✍ zcode-p0a 2026-09-07 — runs clean w/ stubbed process_iter |
| P0-A4 | `ui.py:634` `time.sleep` NameError (killed startup thread on fresh installs) | 🔓 | ✅ | zcode-p0a | ✍ zcode-p0a 2026-09-07 — probe: `ui.time is time` → True (module scope), `import ui` clean |
| P0-A5 | `proactive.py:54-55` silence math | 🔓 | ✅ | zcode-p0a | ✍ zcode-p0a 2026-09-07 — triggers at 16 min real silence, not 5 |

### P0-B — Security triage · owns: `dashboard/server.py`, `actions/desktop.py`, `actions/dev_agent.py`, TLS keys, `.gitignore`
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-B1 | Remove committed TLS keys, **rotate**, purge git history | 🔓 | ⬜ | | keys are burned |
| P0-B2 | Dashboard binds `127.0.0.1` default; `0.0.0.0` explicit opt-in only | 🔓 | ⬜ | | |
| P0-B3 | Delete auto-firewall UAC + Public→Private profile flip | 🔓 | ⬜ | | |
| P0-B4 | Mandatory encryption, prune stale tokens, auth local WebSockets | 🔓 | ⬜ | | |
| P0-B5 | Delete `exec()` (`desktop.py:87`); pip-install behind allowlist (`dev_agent.py:248`) | 🔓 | ⬜ | | |

### P0-C — Dead code & dedup · owns: `core/tts.py`, `core/stt.py`, `memory/cmr_manager.py`, `reminder_manager.py`, `screen_processor.py`, aliases
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-C1 | Delete dead files: `core/tts.py`, `core/stt.py`, `cmr_manager.py`, `reminder_manager.py`, `_VisionSession` | 🔓 | ✅ | zcode-p0c 2026-09-07 | ✍ zcode-p0c 2026-09-07 — deleted `core/tts.py`, `core/stt.py`, `memory/cmr_manager.py`, `memory/reminder_manager.py` (board's `reminder_manager.py` = `memory/` copy); dead second vision stack removed from `screen_processor.py` (`_VisionSession`, `_session*` globals, `_ensure_session`, dead `screen_process`/`warmup_session`, `__main__` block) — live `_capture_screen`/`_capture_camera` untouched. Evidence: `py -3.13` py_compile OK; capture import OK; `import main` OK; grep lingering refs → 0 |
| P0-C2 | Remove duplicate `organize_desktop` + duplicate shutdown tool | DEP: P0-A ✅signed | 🔶 | zcode-p0c 2026-09-07 | touches `main.py` regs — coordinate w/ `p0-crash-bugs` merge |
| P0-C3 | `core/llm_client.py`: wire in or delete | 🔓 | 🔶 | zcode-p0c 2026-09-07 | decide via `research/06` gateway plan |
| P0-C4 | Name purge: JARVIS/HUNNY aliases → **ULTRON** only | DEP: P0-A ✅signed | 🔶 | zcode-p0c 2026-09-07 | touches `main.py`/`ui.py` — coordinate merge |

### P0-D — Config single source · owns: `config/loader.py` + call sites
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-D1 | Write `config/loader.py` + tests (new files only) | 🔓 | ⬜ | | no migration yet — pure new code |
| P0-D2 | Migrate all call sites; delete 4 parallel access paths | DEP: P0-A ✅, P0-B | ⬜ | | touches many files incl. main/dashboard |

### P0-E — CI scaffold · owns: `tests/`, `.github/workflows/`, `pyproject.toml` — READ-ONLY on src
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-E1 | GitHub Actions: pytest + ruff + mypy (advisory) | 🔓 | ⬜ | | NOTE (zcode-p0a): CI must `pip install -r requirements.txt` — system `python` (3.14.7) lacks project deps; 3.13.7 has them |
| P0-E2 | First 20 characterization tests around current tool handlers | 🔓 | ⬜ | | tests only import, never modify src |

---

## Phase 1 — Kernel v0 (unlock: Phase 0 gate verified & logged)
*Every stream owns its own NEW files under `kernel/` — near-zero overlap. The one
real dependency is P1-A (contracts); code against the interface draft in
`research/00_README.md` + `research/05` before it lands if you want a head start.*

| ID | Task (owns) | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P1-A | **Kernel contracts**: `kernel/` pkg, `kernel/types.py` (ToolCall, ToolResult, Event, RiskClass), `kernel/bus.py` typed pub/sub | 🔓 (after gate) | ⬜ | | |
| P1-B | **Tool kernel**: `kernel/tools/` — Tool protocol, JSON-schema params, decorator, timeout/retry, risk metadata | DEP: P1-A | ⬜ | | testable standalone w/ fake tools |
| P1-C | **Model gateway v0**: `kernel/gateway/` — one interface; Gemini + Ollama adapters | DEP: P1-A | ⬜ | | spec: `research/06` §4 |
| P1-D | **Memory engine v0**: `kernel/memory/` — SQLite WAL, FTS5+sqlite-vec+RRF, BGE-M3, `long_term.json` migration | 🔓 (after gate) | ⬜ | | spec: `research/04` §10 |
| P1-E | **Policy engine + audit**: `kernel/policy/` — risk classes, consent, dry-run, audit log | DEP: P1-A | ⬜ | | |
| P1-F | **Port 19 actions** to Tool kernel wrappers | DEP: P1-B | ⬜ | | only stream touching old `actions/` |
| P1-G | **Agent loop v0**: `kernel/loop/` — plan→act→observe, max-N, trace, abort/replan | DEP: P1-B, P1-C | ⬜ | | |
| P1-H | **Voice-as-client**: live loop → modality adapter over kernel | DEP: P1-G, P0-A ✅ | ⬜ | | touches `main.py` |

**Gate:** text session completes 10 scripted multi-step tasks via kernel; voice demo unchanged.

## Phase 2 — MCP + Orchestration
| ID | Task (owns) | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P2-A | ULTRON tools as **FastMCP server** (`kernel/mcp_server.py`) | DEP: P1-B | ⬜ | | `research/05` §2 |
| P2-B | **FastMCP client** + mount filesystem/fetch/GitHub (`kernel/mcp_client.py`) | DEP: P1-B | ⬜ | | |
| P2-C | **Durable queue + orchestrator + subagents** (`kernel/orchestrator/`) | DEP: P1-A, P1-E | ⬜ | | `research/05` §5 |
| P2-D | Browser-use research tool (J-05) | DEP: P2-B | ⬜ | | consent-gated |
| P2-E | Coding subagent (J-11) | DEP: P2-C, P1-G | ⬜ | | sandbox: `research/03` §7 |
| P2-F | Briefing v1 (J-08) standalone (`kernel/briefing/`, `kernel/notify/` ntfy+Telegram) | 🔓 (after gate) | ⬜ | | standalone report; queue-integrate at P2-C |

**Gate:** "research X → report to Desktop" as checkpointed background job, ≥3 tools incl. external MCP server, survives restart.

## Phase 3 — Memory Engine (the moat)
| ID | Task (owns) | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P3-A | Write policy + consolidation (extract→ADD/UPDATE/DELETE, decay, reflection) | DEP: P1-D | ⬜ | | `research/04` §6 |
| P3-B | Recall eval: 40 questions (LOCOMO-style) + CI gate | DEP: P1-D | ⬜ | | harness draftable standalone |
| P3-C | Procedural memory: failures→skills, replay via loop | DEP: P1-D, P1-G | ⬜ | | `research/04` §7 |

**Gate:** recall eval >80% in CI.

## Phase 4 — Perception & Autonomy
| ID | Task (owns) | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P4-A | UIA-first computer control + consent/dry-run (J-06/J-07) | DEP: P1-B, P1-E | ⬜ | | `research/03` |
| P4-B | Voice upgrades: in-process openWakeWord, Silero VAD, speaker ID (J-01/J-02) | DEP: P1-H | ⬜ | | `research/02` |
| P4-C | Home Assistant via MCP-Assist + Frigate/MQTT (J-14/J-16/J-17) | DEP: P2-B | ⬜ | | `research/08` §1,§3 |
| P4-D | Proactive engine rebuild (J-19) + HUD v2 (J-20) | DEP: P2-C | ⬜ | | event-driven, consent classes |

**Gate:** 20 computer-use scenarios >70%; zero unconsented destructive actions.

## Phase 5 — Evals & Self-Improvement
| ID | Task (owns) | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P5-A | 50-task suite + regression dashboard in CI | DEP: P1-G | ⬜ | | `research/05` §7 |
| P5-B | Self-improvement loop (skills close failures) | DEP: P3-C | ⬜ | | |

---

## Verification Log (phase-gate deliverables — evidence or it didn't happen)
| Date | Deliverable | Verified by | Checks run + evidence | Result |
|---|---|---|---|---|
| 2026-09-07 | P0-A crash bugs (A1–A5), branch `p0-crash-bugs` @ 6991cec (code) + 92dae5e (board) | zcode-p0a | `python -m py_compile main.py ui.py actions/system_monitor.py actions/proactive.py` → OK (Py 3.14.7). Runtime probes on Py 3.13.7 (the install with project deps): A3 `auto_close_heavy_background_apps()` with stubbed `process_iter` → `[]`, no NameError; `sm.os.getpid()` resolves. A5 gate: triggers at 16 min silence, not at 5 min; `build_prompt` prints `User silence: 16 minutes` (matches real silence, was inflated by `+min_silence` before); cooldown blocks retrigger. A4 `ui.time` present at module scope. A2 `start_camera_stream` emits `SYS: Camera preview not available — using still capture only.`, stop/show no-op; `grep raise NotImplementedError ui.py` → 0 hits. A1 `import main` clean (full dep chain); E2E `_capture_screen()` → 106,981 bytes image/jpeg (real screenshot) | PASS — all 5 fixed, no new Kill-List violations |

## Findings / Blockers (append-only)
- 2026-09-07: board created from `docs/ROADMAP.md` §4 Phase 0; ownership split for parallel chats.
- 2026-09-07 (zcode-p0a): two Python installs — default `python` is 3.14.7 without project deps (`psutil` missing); Python 3.13.7 has them. P0-E: CI must install from `requirements.txt`.
- 2026-09-07 (zcode-p0a): camera live preview (HUD) intentionally deferred to Phase 4 (J-16) — flagged off in `ui.py`.
- 2026-09-07 (restructure): Phases 1–5 divided into owned-file streams (P1-A…P5-B); Sign-off + DEP columns added; startable-now set: P0-B, P0-C1/C3, P0-D1, P0-E (+ P0-C2/C4, P0-D2 unblocked after `p0-crash-bugs` merges).
- 2026-09-07 (zcode-p0a): compliance audit vs STRICT rules — the 5 P0-A code commits predate the same-commit rule (board update landed in 92dae5e); end state compliant, history deliberately NOT rewritten (unpushed branch, parallel worktrees active). Rule applied from now on. A2/A4 sign-off evidence refs added.
- 2026-09-07 (zcode-p0a): research/03 §9 Phase-0 vision item splits as — NameError fix = P0-A1 (done); "delete the parallel capture code in `screen_processor.py`" = P0-C1 deletions / Phase 1 kernel-owned `capture_screen()`, not P0-A scope.

## Changelog
- 2026-09-07: board created; streams P0-A…P0-E defined.
- 2026-09-07: zcode-p0a claimed P0-A (all 5 rows); branch `p0-crash-bugs`.
- 2026-09-07: P0-A complete — A1–A5 fixed, verified & signed; ready for merge review.
- 2026-09-07: full restructure — Phases 1–5 divided (P1-A…P5-B), Sign-off + DEP rules, strict update-in-same-commit rule.
- 2026-09-07: zcode-p0c claimed P0-C (C1–C4); branch `p0-dead-code` in worktree `ultron-p0c`, based on `p0-crash-bugs` tip 76623fa (merge order: after `p0-crash-bugs`).
- 2026-09-07: zcode-p0a — doc restructure committed (872c041); P0-A compliance audit done: A2/A4 sign-off evidence refs added, verification-log ref clarified; same-commit rule adopted going forward.
