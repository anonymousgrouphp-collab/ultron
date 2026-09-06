# PROGRESS.md — ULTRON Live Task Board

*Last updated: 2026-09-07 (zcode-p0a — claimed P0-A). Every agent/chat: read `AGENTS.md`
first, then claim a stream here. Append-only except your own rows.*

## How to use

1. **Claim** a stream: Status → `in-progress`, Owner → your chat name + date.
2. Update **Status/Notes** after each subtask (✅ done / 🔶 partial / ⬜ open).
3. Phase-gate deliverables get an entry in **Verification Log** with evidence.
4. Claims stale >48h with no commits may be taken over — note it in Findings first.
5. Never start Phase 1 rows before the Phase 0 gate passes (roadmap §4).

---

## Phase 0 — Stabilize & De-Risk
**Gate (roadmap §4):** CI green · no known crash paths · security holes closed · dead code gone.

### P0-A — Crash bugs · owns: `main.py`, `actions/system_monitor.py`, `ui.py`, `actions/proactive.py`
| ID | Task | Status | Owner | Notes |
|---|---|---|---|---|
| P0-A1 | `main.py:346` `_capture_screen` NameError (never imported) — fix screen vision path (J-06) | ✅ | zcode-p0a · 2026-09-07 | import added; E2E screenshot grab verified (106,981 B JPEG) |
| P0-A2 | camera branch calls `ui.start_camera_stream()` → NotImplementedError (`ui.py:573-576`) — implement or feature-flag | ✅ | zcode-p0a · 2026-09-07 | feature-flagged off: start/stop/show stubbed to logged no-op; live preview deferred to Phase 4 (J-16) |
| P0-A3 | `actions/system_monitor.py:154` missing `import os` — kills session on CPU-kill path | ✅ | zcode-p0a · 2026-09-07 | `import os` added; auto-close path runs clean with stubbed process_iter |
| P0-A4 | `ui.py:634` `time.sleep` NameError (module-level `time` import missing) | ✅ | zcode-p0a · 2026-09-07 | worse than reported: kills the startup thread on fresh installs (no API key) — fixed with module-level import |
| P0-A5 | `actions/proactive.py:54-55` silence math (`now - last_triggered + min_silence` ≠ `now - last_user_speech`) | ✅ | zcode-p0a · 2026-09-07 | engine records `last_user_speech` in should_trigger; build_prompt reports real silence |

### P0-B — Security triage · owns: `dashboard/server.py`, `actions/desktop.py`, `actions/dev_agent.py`, TLS keys, `.gitignore`
| ID | Task | Status | Owner | Notes |
|---|---|---|---|---|
| P0-B1 | Remove committed TLS private keys, **rotate them**, purge from git history | ⬜ | — | keys are burned |
| P0-B2 | Dashboard binds `127.0.0.1` by default; `0.0.0.0` only as explicit opt-in | ⬜ | — | |
| P0-B3 | Delete auto-firewall UAC script + Public→Private network-profile flip | ⬜ | — | |
| P0-B4 | Make encryption mandatory, prune stale tokens, auth local WebSockets | ⬜ | — | |
| P0-B5 | Delete `exec()` of LLM code (`desktop.py:87`); gate `dev_agent.py:248` pip-install behind allowlist prompt | ⬜ | — | |

### P0-C — Dead code & dedup · owns: deletions + reference cleanup · **starts after P0-A merges**
| ID | Task | Status | Owner | Notes |
|---|---|---|---|---|
| P0-C1 | Delete dead: `core/tts.py`, `core/stt.py`, `cmr_manager.py`, `reminder_manager.py`, `_VisionSession` (`screen_processor.py`) | ⬜ | — | |
| P0-C2 | Remove duplicate `organize_desktop` (keep one), duplicate shutdown tool | ⬜ | — | |
| P0-C3 | `core/llm_client.py`: wire in or delete (ui.py:214-228 calls nothing that exists) | ⬜ | — | |
| P0-C4 | Name decision: purge JARVIS/HUNNY aliases → **ULTRON** only | ⬜ | — | |

### P0-D — Config single source · owns: `config/loader.py` + call-site migration · **after P0-A & P0-B merge**
| ID | Task | Status | Owner | Notes |
|---|---|---|---|---|
| P0-D1 | One `config/loader.py`; delete the four parallel config-access paths | ⬜ | — | touches many files |

### P0-E — CI scaffold · owns: `tests/`, `.github/workflows/`, `pyproject.toml` (ruff/mypy config) · **read-only on src**
| ID | Task | Status | Owner | Notes |
|---|---|---|---|---|
| P0-E1 | GitHub Actions: pytest + ruff + mypy (advisory) | ⬜ | — | |
| P0-E2 | First 20 characterization tests around current tool handlers | ⬜ | — | refactors stop being blind |

---

## Phase 1+ — Kernel (LOCKED until Phase 0 gate passes)
Event bus → tool kernel (structured `ToolResult`) → model gateway v0 (Gemini + Ollama,
per `research/06`) → agent loop v0 → policy engine → FastMCP server wrap (per
`research/05`). Rows get broken out when unlocked. Voice/UIA/memory designs: docs 02/03/04.

---

## Verification Log (phase deliverables — evidence or it didn't happen)
| Date | Deliverable | Verified by | Checks run + evidence | Result |
|---|---|---|---|---|
| 2026-09-07 | P0-A crash bugs (A1–A5), branch `p0-crash-bugs` @ bd36d98+1 | zcode-p0a | `python -m py_compile main.py ui.py actions/system_monitor.py actions/proactive.py` → OK (Py 3.14.7). Runtime probes on Py 3.13.7 (the install with project deps): A3 `auto_close_heavy_background_apps()` with stubbed `process_iter` → `[]`, no NameError; `sm.os.getpid()` resolves. A5 gate: triggers at 16 min silence, not at 5 min; `build_prompt` prints `User silence: 16 minutes` (matches real silence, was inflated by `+min_silence` before); cooldown blocks retrigger. A4 `ui.time` present at module scope. A2 `start_camera_stream` emits `SYS: Camera preview not available — using still capture only.`, stop/show no-op; `grep raise NotImplementedError ui.py` → 0 hits. A1 `import main` clean (full dep chain); E2E `_capture_screen()` → 106,981 bytes image/jpeg (real screenshot) | PASS — all 5 fixed, no new Kill-List violations (no new modules, no model strings, no spoken raw exceptions) |

## Findings / Blockers (append-only)
- 2026-09-07: board created from `docs/ROADMAP.md` §4 Phase 0; ownership split to allow 2–4 parallel chats.
- 2026-09-07 (zcode-p0a): two Python installs on this machine — default `python` is 3.14.7 without project deps (`psutil` missing); Python 3.13.7 has them. P0-E: CI must install from `requirements.txt`, not assume system deps.
- 2026-09-07 (zcode-p0a): camera live preview (HUD) is still unimplemented by design — flagged off in `ui.py`, real implementation is Phase 4 (J-16) per `docs/research/03` §5 capture note.

## Changelog
- 2026-09-07: zcode-p0a claimed P0-A (all 5 rows); branch `p0-crash-bugs`.
- 2026-09-07: P0-A complete — A1–A5 fixed, verification logged; branch `p0-crash-bugs` ready for merge review (no merge: Phase 0 gate needs CI green first).
- 2026-09-07: board created; streams P0-A…P0-E defined.
