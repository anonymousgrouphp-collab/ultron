# 09 — K9 Repo Deep-Dive: What ULTRON Should Adopt

**Source:** https://github.com/parmarth-kumar/K9 — cloned to `C:/Users/ceoha/repos/K9`
(shallow, single commit `5b4bca2`, MIT license).
**Scope:** every meaningful file read line-by-line (~9k LOC core + all README/* design
docs + test suite + scratch experiments; `tests/experiments/code_v01.txt` is a 7.6k-line
dump of an older iteration, skipped).
**Verdict up front:** K9 is a well-documented solo-build personal assistant with
genuinely good *product* ideas (memory tiers, weather, search cascade, pronoun
resolution) wrapped in a *weaker engine* than ULTRON's. Its router is exactly the
"regex + LLM vote with magic thresholds" style ULTRON's Kill List forbids, and its
orchestrator/memory/barge-in are all a generation behind `kernel/`. But 6–8 concrete
mechanisms are directly adoptable and fill real ULTRON gaps.

---

## 1. What K9 is

Python 3.10+ assistant: prompt_toolkit "Iron Man HUD" TUI, Groq/OpenAI LLM,
STT (faster-whisper/vosk/SR), TTS (pyttsx3/gTTS/edge-tts/ElevenLabs), 4-tier memory
(entity → rolling buffer → JSON facts → FAISS episodic), Plan→Validate→Execute agent
pipeline over a regex/LLM hybrid router, persistent JSON TaskManager, priority EventBus,
9 skills (web_search cascade, weather, memory store/search, system_control, time,
generic, safe_response). Docs are unusually good (`README/*.md` are design docs with
mermaid diagrams, benchmark tables, and an external "brutal review" (.plan.md)).

Maturity: one commit, "initial release", debug scaffolding still in shipped code
(see §5), tests hit live Groq keys. Architecture docs > runtime hardening.

---

## 2. Feature-by-feature verdict vs current ULTRON

| K9 mechanism | ULTRON today | Verdict |
|---|---|---|
| Open-Meteo weather tool (geocoding + forecast + WMO codes + 3-min coord cache + country-bias + fallback) | `actions/weather_report.py` only opens a Google search URL in the browser | **ADOPT (P1)** |
| Search provider cascade Tavily→Serper→Exa→Brave→SerpAPI→DDG + comma-separated multi-key round-robin + 400-char snippet normalization | `actions/web_search.py` is DDG-only, no fallback, no cache | **ADOPT (P1)** |
| Factual cache with TTL (10 min news / 24 h facts) + dedup-by-query-+-time in memory | no search cache | **ADOPT (P1, same PR as above)** |
| Query-time recall ranking: `0.60·sim + 0.30·recency(exp(-age/72h)) + 0.10·importance`, MIN_SCORE gate 0.30, per-entry `max_age_hours` | `MemoryEngine.search` = RRF(BM25, vector) only; importance used only by offline decay | **ADOPT (P1)** → J-04/J-12 |
| Entity memory + pronoun resolution (name/type/gender/confidence, gender-matched rewrite, hard-stop clarification, ≤10 LRU, no-downgrade) | nothing (`grep pronoun|entity_memory` = empty) | **ADOPT (P2)** → J-12 |
| Anti-echo memory guards: skip-index on recalled content, assistant narrative-prefix blocklist, "I will remember that: X" payload re-index as user, tech-state importance floor 0.65 | `memory_formation.py` has no echo guards | **ADOPT (P2)** → J-04 |
| EventBus: bounded PriorityQueue(200), HIGH/NORMAL/LOW tiers, per-tier overflow policy (HIGH evicts LOW; NORMAL warn-drops; LOW silent), concurrent dispatch, drain cap 50/tick | `kernel/bus.py`: serial awaited delivery, no priority, no backpressure — slow subscriber blocks publisher | **ADOPT (P3 hardening)** |
| Route cache TTL 300s + `is_volatile` flag to skip caching; validated compound-query split (adopt "A and B" split only if ≥2 clauses produce valid plans) | AgentLoop re-plans every turn via native tool-calling | **ADOPT (P3, fast-path only)** |
| `requires_internet` tool tag + PlanValidator offline gate ("Privacy Violation" refusal UX) | policy/ + research_gate exist but no offline-mode gate concept | **IDEA (P3)** |
| Stress-test philosophy: planner degradation fuzzing (wrong_tool/none_tool/low_confidence/malformed_args), 100-req load rounds with wall-clock + "no silent under-routing" assertions | evals/ exist (routing policy 19/19, voice-product eval) but no malformed-tool-call fuzzing | **ADOPT (P3)** |
| TTS details: print-before-audio contract, persistent edge-tts event-loop thread, per-engine interrupt | 50 ms barge-in slicing already **better** | skip (borrow "print first" contract only) |
| FAISS/NumPy vector store w/ versioned snapshots + prune | SQLite + WAL + RRF + embedder seam (bge-m3) is better | skip |
| JSON TaskManager (idempotency keys, capped exp backoff, orphan recovery) | orchestrator lease-queue + checkpoints + heartbeats is better | skip (idempotency-key column = nice idea already implied) |
| prompt_toolkit HUD TUI | web dashboard is the chosen direction | skip |
| Groq/OpenAI clients w/ hardcoded model strings, bootstrap auto-pip, class-level entity state, live-API tests | violates Kill List #2/#3, hermetic-test doctrine | **DO NOT copy** (see §5) |

---

## 3. Adoptable items in detail

### P1-A — Real weather tool (J-08 feed + standalone win)
K9 `skills/weather.py`: geocode via `geocoding-api.open-meteo.com` (keyless),
forecast via `api.open-meteo.com` (keyless), WMO code → text table, intent-aware
answer ("humidity" → humidity line, "temperature" → temp line, else full summary),
coordinate cache `(round(lat,2), round(lon,2))` TTL 180 s, hard-mapped coords for
large countries (fixes "Delhi → Russia" geocode drift), profile country-bias, and a
direct-tool fallback (call web_search.execute, never re-enter the router).
**ULTRON port:** new tool in `kernel/tools` / action replacing the browser-open flow;
weather payload feeds the J-08 briefing pipeline. ~1 file + registry entry + tests.

### P1-B — Search cascade + key rotation + TTL cache (J-05 "web_search is shallow")
K9 `core/search_providers.py`: one `SearchProvider` ABC, uniform
`(success, markdown, llm_text, raw_items)` tuple, 8 s timeout, snippet normalization
(whitespace + 400-char cap), `is_configured()` gate, DDG as keyless last resort.
Key trick: `TAVILY_API_KEYS=k1,k2,k3` + `itertools.cycle` = free rate-limit headroom.
Factual cache: normalized query → `{answer, ts}` with TTL 600 s (news regex) / 86400 s.
**ULTRON port:** `actions/web_search.py` provider list + cache module; also usable by
`kernel/research` runner as its fetch leg. Keep DDG as the no-key default (offline-friendly).

### P1-C — Recency-aware recall re-scoring (J-04/J-12)
K9 `recall_engine.py`: after ANN top-k, re-score with 60/30/10 sim/recency/importance,
3-day half-life (`exp(-age_h/72)`), hard 0.30 gate, per-entry `max_age_hours` TTL.
**ULTRON port:** in `kernel/memory/engine.py::search`, re-rank the RRF top-N
(e.g. N=32) using `fact["known_at"]` + stored importance; add optional per-fact
`expires_at` for ephemeral facts. Small, testable, hermetic.

### P2-A — Entity memory + pronoun resolution (J-12, the "who is he?" JARVIS feel)
K9 chain: web_search LLM contract returns strict JSON
`{answer, primary_entity, entity_type, gender}` → Brain extracts → entity store
(type+gender+confidence, ≤10, eviction) → on next turn, pronoun detected → gender/type
matched rewrite → unresolved ⇒ hard-stop "Who are you referring to?" (never hallucinate
a referent). Guards: confidence ≥ 0.7, no confidence downgrades.
**ULTRON port:** session-scoped (NOT class-level like K9 — see §5) store in the loop
state or per-user memory; entities extracted from tool results via the existing
gateway JSON contract; rewrite pass before the AgentLoop turn. This is the single
biggest *perceived-intelligence* win K9 offers.

### P2-B — Anti-echo guards on memory formation (J-04 quality)
Four portable guards from `episodic_index.py` / `brain.py`:
1. never index text that was just *recalled* (skip-index for memory_search results —
   prevents autobiographical echo loops);
2. blocklist assistant narrative prefixes ("based on", "from what i recall",
   "you mentioned", …) from episodic indexing;
3. when the assistant echoes a stored fact ("I will remember that: X"), index the
   *payload* as a user-intent memory, not the wrapper;
4. first-person present-tech-state sentences ("I am debugging X") get an importance
   floor (0.65) so short high-value updates are never dropped by length penalties.

### P3 — Hardening set
- **Bus backpressure** (K9 `event_bus.py`): bounded queue + priority tiers +
  per-tier overflow policy + concurrent handler dispatch + drain cap. ULTRON's bus
  currently awaits every subscriber serially inside publish — one slow subscriber
  stalls the publisher; proactive engine (J-09/J-19) will need the bounded semantics.
- **Route/response cache** with TTL and `is_volatile=True` on weather/search tools.
- **Validated compound split** for the voice fast-path: split on " and " only if
  every clause independently yields a valid plan (their anti-"salt and pepper" test).
- **`requires_internet` tool tag** + gate in policy layer for a future offline mode.
- **Planner fuzz evals:** malformed tool-call fixtures (wrong tool name / none /
  low confidence / malformed args) asserting no crash + no raw JSON leakage; load
  rounds asserting "never silent" (0 responses is a failure, not a pass).

---

## 4. Validations (K9 independently confirms ULTRON choices)

- Their external review (`.plan.md`): "infrastructure solid, engine without mission" —
  same conclusion ULTRON's ROADMAP reached; the fix they propose (goal loop, eval-driven
  self-improvement) is what orchestrator + evals already build.
- Two-stage wake word (local sentinel → full STT only after wake) = K9's `wake_word_integration.md`
  recommends OpenWakeWord — matches ULTRON's parked openwakeword cutover plan.
- Offline gatekeeper = factory-selected local engines (Ollama/Kokoro/Whisper) — matches
  ULTRON's Ollama gateway + voice-stack flag gating.
- Their model benchmark doc (GROK.md): small models degrade on strict JSON under load —
  matches ULTRON's provider_test + routing-policy eval approach (test models, don't guess).
- "Don't mix two agent layers" (rejected Groq Compound) = Kill List #2's one-implementation rule.

---

## 5. Bugs & anti-patterns found while reading (lessons, not imports)

1. **Shipped debug writes:** `vector_store.py::add_memory` opens and appends to
   `verify_debug.txt` on *every memory add* (sync I/O on the hot path, unbounded file).
   Lesson: debug scaffolding needs a kill switch before "release".
2. **Class-level mutable state:** `Router._entity_memory` is shared by every Brain
   instance/thread; their own test suite needed an `IsolatedMemory` contextmanager
   (their "FIX 6") to avoid cross-test poisoning. ULTRON equivalent must be
   session/user-scoped.
3. **Non-reentrant lock deadlock (latent):** `JSONMemory._ensure_current_day_loaded()`
   calls `self.save()` while callers already hold `self._lock`, and `save()` re-acquires
   the same `threading.Lock` → deadlock on the first append after midnight rollover.
   (Works only because rollover mid-process is rare.) Lesson: document lock contracts;
   prefer `RLock` or restructure.
4. **A thread per indexed message** in EpisodicIndex (unbounded spawn under load; a
   pool/queue is the correct shape).
5. **Unbounded caches:** factual cache dict + JSON grows forever; entity memory is
   capped (10) but the routing-log/search-log JSONs are not.
6. **Hardcoded model strings** (`llama-3.1-8b-instant`, `gpt-4o-mini`) inside clients —
   Kill List #3 violation if copied; ULTRON keeps model choice in gateway config.
7. **Bootstrap auto-pip at import time** (`bootstrap.py` installed packages implicitly,
   `DEV_MODE` gating) — non-hermetic startup; ULTRON's pinned requirements + boot gate
   is the right model.
8. **Tests require live API keys** (test_suite drives real Groq) — hermetic tests
   (ULTRON's doctrine) keep the suite green without keys/network.
9. **PlanValidator is security theater:** only checks tool-exists, category≠internal,
   args-is-dict. Contrast ULTRON's consent layer + SSRF guard + injection defenses —
   K9 has no equivalent. Nothing security-shaped should be borrowed from K9.

---

## 6. Suggested ULTRON workstream mapping (for PROGRESS.md)

| Item | Suggested stream | Size | J-ID |
|---|---|---|---|
| Weather tool (Open-Meteo) | tools/voice-product | S | J-08 |
| Search cascade + cache | tools/research | M | J-05 |
| Recency recall re-scoring | memory | S | J-04, J-12 |
| Entity/pronoun layer | loop/voice UX | M | J-12 |
| Anti-echo memory guards | memory | S | J-04 |
| Bus backpressure | kernel core | M | infra (J-09/J-19) |
| Route cache + is_volatile | loop | S | perf |
| Fuzz/load evals | evals | M | J-24 |
| requires_internet gate | policy | S | infra |

---

*Analysis-only deliverable (like the MiniMind pass): nothing adopted yet; no K9 code
was copied. K9 remains at `C:/Users/ceoha/repos/K9` for reference during implementation.*

---

## 7. IMPLEMENTATION STATUS (2026-09-13, later same day)

User authorized full implementation of the adopt list. Landed, with tests
(all hermetic, py -3.14):

| Item | Files | Tests |
|---|---|---|
| §P1-C recency re-ranking + per-fact TTL | `kernel/memory/engine.py` (search: RRF order preserved at depth-k, re-ranked by 0.60·sim + 0.30·recency(72h half-life) + 0.10·importance; `expires_at` now enforced at read time; gate 0.15) | `tests/test_memory_recall_rescoring.py` (5) |
| §P1-A spoken weather | `utils/weather.py` (geocode + current + WMO + 3-min coord cache + hard-mapped countries), `actions/weather_report.py` (spoken path + legacy browser fallback), `core/tool_declarations.py` (description) | `tests/test_weather_tool.py` (12) |
| §P1-B search cascade + factual cache | `utils/search_providers.py` (Tavily→Serper→Exa→Brave→SerpAPI cascade, config+env multi-key round-robin, bounded JSON TTL cache 10min/24h), `actions/web_search.py` (`_cascade_query`: cache→Gemini→cascade→DDG; search/research/price wired) | `tests/test_search_cascade.py` (15) |
| §P2-B anti-echo guards | `kernel/memory/guards.py` (echo-narrative blocklist, store-prefix stripper, tech-state floor 0.65), wired into `_handle_save_memory` (`app/handlers.py`) + Consolidator ADD ops (`kernel/memory/consolidation.py`) | `tests/test_memory_guards.py` (21) |
| §P2-A entity memory | `kernel/memory/entities.py` (session-scoped EntityStore, no-downgrade + LRU cap, deterministic extraction, no-guess pronoun resolver, cross-session persistence with dedup), wired into `generate_session_summary` | `tests/test_entity_memory.py` (13) |
| §P3 bus timeout guard | `kernel/bus.py` (per-subscriber `handler_timeout_s` 10s default, None = legacy unbounded) | `tests/test_bus_hardening.py` (4) |
| §P3 loop fuzz + never-silent | — | `tests/test_loop_fuzz.py` (6) |

**Design lesson recorded mid-implementation:** the first re-rank draft widened the
retrieval legs to k·4 for "re-rank headroom" — that silently changed RRF fusion
outcomes (single-leg exact matches got diluted) and broke a pinned eval
(`test_missing_and_forbid_verifiers_fire`). Final design keeps both legs at depth-k
(fusion untouched) and re-ranks the fused pool only — eval green again. If a
deeper re-rank pool is ever wanted, it must be an explicit, eval-gated change.

Not implemented (deferred, unchanged verdicts): route cache + `is_volatile`
(needs a turn-seam decision in the Live architecture), `requires_internet`
offline gate (policy-layer decision), validated compound split (Live session
handles multi-tool turns natively).
