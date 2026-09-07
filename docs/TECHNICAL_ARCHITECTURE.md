# ULTRON Technical Architecture and Stack

## Decision summary

ULTRON is a local-first Windows desktop application with a thin Python kernel.
The kernel is the only seam allowed to execute a Tool. UI, voice, dashboard,
model adapters, and future MCP clients are clients of that seam; none may call an
`actions/` function directly.

The current repository is in transition. `kernel/legacy.py` is a temporary P1-F
adapter that makes the legacy action catalogue pass through the real registry,
policy engine, event bus, and SQLite audit log. It is not a second framework and
must shrink as native tools replace legacy handlers.

## Runtime shape

```text
voice / text / local dashboard
              |
              v
       modality adapter
              |
              v
    model gateway (P1-C)
              |
              v
       agent loop (P1-G)
              |
              v
 PolicyEngine -> ToolRegistry -> native Tool / temporary legacy adapter
       |              |                    |
       v              v                    v
   SQLite audit     EventBus             result/artifacts
```

Every arrow that changes machine or account state must first cross
`PolicyEngine`. The model is allowed to propose a Tool call; it is never allowed
to authorize one.

## Chosen stack

| Concern | Release 1 choice | Status / rule |
|---|---|---|
| Runtime | CPython 3.13, `.venv`, Windows 11 | Supported baseline; launchers must select it explicitly. |
| Desktop UI | PyQt6 + Qt WebEngine | Keep as a modality adapter; it owns user consent presentation. |
| Voice | `sounddevice` + Google GenAI Live adapter | Existing path; refactor behind P1-C gateway and P1-H client seam. |
| Model gateway | `google-genai`; Ollama adapter in P1-C | Remove `google-generativeai`; model identifiers belong only here. |
| Kernel | stdlib `asyncio`, typed dataclasses, EventBus, ToolRegistry | Existing P1-A/B foundation. No agent framework runtime. |
| Safety | PolicyEngine + SQLite `AuditLog` | Existing P1-E foundation. Default: read allow, write/execute ask, destructive deny. |
| Persistence | SQLite WAL | Audit first; memory follows P1-D. Runtime databases are gitignored. |
| Local dashboard | FastAPI + Uvicorn, loopback only | LAN is disabled. It may return only after a security gate approves standard auth, certificate lifecycle, and revocation. |
| Browser | Playwright | Reintroduce only as a scoped, consented tool with isolated profile and action preview. |
| Tests | pytest, Ruff, mypy | Static checks become blocking for changed files before being repo-wide gates. |

## Technology being removed or quarantined

- `google-generativeai`: obsolete parallel Google SDK; use `google-genai` through
  the gateway only.
- Direct `actions/*` dispatch from `main.py`: temporarily adapted by P1-F, then
  replaced with native Tools.
- `shell=True`, shell string construction, model-generated local commands, and
  direct Python execution: prohibited.
- Auto-closing user processes: removed. Monitoring may suggest an action but may
  not perform it.
- Model-controlled `confirmed=yes`: not a consent mechanism.
- Remote dashboard security based on short pairing codes or custom transport
  encryption: not an acceptable release control.

## Module ownership

| Module | Interface responsibility | Must not know |
|---|---|---|
| `kernel/types.py` | Immutable vocabulary: ToolCall, ToolResult, Event, RiskClass | UI, providers, action implementations |
| `kernel/tools/` | Registration, deadline, retry policy, result normalization | Consent presentation or model protocol |
| `kernel/policy/` | Decision, consent gate, audit record | How a tool performs its work |
| `kernel/gateway/` | Provider-neutral model input/output | Desktop actions or UI controls |
| `kernel/loop/` | Bounded plan-act-observe trace | Provider SDK details |
| modality adapters | Translate voice/text/dashboard input into session events | Direct handler access |
| native tools | One narrow capability and its validation | Provider, UI, or policy implementation |

The intended deep module is `PolicyEngine.run(call, registry, consent)`: callers
get authorization, audit, execution, timeouts, and structured results through one
small interface. They should never reproduce these concerns at call sites.

## Data and configuration

- `config/api_keys.json` is the single human-managed local configuration file and
  is ignored by Git.
- `.ultron/audit.sqlite3` is created at runtime and ignored by Git.
- Future memory uses a distinct SQLite database; it must not be mixed with audit
  records or configuration.
- Tool outputs identify artifacts by path/URL but do not put secret content in
  audit notes.

## Tool safety taxonomy

| Risk | Default | Examples |
|---|---|---|
| Read | allow | system status, weather, web research, screen capture |
| Write | ask | create file, save memory, reminder, send message |
| Execute | ask | open approved app, browser action, computer input |
| Destructive | deny | delete, shutdown, restart, kill process |

Legacy multipurpose handlers receive their highest possible risk until split.
That is intentionally conservative: `file_controller` is destructive even when
asked to list a directory, because its old interface can also delete and move.

## Engineering quality bar

1. A bug fix starts with a regression test at the lowest level that proves the
   user-visible behavior.
2. New native tools require argument validation, a risk class, bounded execution,
   clean failure text, and policy/audit integration tests.
3. Tests use fakes only for the clock, network/provider, and irreversible external
   effects. Kernel wiring is tested with the real registry and policy engine.
4. CI requires pytest plus Ruff and mypy on the maintained kernel, bootstrap, and
   test surface. Extending those checks to legacy modules is tracked remediation;
   advisory legacy scans are not a definition of done.
5. Dependencies are declared from a reviewed input file and resolved into a
   reproducible lock before the next release.

## Next architecture milestones

1. P1-F split legacy handlers into narrow native tools and delete the migration
   adapter.
2. P1-C centralize Gemini and Ollama under `kernel/gateway/`; no other module owns
   model IDs or credentials.
3. P1-G implement the bounded agent loop with trace persistence and evaluation
   hooks.
4. P1-H make Live voice a client of the loop, not a second execution system.
5. P2-E adds a sandboxed coding workspace; only then can code execution return.
