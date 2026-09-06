# PROGRESS.md — ULTRON Live Task Board

*Last updated: 2026-09-07 (merge policy + Merge Queue added; P0-A & P0-D1 signed by zcode-p0a).
Read `AGENTS.md` first. Append-only except your own rows.*

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
6. **Merge your own branch when your stream is ✅+signed** (policy: `AGENTS.md` §3):
   rebase on `main` → merge → push → write `merged @ <sha>` in the stream header +
   changelog. Until P0-E's CI exists, your sign-off evidence IS the merge gate.
   Conflicts in another stream's files → STOP and tell the user. Signed work unmerged
   >24h is a policy violation — dependent streams wait on merges, not signatures.

Legend: ⬜ open · 🔶 in-progress · ✅ done+signed · 🚫 blocked (reason in Notes)

## Merge Queue (branches → `main`)
| Branch | Stream | State | Merged @ | Notes |
|---|---|---|---|---|
| `p0-crash-bugs` | P0-A | ✅ merged | 42e262e | merged & pushed to `main` 2026-09-07; unblocks P0-C2, P0-C4, P0-D2, P1-H |
| `p0-config` | P0-D | 🟡 partial (D1 signed; D2 🚫 on P0-B) | — | rebased on `main` (af628d4) after that merge; D1 (new files only) may merge early; D2 waits on P0-B |

---

## Phase 0 — Stabilize & De-Risk
**Gate:** CI green · no known crash paths · security holes closed · dead code gone.

### P0-A — Crash bugs · owns: `main.py`, `actions/system_monitor.py`, `ui.py`, `actions/proactive.py` — **complete & signed; MERGED to `main` @ 42e262e (2026-09-07)**
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
| P0-C1 | Delete dead files: `core/tts.py`, `core/stt.py`, `cmr_manager.py`, `reminder_manager.py`, `_VisionSession` | 🔓 | ⬜ | | dead = imported by nothing; zero conflicts |
| P0-C2 | Remove duplicate `organize_desktop` + duplicate shutdown tool | DEP: P0-A ✅signed | ⬜ | | touches `main.py` regs — coordinate w/ `p0-crash-bugs` merge |
| P0-C3 | `core/llm_client.py`: wire in or delete | 🔓 | ⬜ | | decide via `research/06` gateway plan |
| P0-C4 | Name purge: JARVIS/HUNNY aliases → **ULTRON** only | DEP: P0-A ✅signed | ⬜ | | touches `main.py`/`ui.py` — coordinate merge |

### P0-D — Config single source · owns: `config/loader.py` + call sites — **claimed by zcode-p0a (branch `p0-config`): D1 done, D2 blocked by P0-B**
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-D1 | Write `config/loader.py` + tests (new files only) | 🔓 | ✅ | zcode-p0a · 2026-09-07 | ✍ zcode-p0a 2026-09-07 — pytest 14/14 passed (Py 3.13.7); real-config smoke: loads, `get_api_key()` resolves (key names only, never values); stdlib-only, atomic writes w/ Windows PermissionError retry, no cache. Tests in `tests/test_config_loader.py` — hermetic (tmp_path), distinct filename so no P0-E2 overlap |
| P0-D2 | Migrate all call sites; delete 4 parallel access paths | DEP: P0-A ✅, P0-B | 🚫 | zcode-p0a · 2026-09-07 | 🚫 blocked: P0-B not signed off yet (AGENTS.md §3 DEP rule) — also wants `p0-crash-bugs` merged (touches main.py/ui.py/dashboard). Migration surface mapped in Findings (5 paths, not 4) |

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
| 2026-09-07 | P0-D1 `config/loader.py` + tests, branch `p0-config` | zcode-p0a | `python -m py_compile config/loader.py tests/test_config_loader.py` → OK. `python -m pytest tests/test_config_loader.py -v` (Py 3.13.7, pytest 9.1.1) → **14 passed** (hermetic: missing/corrupt/non-dict → `{}`, roundtrip, atomic-write no `.tmp` leftovers, RMW preserves keys, placeholder/strip/None key policy, 8-thread × 25-key concurrent-write stress → 200/200, on-disk file is valid JSON). Real-config smoke (Py 3.13.7): `load_config()` OK (key names: assistant_name, gemini_api_key, morning_brief_enabled, os_system, ui_color, user_name — values never printed), `get_api_key()` resolves → True. New files only; no call sites touched (D2) | PASS |

## Findings / Blockers (append-only)
- 2026-09-07: board created from `docs/ROADMAP.md` §4 Phase 0; ownership split for parallel chats.
- 2026-09-07 (zcode-p0a): two Python installs — default `python` is 3.14.7 without project deps (`psutil` missing); Python 3.13.7 has them. P0-E: CI must install from `requirements.txt`.
- 2026-09-07 (zcode-p0a): camera live preview (HUD) intentionally deferred to Phase 4 (J-16) — flagged off in `ui.py`.
- 2026-09-07 (restructure): Phases 1–5 divided into owned-file streams (P1-A…P5-B); Sign-off + DEP columns added; startable-now set: P0-B, P0-C1/C3, P0-D1, P0-E (+ P0-C2/C4, P0-D2 unblocked after `p0-crash-bugs` merges).
- 2026-09-07 (zcode-p0a): compliance audit vs STRICT rules — the 5 P0-A code commits predate the same-commit rule (board update landed in 92dae5e); end state compliant, history deliberately NOT rewritten (unpushed branch, parallel worktrees active). Rule applied from now on. A2/A4 sign-off evidence refs added.
- 2026-09-07 (zcode-p0a): research/03 §9 Phase-0 vision item splits as — NameError fix = P0-A1 (done); "delete the parallel capture code in `screen_processor.py`" = P0-C1 deletions / Phase 1 kernel-owned `capture_screen()`, not P0-A scope.
- 2026-09-07 (zcode-p0a): config access surface is **5** parallel paths, not 4 — main.py, ui.py, utils/env.py, memory/config_manager.py, plus `config/__init__.py::get_config()` (own cache) and `core/llm_client.py::_load_config()` (own reader). Also inconsistent across them: lru_cache vs manual vs no cache, `indent=2` vs `4`, missing key → `""` vs `None` vs raise, `len>15` heuristic vs placeholder check. All consolidated onto `config/loader.py` in D2.

## Changelog
- 2026-09-07: board created; streams P0-A…P0-E defined.
- 2026-09-07: zcode-p0a claimed P0-A (all 5 rows); branch `p0-crash-bugs`.
- 2026-09-07: P0-A complete — A1–A5 fixed, verified & signed; ready for merge review.
- 2026-09-07: full restructure — Phases 1–5 divided (P1-A…P5-B), Sign-off + DEP rules, strict update-in-same-commit rule.
- 2026-09-07: zcode-p0a — doc restructure committed (872c041); P0-A compliance audit done: A2/A4 sign-off evidence refs added, verification-log ref clarified; same-commit rule adopted going forward.
- 2026-09-07: zcode-p0a claimed P0-D (branch `p0-config`, stacked on `p0-crash-bugs`); D1 executing, D2 marked 🚫 blocked by unsigned P0-B.
- 2026-09-07: P0-D1 done — `config/loader.py` + 14 hermetic tests, verified & signed; D2 remains 🚫 until P0-B signs off.
- 2026-09-07 (planning): merge policy defined — streams merge their OWN signed branches (rebase → merge → push → board update); Merge Queue section added: `p0-crash-bugs` 🟢 merge-ready, `p0-config` 🟡 partial (D1).
- 2026-09-07: zcode-p0a — `p0-crash-bugs` MERGED to `main` @ 42e262e per merge policy. Gate evidence re-run on main: py_compile OK on all 4 owned files; merged files diff-identical to verified branch tip. Push follows; unblocks P0-C2/C4, P0-D2, P1-H.
- 2026-09-07: zcode-p0a — `p0-config` rebased on updated `main` (patch-PR batch #8–#11 verified not to touch P0-A files; loader tests 14/14 after rebase); Merge Queue updated.
