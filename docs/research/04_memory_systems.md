# 04 — Memory Systems Research

*Research doc 4 of 8, companion to `01_jarvis_feature_catalog.md`. Covers **J-04**
(total recall across sessions), **J-12** (continuity, session summaries), **J-25**
(learned patterns/preferences), **J-24** (procedural memory — failures become skills),
and Roadmap Phase 3. Written 2026-09-07; sources verified Sep 2026.*

**ULTRON context:** memory today is one JSON file dumped whole into the prompt, capped
at 2,200 characters, no embeddings, no retrieval. The roadmap calls memory *the moat*
— "retrieval quality and consolidation quality are the product." This doc picks the
architecture.

---

## 1. Memory frameworks (adopt patterns / libraries, not platforms)

| Framework | Model (Sep 2026) | Stars* | License | Take for ULTRON |
|---|---|---|---|---|
| **mem0** (mem0ai/mem0) | Memory **layer**: extract→store→retrieve pipeline bolted onto any agent; OpenMemory MCP server; graph memory option | ~40k | Apache-2.0 | The pipeline to imitate (extraction + consolidation prompts). Scored ~49% in a 2026 multi-framework benchmark — good, not magical. Usable as a *library* if we outsource phase 3; our verdict below: steal patterns, own the store |
| **Letta** (letta-ai/letta) | Full **agent framework** (MemGPT lineage: context = RAM, memory = disk, self-editing memory); sleep-time compute; **"Memory Models" (Jun 2026)** — memory-native RL for agents that learn; "Context Constitution" (Apr 2026) | ~40k | Apache-2.0 | Deepest research lineage. But it wants to BE the agent runtime — conflicts with our kernel. Adopt concepts: main/external context paging, sleep-time consolidation |
| **Zep / Graphiti** (getzep/graphiti) | Temporal **knowledge graph** (bi-temporal: facts valid-at vs known-at); community edition self-hosted | ~20k (graphiti) | Apache-2.0 | Best-in-class temporal reasoning ("moved apartments in March"), but needs Neo4j — ops-heavy for one desktop. Revisit if recall evals fail without it |
| **LangMem** (langchain-ai/langmem) | Memory SDK from LangChain (extraction, update strategies) | ~2k | MIT | Good reference code for write/update policies |
| **cognee / Memobase / others** | Graph+vector memory kits, user-profile memory | growing | varied | Nothing we can't build thinner |

**Verdict:** ULTRON owns the store (SQLite) and the policy; borrows mem0's pipeline
prompts, Letta's paging/consolidation concepts, Graphiti's bi-temporal *schema idea*
(valid_from/known_at columns cost nothing today, enable KG later). A platform would
make the moat someone else's product.

## 2. Vector store (local Windows desktop, zero Docker)

| Store | Shape | Maturity | Verdict |
|---|---|---|---|
| **sqlite-vec** (asg017/sqlite-vec) | SQLite extension, brute-force + quantized; single file | Actively developed; successor of sqlite-vss | **Pick.** Same file as our memory DB; WAL; zero servers; perfectly sized for 10k–1M vectors |
| **LanceDB** (lancedb/lancedb) | Embedded columnar vector DB, fast ANN, SQL-ish | Strong, growing | Upgrade path if recall volume outgrows brute-force (100k+ chunks) |
| Chroma | Embedded/server, Pythonic | Popular but heavier; persistence friction | Skip (extra process/dep for no gain here) |
| Qdrant | Server (even in local mode) | Excellent at scale | Overkill on a desktop |
| FAISS | Library | Battle-hardened | Manual persistence/index mgmt; skip |

## 3. Retrieval = hybrid, not vector-only

Names, dates, commands ("which site did I say for the flight deal?") fail pure
embedding search. Design:

1. **SQLite FTS5** (BM25) over the same rows — literal keyword recall.
2. **sqlite-vec** cosine over embeddings — semantic recall.
3. **Reciprocal Rank Fusion** (k=60) merges both lists — 3 lines of code, big wins.
4. **Rerank** top-50→top-8 with **bge-reranker-v2-m3** (CPU-viable) for the final
   context assembly.
5. Score = `w_r·relevance + w_t·recency(half-life) + w_i·importance` — the
   Generative Agents formula (Park et al. 2023), proven and cheap.

## 4. Embeddings

| Model | Dim | Why |
|---|---|---|
| **BGE-M3** (BAAI) | 1024 | Multilingual (J-13 users), 8k ctx, strong retrieval default — via Ollama/sentence-transformers |
| **nomic-embed-text** | 768 | Fast, long-context, Ollama-native |
| **all-MiniLM-L6-v2** | 384 | CPU-fast when Tier-N hardware (doc 06) |
| API: OpenAI `text-embedding-3-*`, Gemini, Voyage | — | Fine, but local keeps memory private by default (and free at our scale) |

## 5. Knowledge graph for ONE user — honest take

Graphiti/Zep's temporal KG shines at multi-entity reasoning over *many* users/sources.
For one person: start with **typed tables + metadata columns** (`entity`, `topic`,
`valid_from`, `known_at`) — that's 80% of the value at 5% of the ops. Promote to a KG
only if the recall eval (§8) shows failures KG-shaped (multi-hop across people/places).

## 6. The design patterns that matter (from the papers)

1. **MemGPT (2023, now Letta):** context as paged memory — working set in prompt,
   everything else behind tool-calls ("search_memory", "archive"). ULTRON's kernel
   exposes exactly two memory tools; the LLM pages in what it needs.
2. **Generative Agents (2023):** memory stream + recency×importance×relevance scoring
   + **reflection** jobs that synthesize observations ("you've been skipping workouts
   for 3 weeks") into higher-level memories.
3. **Mem0 (2024-25, LOCOMO benchmark):** the extract→consolidate pipeline: after each
   session, an LLM job proposes new memories *and updates/dedupes* old ones (ADD /
   UPDATE / DELETE / NOOP ops). This is our consolidation job.
4. **Sleep-time compute (Letta, 2025-26):** consolidation runs when the machine is
   idle — Windows Task Scheduler idle trigger + kernel job, not a live-taxed thread.
5. **Write policy (the hard part):** not everything deserves memory. The kernel's
   write path: candidate → LLM judges (importance 0–10 + justification + dedupe check)
   → commit. Captures J-04 without a noise graveyard. User-visible ("what do you
   remember about me?") and editable — memory is a product, give it a UI.
6. **Forgetting:** exponential decay on score, hard-expiry for classes (whereabouts),
   tombstones not deletes (audit + undo).

## 7. Procedural memory (J-24) — failures become skills

- **Voyager** (Minecraft): successful programs saved to a skill library, retrieved by
  embedding on similar future tasks — the canonical pattern.
- **Agent-S** keeps trajectory memory; **OpenAdapt** records GUI demos for replay.
- ULTRON design: every completed multi-step job stores its **tool-call script + final
  verifier + failure notes** as a `procedure` row (embedded summary). The agent loop
  retrieves "similar past run" before planning. Sandbox+consent rules apply to replay,
  and every replay is a fresh eval-trace (doc 05) — skills that regress get pruned.

## 8. Long context vs retrieval (2026 honesty)

Million-token contexts exist and are fine *within* a session. For a personal
assistant, retrieval still wins because: (a) cost & latency of stuffing months of
history every turn, (b) cross-session **persistence** independent of any provider's
window, (c) **structured recall** (counts, dates, lists need grounded lookup, not
attention), (d) privacy scoping. Rule: working set in-context, everything else behind
the two memory tools.

## 9. The recall eval (Phase 3 gate, roadmap: 40 questions >80%)

Build it like the benchmarks (**LOCOMO**, **LongMemEval**):
- Question types: single-hop fact · temporal ("what did I say in March?") · multi-hop
  across sessions · negation/absence ("did I ever...?") · counts/lists.
- Seed corpus: scripted sessions + the migration of real `long_term.json`.
- Verifiers: scripted (answer-string/SQL-checkable) + LLM-judge with rubric; every
  question records retrieved chunks → misses are diagnosable.
- CI: runs on PRs touching memory; score may not drop (doc 05 eval stack: DeepEval).

## 10. Recommended architecture (the moat, concretely)

```
SQLite (WAL) ── tables: episodes · semantic_facts · procedures · people · preferences
   ├─ columns: entity, topic, importance, valid_from, known_at, expires_at, source_ref
   ├─ FTS5 index (keyword)  ─┐
   └─ sqlite-vec index (BGE-M3 / nomic) ─┴─ RRF fusion → bge-reranker → top-8
Write path: session-end extraction job (mem0-style ADD/UPDATE/DELETE) + importance gate
Consolidation: idle-time job (decay, dedupe, reflection syntheses)
Access: kernel tools memory_search / memory_page (+ "what do you remember" UI later)
Migration: long_term.json → semantic_facts once, scripted, verified
```

Build-vs-adopt per layer: **adopt** sqlite-vec, FTS5, BGE-M3, bge-reranker, mem0's
pipeline prompts; **build** schema, write policy, consolidation, evals (this IS the
moat); **defer** KG, Letta runtime, Zep/Neo4j.

*Cross-refs: `02_voice_stack.md` (speaker ID → per-person memory rows) ·
`05_agent_frameworks_mcp.md` (eval stack, memory tools in the loop) ·
`06_local_models.md` (local embeddings tier table) · `08_integration_automation.md`
(briefing = memory highlights + calendar).*
