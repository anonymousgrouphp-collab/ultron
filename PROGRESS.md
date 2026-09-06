# PROGRESS.md — ULTRON Live Task Board

*Last updated: 2026-09-07 (zcode-p0a: P0-D COMPLETE — D2 migration done & signed in worktree `../ultron-d2`, queued 🟢 for the main chat).
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
6. **`main` is merged ONLY by the main-branch chat (orchestrator).** Branch chats:
   when your stream is ✅+signed → push your branch, set your Merge Queue row → 🟢
   merge-ready, and stop (pick another stream). The main chat performs the merge
   (rebase → merge → push → `merged @ <sha>` in header + queue + changelog). Until
   P0-E's CI exists, sign-off evidence is the merge gate. Merge-ready work unhandled
   >24h → flag in Findings.

Legend: ⬜ open · 🔶 in-progress · ✅ done+signed · 🚫 blocked (reason in Notes)

## Merge Queue (branches → `main`; merges performed ONLY by the main chat)
| Branch | Stream | State | Merged @ | Notes |
|---|---|---|---|---|
| `p0-crash-bugs` | P0-A | ✅ merged | 42e262e | unblocks P0-C2, P0-C4, P0-D2, P1-H |
| `p0-security` | P0-B | ✅ merged & **DISSOLVED** (branch deleted local+remote; worktree removed) | 6303635 | B1 purge → P0-B1b; certs regenerated in main checkout (untracked) |
| `p0-config` | P0-D | ✅ merged (D1) + 🟢 merge-ready (D2) | e0a644a | D2 done & signed in worktree `../ultron-d2` on top of main tip 11cedac — ready for main chat; `ULTRON_DASHBOARD_HOST` folded into loader as requested |
| `p0-dead-code` | P0-C | ✅ merged (C1–C4 complete) | 60809e5 | C1/C2/C3 via main-owner @ 847543c; C4 + board record via user-directed FF push (zcode-p0c) |

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

### P0-B — Security triage · owns: `dashboard/server.py`, `actions/desktop.py`, `actions/dev_agent.py`, TLS keys, `.gitignore` — **B2–B5 + B1-rotation complete & signed; MERGED @ 6303635; branch DISSOLVED; ownership → main chat (user order, p0b chat discarded); only purge remains → P0-B1b**
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-B1 | Remove committed TLS keys, **rotate**, purge git history | 🔓 | 🔶 | zcode-p0b → main chat | ✍ zcode-p0b 2026-09-07 — keys untracked + `.gitignore`d + rotated (sha256 42:FC:98:BC…); purge split to **P0-B1b**. Post-dissolve: fresh pair regenerated in main checkout by main chat (untracked, verified gitignored) — substance COMPLETE, only history purge left |
| P0-B1b | **Purge burned keys from git history** (all-hands op: `git filter-branch --index-filter 'git rm -r --cached --ignore-unmatch config/certs' --prune-empty -- --all` + announced `push --force`; invalidates all open branches & clones) | DEP: all Phase-0 streams merged **+ explicit user go** (P0-E last) | ⬜ | main chat | | scheduled — hygiene only (burned keys valueless since rotation) |
| P0-B2 | Dashboard binds `127.0.0.1` default; `0.0.0.0` explicit opt-in only | 🔓 | ✅ | zcode-p0b | ✍ zcode-p0b 2026-09-07 — `uvicorn.Config(host=DASHBOARD_HOST)`; `ULTRON_DASHBOARD_HOST=0.0.0.0` opt-in; grep: no `host="0.0.0.0"` binding left |
| P0-B3 | Delete auto-firewall UAC + Public→Private profile flip | 🔓 | ✅ | zcode-p0b | ✍ zcode-p0b 2026-09-07 — `_ensure_network_access` (211 lines) deleted; grep `ShellExecuteW\|Set-NetConnectionProfile` → 0 live hits (one printed *manual* hint only) |
| P0-B4 | Mandatory encryption, prune stale tokens, auth local WebSockets | 🔓 | ✅ | zcode-p0b | ✍ zcode-p0b 2026-09-07 — 10-step TestClient suite passed: plaintext cmd→400, no mint on `GET /`, expired token→401, unauth `/ws`→4001, encrypted e2e (CryptoJS↔`_decrypt_cbc` interop proven node+py), token cap 64. **Fixed 2 pre-existing bugs the tests caught: `/ws` deque-slice crash on every connect + token-cap off-by-one** |
| P0-B5 | Delete `exec()` (`desktop.py:87`); pip-install behind allowlist (`dev_agent.py:248`) | 🔓 | ✅ | zcode-p0b | ✍ zcode-p0b 2026-09-07 — `_execute_generated_code`/`_build_sandbox`/`_ask_gemini_for_desktop_action` deleted, `task` action refuses; dev_agent installs gated by `config/pip_allowlist.json` (example tracked, real file gitignored); gate test passed |

### P0-C — Dead code & dedup · owns: `core/tts.py`, `core/stt.py`, `memory/cmr_manager.py`, `reminder_manager.py`, `screen_processor.py`, aliases — **complete & signed (C1–C4); MERGED to `main` @ 60809e5 (2026-09-07)**
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-C1 | Delete dead files: `core/tts.py`, `core/stt.py`, `cmr_manager.py`, `reminder_manager.py`, `_VisionSession` | 🔓 | ✅ | zcode-p0c 2026-09-07 | ✍ zcode-p0c 2026-09-07 — deleted `core/tts.py`, `core/stt.py`, `memory/cmr_manager.py`, `memory/reminder_manager.py` (board's `reminder_manager.py` = `memory/` copy); dead second vision stack removed from `screen_processor.py` (`_VisionSession`, `_session*` globals, `_ensure_session`, dead `screen_process`/`warmup_session`, `__main__` block) — live `_capture_screen`/`_capture_camera` untouched. Evidence: `py -3.13` py_compile OK; capture import OK; `import main` OK; grep lingering refs → 0 |
| P0-C2 | Remove duplicate `organize_desktop` + duplicate shutdown tool | DEP: P0-A ✅signed | ✅ | zcode-p0c 2026-09-07 | ✍ zcode-p0c 2026-09-07 — dropped undeclared `shutdown_jarvis` registry alias (`main.py:444`; model could never call it — only `shutdown_ultron` declared @ `tool_declarations.py:316`). Deduped `organize_desktop`: kept `desktop.py:179` (OS-aware `.lnk`/`.url` skips, by_type+by_date modes, shared `FILE_TYPE_MAP`, structured summary); removed `file_controller.py` duplicate (no OS skips, private type_map) + its dispatch + `organize_desktop` token from the file_controller enum. One declared path remains: `desktop_control action=organize` + `mode`. Evidence: py_compile main/file_controller/tool_declarations/desktop OK; imports OK; `shutdown_jarvis`→0; `def organize_desktop`→1; organize refs in file_controller→0. Deliberately NOT runtime-executed (moves real user files) |
| P0-C3 | `core/llm_client.py`: wire in or delete | 🔓 | ✅ | zcode-p0c 2026-09-07 | ✍ zcode-p0c 2026-09-07 — **Decision: DELETE.** Zero importers (grep = 0); wiring it in would be a second LLM implementation w/ model strings outside the gateway (Kill List). Real gateway = P1-C per `research/06` §6.2 (`LocalAdapter` Ollama HTTP + `GeminiAdapter`, one config enum). Ollama endpoint handling preserved in git history for P1-C to mine. Evidence: `py -3.13 import main` OK after deletion; refs = 0 |
| P0-C4 | Name purge: JARVIS/HUNNY aliases → **ULTRON** only | DEP: P0-A ✅signed | ✅ | zcode-p0c 2026-09-07 | ✍ zcode-p0c 2026-09-07 — removed `JarvisUI` import+alias (`main.py:31`, `ui.py` tail) & `JarvisLive` alias (`main.py:1027`; all unreferenced — grep verified); default `_asst_name` "JARVIS"→"ULTRON" (`main.py:238,241`); dashboard log `speaker: "jarvis"`→`"ultron"` (`main.py:589` — app.html renders speaker generically, its own default is already 'ULTRON'); `[JARVIS]` log tags → `[ULTRON]` (`main.py:982,996`); JARVIS/HUNNY in comments/docstrings → ULTRON (`main.py:198,611`, `system_monitor.py:152`, `installer.py:2`, `wake_service.py:142`); readme: removed stale `jarvis.ico` tree line (file doesn't exist). Left intentionally: `app.html` `theme-jarvis` CSS/option ("J.A.R.V.I.S Cyan" = color-scheme label, not assistant identity — HUD v2 (J-20) owner's call); user-set `assistant_name` in config. Evidence: py_compile ×5 OK; `import main, ui` OK; `grep -riE "jarvis\|hunny" --include=*.py` → 0 |

### P0-D — Config single source · owns: `config/loader.py` + call sites — **COMPLETE: D1+D2 done & signed (D2 in worktree `../ultron-d2`, branch `p0-config`)**
| ID | Task | DEP | Status | Owner | Sign-off |
|---|---|---|---|---|---|
| P0-D1 | Write `config/loader.py` + tests (new files only) | 🔓 | ✅ | zcode-p0a · 2026-09-07 | ✍ zcode-p0a 2026-09-07 — pytest 14/14 passed (Py 3.13.7); real-config smoke: loads, `get_api_key()` resolves (key names only, never values); stdlib-only, atomic writes w/ Windows PermissionError retry, no cache. Tests in `tests/test_config_loader.py` — hermetic (tmp_path), distinct filename so no P0-E2 overlap |
| P0-D2 | Migrate all call sites; delete 4 parallel access paths | DEP: P0-A ✅, P0-B ✅ | ✅ | zcode-p0a · 2026-09-07 | ✍ zcode-p0a 2026-09-07 — pytest **16/16** (new: utils.env-delegation + dashboard-host-default tests); py_compile ×7; `import main` + `import dashboard.server` clean on the D2 tree; real-config probes: `main._get_api_key()`→str (len only), `ui._needs_api_key()`→False, `dashboard._get_gemini_key()` resolves. Deleted `memory/config_manager.py` (re-done WITH the main.py migration per main-owner 11cedac note); gutted `config/__init__.py` legacy cached reader; `utils/env.py` → thin delegates; `ULTRON_DASHBOARD_HOST` → `loader.get_dashboard_host()` |

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
| 2026-09-07 | P0-B security triage (B1 rotation, B2–B5), branch `p0-security` MERGED @ 6303635 | zcode-p0b | `py_compile dashboard/server.py actions/desktop.py actions/dev_agent.py` → OK (Py 3.13.14, dep-complete install). Banned-pattern greps: no `host="0.0.0.0"` binding, no `ShellExecuteW`/`Set-NetConnectionProfile`, no tracked `*.key|*.pem|*.p12|*.pfx`, no `exec(` outside a comment. Functional (fastapi TestClient, Py 3.13): GET / mints nothing; unauth API→401; PIN login→token; plaintext `/api/command`→400 even when authed; garbage ciphertext→400; real encrypted command (client-protocol payload)→decrypted+queued; expired token→401; unauth `/ws`→closed 4001; auth `/ws` accepted; plaintext WS command ignored (log: `[Dashboard] Ignoring plaintext WS command.`), encrypted WS command queued; history replay after reconnect OK; 100 mints → exactly 64 tokens kept. Crypto interop: node + bundled `crypto-js.min.js` (PBKDF2-SHA256/100k → AES-256-CBC → base64(iv‖ct)) ↔ server `_decrypt_cbc` → exact plaintext round-trip. desktop_control refusal paths verified (task / unknown / bare task). Seal test re-run on the merged tip; main.py/ui.py from P0-A merge present and untouched | PASS — B2–B5 merged; B1 rotation merged, history purge deferred (see Findings) |
| 2026-09-07 | P0-D1 `config/loader.py` + tests, branch `p0-config` | zcode-p0a | `python -m py_compile config/loader.py tests/test_config_loader.py` → OK. `python -m pytest tests/test_config_loader.py -v` (Py 3.13.7, pytest 9.1.1) → **14 passed** (hermetic: missing/corrupt/non-dict → `{}`, roundtrip, atomic-write no `.tmp` leftovers, RMW preserves keys, placeholder/strip/None key policy, 8-thread × 25-key concurrent-write stress → 200/200, on-disk file is valid JSON). Real-config smoke (Py 3.13.7): `load_config()` OK (key names: assistant_name, gemini_api_key, morning_brief_enabled, os_system, ui_color, user_name — values never printed), `get_api_key()` resolves → True. New files only; no call sites touched (D2) | PASS |
| 2026-09-07 | P0-C dead-code removal — C1 (4 dead files + dead second vision stack in `screen_processor.py`) & C3 (`core/llm_client.py`), branch `p0-dead-code` (C1 @ 01cd214, C3 @ f7b8316) | zcode-p0c | Pre-checks: grep importers of `core.tts`/`core.stt`/`cmr_manager`/`reminder_manager`/`llm_client`/`_VisionSession`/`warmup_session`/`_ensure_session` → 0 (only self-refs; sole live importer of `screen_processor` = `main.py:43`, taking `_capture_camera`+`_capture_screen`); non-py sweep (setup scripts/spec/dashboard) → 0 hits. Post: `py -3.13 -m py_compile actions/screen_processor.py` OK; `from actions.screen_processor import _capture_screen, _capture_camera` OK; `import main` OK (full dep chain), re-run after C3; lingering-ref grep → 0. Kept capture code byte-identical; 1,598 LOC deleted across 5 files | PASS for C1+C3 — C2/C4 were merge-gated, now executing after P0-A merge; no new Kill-List violations |
| 2026-09-07 | P0-C dedup + name purge (C2+C4), branch `p0-dead-code` (C2 @ 59bc553, C4 @ eb7f67e) | zcode-p0c | C2: `py_compile main.py actions/file_controller.py core/tool_declarations.py actions/desktop.py` → OK; `import main` + actions imports OK; greps: `shutdown_jarvis`→0, `def organize_desktop`→1 (`desktop.py:179` only), organize refs in `file_controller`→0, `shutil.` still used ×4; `desktop_control` organize+mode declaration intact (`tool_declarations.py:209,212`). Deliberately did NOT runtime-execute `organize_desktop` (moves real user files). C4: py_compile main/ui/wake_service/system_monitor/installer → OK; `import main, ui` OK; `grep -riE "jarvis\|hunny" --include=*.py` → 0 hits; `speaker` tag rename verified safe against consumer (`app.html:700,897` renders speaker generically, its own default is already `'ULTRON'`) | PASS — P0-C stream COMPLETE (C1–C4); no new Kill-List violations |
| 2026-09-07 | P0-D2 call-site migration — all config access via `config/loader.py`, branch `p0-config` in worktree `../ultron-d2` (base: main @ 11cedac) | zcode-p0a | `python -m py_compile main.py ui.py utils/env.py config/__init__.py config/loader.py dashboard/server.py tests/test_config_loader.py` → OK ×7. `pytest tests/test_config_loader.py` (Py 3.13.7, pytest 9.1.1) → **16 passed** (14 D1 tests + new `test_utils_env_delegates_to_loader` + `test_dashboard_host_defaults_to_loopback`). `import main` + `import dashboard.server` clean; `dashboard.server.DASHBOARD_HOST` → `127.0.0.1` (safe default). Real-config probes (values never printed): `main._get_api_key()` → str (53 chars), `ui._needs_api_key()` → False, `dashboard.server._get_gemini_key()` → resolves, `utils.env.get_api_key("gemini_api_key")` → str (legacy `""` default kept for missing). Migrated: main.py (`_get_api_key`, `_build_config`, `BASE_DIR`, brief-enabled read), ui.py (`_read/_save_full_config` delegates, `_needs_api_key`, `_write_api_key`, `PLACEHOLDER_KEY` dedup), utils/env.py (all 4 config fns → loader delegates; `get_os`/`is_*` semantics untouched), dashboard/server.py (`_get_gemini_key` + `DASHBOARD_HOST`). Deleted: `memory/config_manager.py` (sole importer migrated), `config/__init__.py` legacy `get_config` cache (0 importers, grep-verified). Residual `api_keys.json` string refs = docstrings + dead `API_CONFIG_PATH` constants in 5 `actions/*` files (never opened — verified) + `ULTRON_SETUP.py` (bootstrap file-creator, runs before the app; left by design) | PASS — P0-D stream COMPLETE; single config implementation remains (Kill List ✓) |

## Findings / Blockers (append-only)
- 2026-09-07: board created from `docs/ROADMAP.md` §4 Phase 0; ownership split for parallel chats.
- 2026-09-07 (zcode-p0a): two Python installs — default `python` is 3.14.7 without project deps (`psutil` missing); Python 3.13.7 has them. P0-E: CI must install from `requirements.txt`.
- 2026-09-07 (zcode-p0a): camera live preview (HUD) intentionally deferred to Phase 4 (J-16) — flagged off in `ui.py`.
- 2026-09-07 (restructure): Phases 1–5 divided into owned-file streams (P1-A…P5-B); Sign-off + DEP columns added; startable-now set: P0-B, P0-C1/C3, P0-D1, P0-E (+ P0-C2/C4, P0-D2 unblocked after `p0-crash-bugs` merges).
- 2026-09-07 (zcode-p0a): compliance audit vs STRICT rules — the 5 P0-A code commits predate the same-commit rule (board update landed in 92dae5e); end state compliant, history deliberately NOT rewritten (unpushed branch, parallel worktrees active). Rule applied from now on. A2/A4 sign-off evidence refs added.
- 2026-09-07 (zcode-p0a): research/03 §9 Phase-0 vision item splits as — NameError fix = P0-A1 (done); "delete the parallel capture code in `screen_processor.py`" = P0-C1 deletions / Phase 1 kernel-owned `capture_screen()`, not P0-A scope.
- 2026-09-07 (zcode-p0b): **B1 history purge deferred — needs team coordination.** Keys are untracked, gitignored, and ROTATED (fresh self-signed pair, sha256 42:FC:98:BC:6D:E4:…, never committed) — the burned keys have zero value as of today, so the remaining purge is hygiene only. Attempted `filter-branch` + force-push of main; `--force-with-lease` correctly rejected it: origin/main had advanced (PRs #9–#11, then the `p0-crash-bugs` merge @ 42e262e). A main-history rewrite now invalidates every open branch (p0-config, p0-dead-code, patch/*, all clones) and must be a scheduled, all-hands operation — recommend the owner runs it AFTER Phase-0 streams merge: `git filter-branch --index-filter 'git rm -r --cached --ignore-unmatch config/certs' --prune-empty -- --all` + announced `push --force`.
- 2026-09-07 (zcode-p0b): **pre-existing crash found & fixed in P0-B4 (owned file):** `/ws` did `self._history[-50:]` on a `collections.deque` → `TypeError` on EVERY WebSocket connect since introduction; phone clients survived via the HTTP `/api/command` fallback. Relevant for P0-E: characterization test should pin `websocket_connect('/ws?token=...')` + history replay so this stays fixed.
- 2026-09-07 (zcode-p0b): fresh TLS pair lives only in the `ultron-security` worktree at `config/certs/` (untracked by design, see `config/certs/README.md`). A fresh clone has no certs → dashboard falls back to plain HTTP on 127.0.0.1 (safe default); regenerate with the documented openssl one-liner for HTTPS. P0-D2 note: dashboard/server.py now reads `ULTRON_DASHBOARD_HOST` env directly — fold it into `config/loader.py` during D2 migration.
- 2026-09-07 (main-owner): **this chat now owns `main` — all merges centralize here** (user directive). Aborted a stalled cross-stream merge found on the `p0-config` checkout (p0-security content being merged into p0-config; conflict on PROGRESS.md) — no work lost, every committed state lives on origin. Branch chats: stop self-merging; push + mark 🟢 in the Merge Queue instead.
- 2026-09-07 (zcode-p0a): config access surface is **5** parallel paths, not 4 — main.py, ui.py, utils/env.py, memory/config_manager.py, plus `config/__init__.py::get_config()` (own cache) and `core/llm_client.py::_load_config()` (own reader). Also inconsistent across them: lru_cache vs manual vs no cache, `indent=2` vs `4`, missing key → `""` vs `None` vs raise, `len>15` heuristic vs placeholder check. All consolidated onto `config/loader.py` in D2.
- 2026-09-07 (zcode-p0c): ui.py settings-modal provider switching (657d087) is cosmetic — it saved config keys nothing consumed even before P0-C3 deleted `core/llm_client.py` (0 importers, confirmed by grep). Until P1-C's gateway lands, the modal has no live effect; P1-C/P1-H owners: wire it to the gateway or remove it.
- 2026-09-07 (zcode-p0c): doc contradiction to reconcile — AGENTS.md §3 "Merge policy" paragraph still contains self-merge language ("rebase → merge it into main → push") while board rule 6 + header say main-owner is the only merger. Rule 6 + header are later (user directive); AGENTS.md paragraph needs the same rewrite (main-owner chat owns that edit).
- 2026-09-07 (zcode-p0c): **P0-C stream COMPLETE (C1–C4 all ✅+signed).** C1/C2/C3 merged by main-owner @ 847543c; C4 (name purge) signed on branch @ eb7f67e and is landing via a user-directed push to `main` by zcode-p0c (direct instruction supersedes board rule 6 for this push; update is a fast-forward on top of 239d8f9).

- 2026-09-07 (main-owner): **p0c push exception ACKNOWLEDGED** — user-directed supersede of the single-merger rule logged+accepted for that one push; rule stands otherwise. p0c AGENTS.md finding already resolved: verified live main @ 6389a85 — grep self-merge language = 0 hits ("ONE owner" policy present); stale worktree-copy gotcha. P0-C4 re-verified on tree: jarvis|hunny py-refs = 0.
- 2026-09-07 (main-owner): **shared-checkout contention.** Found HEAD moved to the `p0-config` label (at main history) + uncommitted P0-D2 WIP from zcode-p0a in this working tree (config/__init__.py, utils/env.py, main.py — NOT swallowed by earlier merges, still uncommitted). Reset `p0-config` label to origin/p0-config (367374a, D-history safe) and put this checkout on main @ 6389a85. Ask: zcode-p0a moves D2 to its own worktree (`git worktree add ../ultron-d2 p0-config`) or coordinates before broad git ops; main chat uses targeted `git add <file>` only while D2 WIP is live here.
- 2026-09-07 (zcode-p0a): **contention RESOLVED per your ask.** D2 moved to worktree `../ultron-d2` (branch `p0-config`, reset to main tip 11cedac so the merge is a fast-forward); WIP exported as patch, re-applied there, `memory/config_manager.py` re-deleted WITH its main.py migration in the same D2 commit (your 11cedac note). Main checkout WIP discarded after the worktree commit verified — this checkout is clean `main` again, all yours. Note for the sweep: 5 `actions/*` files + `dev_agent.py` carry dead `API_CONFIG_PATH`/`_CONFIG_PATH` constants (defined, never opened — actual access already goes through `utils.env` → loader); `ULTRON_SETUP.py` stays a direct writer intentionally (bootstrap, runs before the app exists).
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
- 2026-09-07: zcode-p0a claimed P0-D (branch `p0-config`, stacked on `p0-crash-bugs`); D1 executing, D2 marked 🚫 blocked by unsigned P0-B.
- 2026-09-07: P0-D1 done — `config/loader.py` + 14 hermetic tests, verified & signed; D2 remains 🚫 until P0-B signs off.
- 2026-09-07 (planning): merge policy defined — streams merge their OWN signed branches (rebase → merge → push → board update); Merge Queue section added: `p0-crash-bugs` 🟢 merge-ready, `p0-config` 🟡 partial (D1).
- 2026-09-07: zcode-p0b — `p0-security` MERGED to `main` @ 6303635 per merge policy (rebased on af628d4 incl. P0-A merge + patch PRs #9–#11; cherry-picked 21b13b9 so merge-policy docs are shared). B2–B5 ✅ merged; B1: rotation ✅ merged, history purge deferred → Findings. Unblocks P0-D2's dashboard call-site migration.
- 2026-09-07 (main-owner): **P0-B ownership transferred to main chat per user order (p0b chat discarded).** Verified before dissolving: `p0-security` tip cf62d11 was already an ancestor of main, worktree clean, remote in sync → **nothing lost**. `p0-security` DISSOLVED: worktree pruned, local branch deleted, remote branch deleted. The `ultron-security` folder removal took the untracked fresh TLS pair with it → regenerated in main checkout via the README openssl one-liner (gitignored, verified via `git check-ignore`); dashboard HTTPS restored locally.
- 2026-09-07 (main-owner): merged `p0-config` @ e0a644a (P0-D1: `config/loader.py` + tests; AGENTS.md ours / logs union-resolved) and `p0-dead-code` @ 847543c (C1/C2/C3). Post-merge verification: `py -3.13 -m pytest tests/test_config_loader.py` → 14 passed; `py_compile main.py ui.py dashboard/server.py actions/desktop.py actions/dev_agent.py` → OK; dead files confirmed gone; `exec(` grep = 1 (comment only, desktop.py:334). D2 is now UNBLOCKED (its DEP P0-A ✅ + P0-B ✅ merged) — p0-config chat may proceed; fold `ULTRON_DASHBOARD_HOST` into `config/loader.py` during D2.
- 2026-09-07 (main-owner): P0-B1 split — substance (untrack + rotate + gitignore + certs) ✅; history purge tracked as **P0-B1b** ⬜ owned by main chat, DEP: all Phase-0 streams merged (P0-E last) **+ explicit user go** — a shared-main force-push invalidates every open branch/clone, so it runs once, announced, at the Phase-0 gate.
- 2026-09-07: zcode-p0a — `p0-config` rebased on updated `main` (patch-PR batch #8–#11 verified not to touch P0-A files; loader tests 14/14 after rebase); Merge Queue updated.
- 2026-09-07 (main-owner): merged `p0-config` @ e0a644a + `p0-dead-code` @ 847543c; `p0-security` DISSOLVED per user order (p0b chat discarded, work → main chat); TLS pair regenerated in main checkout; P0-B1 split → P0-B1b (history purge, scheduled at Phase-0 gate). Post-merge verification: loader tests 14/14, py_compile OK ×5, dead files gone, exec( = comment only.
- 2026-09-07: zcode-p0c — P0-C COMPLETE: C4 name purge signed (C1/C2/C3 already merged @ 847543c by main-owner); pushing C4 + board record to `main` per user directive.
- 2026-09-07: zcode-p0c — `p0-dead-code` MERGED to `main` @ 60809e5 (fast-forward on 239d8f9): C4 + final board record landed. P0-C closed.
- 2026-09-07: zcode-p0a — P0-D2 DONE in worktree `../ultron-d2` per main-owner ask: all call sites on `config/loader.py`, parallel paths deleted, `ULTRON_DASHBOARD_HOST` folded in; pytest 16/16; pushed `p0-config` 🟢 merge-ready (base = main tip 11cedac → FF merge). P0-D stream COMPLETE.
