# AGENTS.md — Operating Manual for AI Agents Working on ULTRON

Every AI chat/agent session working in this repo **must** follow this file. Purpose:
keep parallel work aligned with the strategy docs, prevent merge conflicts between
simultaneous chats, and make every deliverable verifiable. Project goal: evolve the
ULTRON voice assistant into a JARVIS-like general agent harness (see `docs/ROADMAP.md`).

---

## 1. Mandatory reading (in order, before touching code)

1. `docs/ROADMAP.md` — strategy, phases 0–6, **acceptance gates**, Kill List (§5)
2. `docs/research/00_README.md` — research synthesis, chosen stack, build order
3. `docs/research/01_jarvis_feature_catalog.md` — feature IDs **J-01…J-28**
4. `PROGRESS.md` — live task board. Find your workstream or claim an open one
5. Only the deep-dive docs (`docs/research/02…08_*.md`) relevant to **your** stream

## 2. Alignment rules (how work stays tied to the docs)

- **Reference your IDs.** Every task/commit maps to a Phase (0–6) and, where
  applicable, a J-ID (e.g. "J-06 capture fix"). If you can't name the ID, re-read the
  docs — the task probably shouldn't exist.
- **The Kill List is binding** (ROADMAP §5): no new `actions/` modules in the old
  style, no second implementation of anything, no model strings outside the gateway,
  no raw exceptions spoken aloud, one name — **ULTRON**.
- **Docs and code must never diverge silently.** If your change contradicts a doc,
  either update the doc in the same commit (note it in `PROGRESS.md`) or stop and
  flag it in `PROGRESS.md` → Findings.
- **Scope discipline.** Do only your claimed stream. Discover something broken
  outside your files? Log it in `PROGRESS.md` → Findings/Blockers. Don't fix it there.

## 3. Parallel-work protocol (multiple chats, one repo)

- **One chat = one workstream.** Claim it in `PROGRESS.md` before your first edit
  (Status → `in-progress`, Owner → chat name + date). Never edit another stream's
  owned files (ownership table on the board).
- **Commit small and often** on your stream branch `p0-<stream>` (e.g. `p0-security`),
  one logical change per commit, message format: `[P0-B2] dashboard binds 127.0.0.1 by default`.
- **Same-file conflicts:** `main.py` is the hotspot — during Phase 0 *only* P0-A may
  touch it. If two streams genuinely need the same file, the later one waits for the
  earlier one's merge (dependency column on the board).
- **Isolation option:** if you must run truly conflicting streams at once, use
  `git worktree add ../ultron-<stream> p0-<stream>` and open the chat in that folder.
- **Merge order:** dependency-free streams first (P0-A, P0-B); P0-C/P0-D after their
  dependencies merge; P0-E (CI) merges last because it tests everything.
- Rebase your branch on `main` before opening the merge; CI must be green.

### Progress discipline & sign-off (STRICT)

- **Update your board row in the same commit as your code.** A commit without its
  `PROGRESS.md` update is an incomplete commit. Non-negotiable with parallel chats.
- **Sign-off:** on completing a task, set Status → ✅ and fill the Sign-off column
  with `✍ <chat-name> <date>` plus an evidence reference in Notes. Only sign what
  you verified (tests run / commands / greps — real output, not claims).
- **DEP rule (dependencies):** every row in `PROGRESS.md` has a `DEP` column.
  - `🔓 START NOW` → proceed, zero dependencies.
  - `DEP: <ID>` → **check that ID on the board first.** If it is ✅ **and signed**,
    proceed (mind its merge state in Notes). If it is still ⬜/🔶, **STOP and tell
    the user**: "[my task] is blocked by [ID], which isn't signed off yet — pick
    another 🔓 stream or wait." Never silently do someone else's stream to unblock
    yourself; never edit a blocked dependency's files.
- A stream is "done" only when: all rows ✅ + signed, a Verification Log entry with
  evidence exists, and affected docs are still accurate.

## 4. Verification requirement (re-verify deliverables — this is not optional)

**After every task:** run the checks your stream's board row lists (minimum:
`python -m py_compile <touched files>` until CI exists; after P0-E: `pytest`, `ruff`).

**After completing a phase (or a phase-gate deliverable), an agent MUST re-verify:**
1. Re-read the phase's **acceptance gate** in `docs/ROADMAP.md` §4.
2. Re-read the relevant research doc's "Integration notes" section and confirm the
   implementation matches it.
3. Produce evidence, not claims — run the commands, grep for banned patterns
   (e.g. `0.0.0.0` bindings, committed keys, `exec(`, model-name strings), and record
   actual command output summaries.
4. Write an entry in `PROGRESS.md` → **Verification Log** (date, deliverable, checks
   run, results, residual risks). A phase with no verification entry is not done.

**Deliverable checklist (every phase):**
- [ ] Code compiles/imports; tests pass; no new Kill-List violations
- [ ] Acceptance gate conditions checked one-by-one with evidence
- [ ] Affected docs (ROADMAP/research) still accurate — updated if not
- [ ] `PROGRESS.md` board rows + Verification Log updated

## 5. Style & conduct

- Match surrounding code style; no drive-by reformatting.
- Windows 10/11 is the deployment target; test paths/commands accordingly (Git Bash
  + PowerShell both exist).
- Never commit secrets; never "quickly" widen a permission/bind address.
- Be blunt in `PROGRESS.md` notes: failed checks are recorded as failed, with output.

---

*Board: `PROGRESS.md` · Strategy: `docs/ROADMAP.md` · Research: `docs/research/`*
