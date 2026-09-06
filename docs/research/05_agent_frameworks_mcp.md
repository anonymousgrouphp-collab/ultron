# 05 — Agent Frameworks, MCP & Orchestration Research

*Research doc 5 of 8, companion to `01_jarvis_feature_catalog.md`. Covers **J-05** (agentic
web research), **J-11** (coding subagent), **J-21/J-22** (subagent fleet / house-party
protocol), **J-24** (self-improvement loop), and Roadmap Phases 1–2 (kernel agent loop,
MCP tool bus, task orchestrator). Written 2026-09-07; sources verified Sep 2026.*

**ULTRON context:** the roadmap commits to *building a thin kernel agent loop ourselves*
(plan→act→observe, structured ToolResult, policy engine) and *adopting MCP as the tool
protocol in both directions*. This doc maps the 2026 landscape so we adopt the right
pieces instead of rebuilding what's commodity, and don't adopt what would fight the kernel.

---

## 1. The state of play in September 2026 (executive summary)

- **MCP won.** The official MCP Registry (preview Sep 2025) counted **~9,650 live servers
  (May 2026 pull, ~29k version records)**. FastMCP is the de-facto Python framework for
  both servers and clients. Streamable HTTP replaced the old HTTP+SSE transport.
  → Our "adopt MCP both directions" bet has paid off *before we even build it*: the
  ecosystem did the tool-distribution work for us.
- **Agent frameworks consolidated.** AutoGen + Semantic Kernel merged into
  **Microsoft Agent Framework** (1.0 shipped April 3 2026, production, Python + .NET).
  LangGraph is the production-grade leader for stateful control. OpenAI Agents SDK and
  PydanticAI are the lightweight layers. CrewAI owns role-based prototyping.
- **The "framework vs kernel" verdict:** every serious 2026 comparison lands on the same
  conclusion — heavy frameworks are great for demos, painful to debug at the edges, and
  all of them eventually get escaped via raw loops + tracing when reliability matters.
  For ULTRON (a desktop kernel with voice, policy, and memory as first-class citizens),
  **a thin owned loop + MCP + selective library adoption** is correct. We adopt
  *patterns* (graph state, checkpointing, handoffs) more than *runtimes*.

---

## 2. MCP — the tool protocol (Roadmap Bet #1)

### 2.1 Protocol status

| Item | State (Sep 2026) | Note |
|---|---|---|
| Spec | MCP spec, streamable HTTP + stdio transports | HTTP+SSE retired (2025-03-26 spec) |
| Python SDK | `mcp` (official, modelcontextprotocol/python-sdk) | Low-level; fine primitives |
| Framework | **FastMCP** (gofastmcp.com) | "The framework for MCP" — servers, clients, proxying, composition, auth. The pragmatic choice |
| Registry | Official MCP Registry (registry.modelcontextprotocol.io) + directories: Glama, PulseMCP, mcp.so | Programmatic server discovery — the kernel can *search* for tools |
| Reference servers | [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) | filesystem, git, fetch, memory, etc. |
| Security | Registry hosts metadata only, **no security scanning of server code** | Prompt-injection / tool-poisoning is a real 2026 research topic (DSN 2026 paper on MCP). Our policy engine must treat every external MCP tool as untrusted input |

### 2.2 What this means for ULTRON (concrete)

1. **Serve our tools:** wrap the 19 actions + kernel tools as an MCP server with
   FastMCP (`@mcp.tool` decorators ≈ our auto-registering decorator, near-zero extra
   code). This gives phone/dashboard/any-MCP-client access to ULTRON for free.
2. **Consume the ecosystem:** FastMCP's *client* can mount remote servers into our Tool
   Bus. First three to ship with the kernel: **filesystem** (self-hosted, we control
   scope), **fetch/fetch-mcp** (J-05 research), and **GitHub** (J-11 coding flows).
3. **Trust boundary:** every MCP server's tool descriptions are *prompt-injection
   surface*. Rule for the kernel: MCP tool output goes through the same
   structured-ToolResult sanitization as local tools, and destructive MCP calls need the
   same consent gate. Discovery from the registry is a human-approved, allowlisted
   operation — never autonomous install.

**Top picks:** FastMCP (server+client, Apache-2.0) · official `mcp` SDK as fallback ·
registry + Glama for discovery.

---

## 3. Agent frameworks — comparison & verdict

| Framework | What it is (Sep 2026) | Stars* | License | Strengths | Why not as ULTRON's core |
|---|---|---|---|---|---|
| **LangGraph** (langchain-ai/langgraph) | Graph-of-state agent runtime; checkpointer infra; leading prod choice (~27k monthly searches vs CrewAI 14.8k) | ~20k | MIT | Best-in-class checkpointing/durability, human-in-the-loop interrupts, streaming, subgraphs | Heavy conceptual surface; pulls LangChain orbit; we only need its *patterns* (state channels, checkpointers) |
| **Microsoft Agent Framework** (microsoft/agent-framework) | AutoGen + Semantic Kernel unified; **1.0 GA Apr 3 2026**, Python + .NET, production LTS | ~10k+ | MIT | Workflows, multi-agent orchestration, Azure-first, finally "production ready" | Young API surface post-merge; Azure gravity; Python side still maturing; watchlist |
| **CrewAI** (crewAIInc/crewAI) | Role-based crews, tasks, tools | ~35k | MIT | Fastest way to prototype multi-role fleets | Abstractions fight custom kernel control; performance overhead; fleet logic (J-21) is 200 lines of our own orchestrator |
| **OpenAI Agents SDK** (openai/openai-agents-python) | Lightweight loop: agents, handoffs, guardrails, tracing; MCP client built-in | ~12k | MIT | Clean primitives; handoffs ≈ our subagent handoff; good tracing schema | OpenAI-model-first tracing/UX; still a fine *reference* for our loop design |
| **PydanticAI** (pydantic/pydantic-ai) | Type-safe agent layer from the Pydantic team; **native Temporal durable execution**; structured output is first-class | ~12k | MIT | Best structured-output ergonomics (matches our ToolResult philosophy); provider-agnostic; durable-exec integration | It *is* close to our model-gateway + loop layer — strongest "adopt" candidate if we ever don't want to own the loop |
| **smolagents** (huggingface/smolagents) | HF's barebones agents; **CodeAgent** (writes Python actions instead of JSON calls) + ToolCallingAgent | ~24k | Apache-2.0 | Code-as-action is genuinely more expressive for long tool chains; tiny | Code-exec agent on a desktop with real user files = sandbox load; we already have a coding subagent path (J-11) |
| **OpenHands** (All-Hands-AI/OpenHands) | The most-starred OSS coding agent; full dev environment, sandbox, browser | ~60k+ | MIT | Proved plan→act→observe + sandbox + evals at scale (roadmap reference class) | It's an *application*, not a library; we steal its loop/verifier patterns for J-11 |
| **AutoGen (legacy)** | Frozen; migrated into MS Agent Framework | — | — | — | Don't start anything here |

\* Stars approximate as of Sep 2026; verify at build time.

### Verdict for ULTRON

- **Own the loop** (roadmap Phase 1 stands). Steal: LangGraph's *state-channel +
  checkpointer* design, OpenAI Agents SDK's *handoff/guardrail* vocabulary, smolagents'
  *code-action* idea (only inside the sandboxed coding subagent, where the sandbox
  exists anyway).
- **Adopt**: MCP via FastMCP (both directions) — non-negotiable, it's the ecosystem rail.
- **Watchlist**: Microsoft Agent Framework 1.x (re-evaluate after two more quarters of
  Python-side maturity), PydanticAI as a possible gateway implementation detail.
- **Never**: marry a framework's runtime so deeply that voice/policy/memory have to
  bypass it. That's the god-module sin again, one level up.

---

## 4. Browser automation for agents (J-05, J-07 web half)

| Project | Approach (2026) | Stars* | License | Take |
|---|---|---|---|---|
| **Playwright** (microsoft/playwright) | Deterministic browser automation, Python API | ~75k | Apache-2.0 | The substrate. Our existing `browser_control.py` should sit on Playwright, not raw Selenium/keys |
| **browser-use** (browser-use/browser-use) | LLM agent over Playwright, DOM+vision hybrid | ~75k+ | MIT | Most popular Python agent layer; dev ergonomics praised; community reports hallucinated clicks/slow runs on hard sites — fine for cooperative sites, not a guarantee |
| **Stagehand** (browserbase/stagehand) | `act / extract / observe` primitives on Playwright; caches actions to cut repeat costs | ~15k | MIT (TS; Python SDK 2026) | Best cost profile for repeated flows; TS-first (Python SDK now exists) |
| **Skyvern** (Skyvern-AI/skyvern) | Vision-first (reads pages as pixels), no-code workflows | ~12k+ | AGPL-3.0 | AGPL is a license consideration; vision-first suits hostile pages; heavier |

**Pick:** Playwright as substrate + **browser-use** as the J-05 research/reading agent
(MIT, Python, zero friction) with the kernel's consent gate on logins/payments.
Stagehand's action-caching is the pattern to steal for recurring flows ("open YouTube
and play X" should get cheaper every time). Keep Skyvern on the shelf unless anti-bot
pages become the bottleneck.

---

## 5. Orchestration: task queue, durability, subagents (J-21/J-22, Phase 2)

The roadmap's Phase-2 gate: *"research X and write a report to Desktop" as a background
job with checkpoints, surviving a kernel restart, using ≥3 tools incl. one external MCP
server."* What exists to build on:

| Option | Model | Infrastructure | Fit for a Windows desktop kernel |
|---|---|---|---|
| **Temporal** | Full durable workflow engine, automatic state replay | Requires Temporal server (+ DB) | Overkill to install per-desktop; the gold standard we're borrowing semantics from |
| **DBOS** (dbos-inc/dbos-transact-py) | Durable execution via decorator, checkpoints to Postgres | Postgres only, no separate server | Nice design; still wants a DB server. Watch: SQLite backend |
| **Restate / Hatchet / Inngest** | Durable workflows / queues | Own server/cloud | Same objection as Temporal |
| **arq** (python-arq/arq) | asyncio job queue on Redis | Redis | One more daemon on a desktop; only if we already want Redis |
| **APScheduler** | In-process cron/scheduler | None | Already close to ULTRON's reminder needs; no durability |
| **SQLite-backed queue (own, ~300 lines)** | Jobs table + lease/heartbeat + checkpoint blobs + WAL | None — SQLite is already our memory backbone | **The pick.** Desktop-friendly, zero new daemons, survives restarts, auditable in the same DB as memory. This is the roadmap's "durable task queue" and it is genuinely small |

**Subagent fleet (J-21/J-22):** the 2026-proven pattern is a parent agent spawning scoped
children with: own context window, restricted tool subset (policy engine decides),
step budget, and a structured result contract; results aggregate through the event bus.
Queue-backed jobs make the "House Party Protocol" a fan-out of N queued jobs, not a
blocker of the voice loop. Background jobs must report progress as events the HUD
subscribes to — the fleet dashboard is then just an event-log view.

---

## 6. Coding subagent (J-11)

State of the art to learn from (Sep 2026):

- **Terminal-Bench 2.1 leaders:** Codex CLI ~89.5% (GPT-5.6 Sol), Claude Code ~89.1% —
  both closed-loop terminal agents with sandboxing and checkpoint/rollback.
- **Open source:** OpenHands (most-starred), OpenCode, Cline, Roo Code, Aider —
  Aider remains the token-efficiency champion (~4× fewer tokens than Claude Code in
  community tests) thanks to tight repo-map diffs.
- **Lessons that transfer to our kernel:**
  1. Plan→write→run→observe→fix loops with a **verifier** (tests/lint/exit codes) beat
     clever prompting.
  2. **Rollback is a feature**: git snapshot (or shadow copy) before every mutation,
     one-key restore — this is our consent/dry-run story for code.
  3. Repo-map context selection (Aider) keeps long sessions cheap.
  4. Sandboxed execution first (see `03_computer_use_vision.md` sandbox ladder), ambient
     `pip install` never (roadmap kill-list already bans it).

**Build:** coding subagent = kernel loop + tools (read/write/patch, shell-in-sandbox,
git, test-runner) + MCP GitHub server. ~1k lines on top of Phase-2 primitives.

---

## 7. Evals for the agent loop (J-24, Phase 1/5 CI gate)

| Tool | What it is | License | Fit |
|---|---|---|---|
| **DeepEval** (confident-ai/deepeval) | pytest-native LLM/agent evals; `deepeval inspect` gives per-span trace scores | Apache-2.0 | **Primary pick** — matches our pytest-CI doctrine; agent-trace scoring is built in |
| **promptfoo** (promptfoo/promptfoo) | Config-driven evals + red-teaming; many assertions need no judge LLM | MIT | Best for persona/style regression (J-03) and security red-team of tool surfaces |
| **Inspect AI** (UK AISI, inspect-ai) | Research-grade eval framework, solvers/scorers used by safety institutes | MIT | When we outgrow pytest idioms; heavy for now |
| Benchmarks | OSWorld / Windows Agent Arena (computer use — see doc 03), SWE-bench-Verified / Terminal-Bench (coding), LOCOMO / LongMemEval (memory — see doc 04) | — | Calibrate our private 50-task suite against these quarterly |

**Design for ULTRON:** scripted verifiers wherever a task has a checkable end-state
(file exists, window closed, answer string matches); LLM-judge (rubric) for persona and
research quality; every eval records the full event-bus trace so a failure is
replayable. CI gate: 50-task success rate may not drop (roadmap NSM).

---

## 8. Integration notes for ULTRON (build order)

1. **Phase 1:** FastMCP wraps existing tools (server side) — mechanical, unblocks the
   dashboard/phone as MCP clients. Kernel agent loop v0 stays text-mode, structured
   ToolResult, policy-gated.
2. **Phase 2:** FastMCP client mounts filesystem/fetch/GitHub servers; SQLite durable
   queue + checkpoint blobs + lease/heartbeat lands with the orchestrator; subagent
   spawning = queue jobs with scoped tool lists; browser-use wired behind consent for
   J-05.
3. **Phase 5:** DeepEval suites in CI (50-task + recall + persona), promptfoo red-team
   for prompt-injection over MCP tool results.
4. **Standing rules:** no framework runtime in the kernel path; no MCP server install
   without human allowlisting; every external tool result sanitized as untrusted input;
   one name, one protocol, one loop.

*Cross-refs: `02_voice_stack.md` (voice front-end), `03_computer_use_vision.md`
(sandbox ladder, UIA), `04_memory_systems.md` (procedural memory → skills), `06_local_models.md`
(model gateway back-ends), `08_integration_automation.md` (what MCP servers to consume next).*
