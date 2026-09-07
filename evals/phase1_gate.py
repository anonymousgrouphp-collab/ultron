"""evals/phase1_gate.py — the PHASE 1 acceptance gate (docs/ROADMAP.md §4).

"Text session completes 10 scripted multi-step tasks via kernel; voice demo
unchanged."

Run:  py -3.13 evals/phase1_gate.py [--provider gemini|ollama]

The harness builds a fresh ToolRegistry with sandboxed note tools + a seeded
memory engine, then drives each task through the REAL AgentLoop → PolicyEngine
consent/audit stack with a LIVE model via the P1-C gateway. Verifiers check
end-states (note contents, policy refusals, counts), never exact wording
(research/05 §7: scripted verifiers wherever a task has a checkable end-state).
WRITE actions run with auto-consent (running this script IS the consent);
DESTRUCTIVE stays absolute-denied — one task asserts that refusal.

Results land in .ultron/eval/phase1_gate_results.json. Sandbox/memory contents
are synthetic; the gate can touch nothing outside its sandbox. This is an eval
script, not kernel code — importing config.loader here is by design (the
kernel itself never does).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from config.loader import get_api_key, load_config  # noqa: E402
from kernel.gateway import (  # noqa: E402
    Gateway,
    GatewayError,
    GatewaySettings,
    GeminiAdapter,
    Message,
    OllamaAdapter,
    Provider,
)
from kernel.loop import AgentLoop, LoopResult  # noqa: E402
from kernel.memory import (  # noqa: E402
    HashingEmbedder,
    MemoryEngine,
    register_memory_tools,
)
from kernel.policy import AuditLog, PolicyEngine  # noqa: E402
from kernel.tools import ToolRegistry  # noqa: E402
from kernel.types import RiskClass, ToolCall, ToolResult  # noqa: E402

EVAL_DIR = BASE / ".ultron" / "eval"
SANDBOX = EVAL_DIR / "sandbox"


# --------------------------------------------------------------- tools -----


def make_gate_registry(engine: MemoryEngine) -> ToolRegistry:
    reg = ToolRegistry()
    sandbox = SANDBOX
    sandbox.mkdir(parents=True, exist_ok=True)

    def note_path(name: str) -> Path:
        safe = "".join(c for c in name if c.isalnum() or c in "-_")
        return sandbox / f"{safe}.txt"

    @reg.tool(
        name="write_note",
        description="Create or overwrite a note in the sandbox. Returns a "
                    "confirmation with the note name.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string", "description": "short note name"},
            "text": {"type": "string", "description": "full note text"},
        }, "required": ["name", "text"]},
        risk=RiskClass.WRITE,
    )
    def write_note(call):
        note_path(str(call.args.get("name", ""))).write_text(
            str(call.args.get("text", "")), encoding="utf-8")
        return "note saved"

    @reg.tool(
        name="read_note",
        description="Read the full text of a sandbox note by name.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}
        }, "required": ["name"]},
        risk=RiskClass.READ,
    )
    def read_note(call):
        path = note_path(str(call.args.get("name", "")))
        if not path.exists():
            return ToolResult.fail(call, f"note {path.stem!r} does not exist")
        return path.read_text(encoding="utf-8")

    @reg.tool(
        name="list_notes",
        description="List the names of all sandbox notes.",
        parameters={"type": "object", "properties": {}, "required": []},
        risk=RiskClass.READ,
    )
    def list_notes(call):
        return sorted(p.stem for p in sandbox.glob("*.txt"))

    @reg.tool(
        name="delete_note",
        description="Delete a sandbox note permanently by name.",
        parameters={"type": "object", "properties": {
            "name": {"type": "string"}
        }, "required": ["name"]},
        risk=RiskClass.DESTRUCTIVE,
    )
    def delete_note(call):
        path = note_path(str(call.args.get("name", "")))
        path.unlink(missing_ok=True)
        return "deleted"

    register_memory_tools(reg, engine)
    return reg


# ---------------------------------------------------------------- tasks ----


@dataclass
class GateTask:
    id: int
    prompt: str
    verify: Callable[[LoopResult], tuple[bool, str]]


def _note(name: str) -> str:
    path = SANDBOX / f"{name}.txt"
    return path.read_text(encoding="utf-8") if path.exists() else ""


def _has(name: str, *needles: str) -> bool:
    content = _note(name).lower()
    return all(n.lower() in content for n in needles)


def _count_notes() -> int:
    return len(list(SANDBOX.glob("*.txt")))


TASKS: list[GateTask] = [
    GateTask(1,
             "Create a note named 'groceries' with the text 'milk and eggs', "
             "then read it back to confirm it saved, and report its content.",
             lambda r: (_has("groceries", "milk") and "milk" in r.text.lower(),
                        "groceries note exists with milk")),
    GateTask(2,
             "Create two notes: 'aa' containing 'first' and 'bb' containing "
             "'second'. Then list all notes and tell me how many notes exist.",
             lambda r: (_has("aa", "first") and _has("bb", "second"),
                        "both notes exist")),
    GateTask(3,
             "Read the note 'aa', then create a new note 'aa_copy' containing "
             "exactly the same text you read.",
             lambda r: (_has("aa_copy", "first"), "copy carries the content")),
    GateTask(4,
             "Search your long-term memory (memory_search) for the user's "
             "favorite editor and tell me which editor it is.",
             lambda r: ("neovim" in r.text.lower(), "answer recalls neovim")),
    GateTask(5,
             "Read the note 'poem' and tell me exactly how many words it "
             "contains.",
             lambda r: ("4" in r.text, "answer contains the count 4")),
    GateTask(6,
             "Write a note 'todo' that contains exactly these three items: "
             "'buy milk', 'walk dog', 'read book'. Then read it back to make "
             "sure all three are in it.",
             lambda r: (_has("todo", "buy milk", "walk dog", "read book"),
                        "todo has all three items")),
    GateTask(7,
             "Try to delete the note named 'protected' with delete_note. "
             "Whatever happens, report whether the deletion was allowed.",
             lambda r: (_note("protected") != "" and "protected" in r.text.lower(),
                        "protected note still exists (DESTRUCTIVE refused)")),
    GateTask(8,
             "Create three notes: 'n1' with '5', 'n2' with '7', 'n3' with "
             "'10'. Then read all three and tell me their sum.",
             lambda r: (_has("n1", "5") and _has("n2", "7") and _has("n3", "10")
                        and "22" in r.text, "notes exist and sum 22 reported")),
    GateTask(9,
             "Search your memory (memory_search) for facts about the ULTRON "
             "project, then write a note 'findings' containing what you found.",
             lambda r: (_has("findings", "harness") or _has("findings", "ultron"),
                        "findings note carries recalled memory")),
    GateTask(10,
             "Read the note 'groceries', then create 'groceries_summary' with "
             "a one-sentence summary of it, then use list_notes and report "
             "the total number of notes.",
             lambda r: (_has("groceries_summary", "milk") and _count_notes() >= 6,
                        "summary written from the read; notes listed")),
]


# ----------------------------------------------------------------- run -----


async def _auto_consent(call: ToolCall, risk: RiskClass) -> bool:
    return True  # scripted session: running the gate IS the consent


class PacedGateway:
    """Harness-side Completer wrapper: minimum spacing between calls and
    exponential backoff on 429 (free-tier rate limits). Kernel stays clean —
    pacing/retry policy is an eval-runner concern until P2-C owns retries."""

    def __init__(self, inner: Gateway, min_interval_s: float = 4.0,
                 max_retries: int = 4) -> None:
        self._inner = inner
        self._min = min_interval_s
        self._max_retries = max_retries
        self._last = 0.0

    async def complete(self, messages, tools=(), response_schema=None):
        for attempt in range(self._max_retries + 1):
            gap = time.monotonic() - self._last
            if gap < self._min:
                await asyncio.sleep(self._min - gap)
            try:
                result = await self._inner.complete(messages, tools=tools,
                                                    response_schema=response_schema)
                self._last = time.monotonic()
                return result
            except GatewayError as exc:
                self._last = time.monotonic()
                if "429" not in str(exc) or attempt == self._max_retries:
                    raise
                wait = 30.0 * (attempt + 1)
                print(f"    (429 rate-limited; waiting {wait:.0f}s)")
                await asyncio.sleep(wait)
        raise AssertionError("unreachable")


async def run_gate(provider_name: str) -> int:
    # clean sandbox, seed fixed fixtures
    SANDBOX.mkdir(parents=True, exist_ok=True)
    for old in SANDBOX.glob("*.txt"):
        old.unlink()
    (SANDBOX / "poem.txt").write_text("one two three four", encoding="utf-8")
    (SANDBOX / "protected.txt").write_text("do not delete", encoding="utf-8")

    engine = MemoryEngine(EVAL_DIR / "memory.sqlite3",
                          embedder=HashingEmbedder())
    engine.remember("The user's favorite editor is Neovim",
                    entity="preferences", source_ref="phase1-gate-seed")
    engine.remember("Project ULTRON is a personal agent harness built on a "
                    "kernel architecture", entity="projects",
                    source_ref="phase1-gate-seed")
    engine.remember("The user prefers short, direct answers",
                    entity="preferences", source_ref="phase1-gate-seed")

    registry = make_gate_registry(engine)
    audit = AuditLog(EVAL_DIR / "gate_audit.sqlite3")
    policy = PolicyEngine(audit=audit)

    settings = GatewaySettings.from_config(
        {**load_config(), "llm_provider": provider_name})
    gateway: Gateway
    if settings.provider is Provider.GEMINI:
        key = get_api_key("gemini_api_key")
        if not key:
            raise SystemExit("no gemini_api_key in config — cannot run the gate")
        gateway = GeminiAdapter(settings, api_key=key)
    else:
        gateway = OllamaAdapter(settings)

    loop = AgentLoop(PacedGateway(gateway), policy, registry, max_steps=10,
                     consent=_auto_consent, source="phase1-gate")

    print(f"PHASE-1 GATE — provider={settings.provider.value} "
          f"model={gateway.model}")
    print("=" * 68)
    results: list[dict[str, Any]] = []
    passed = 0
    for task in TASKS:
        start = time.monotonic()
        result: LoopResult | None = None
        try:
            result = await loop.run([Message(role="user", text=task.prompt)])
            ok, detail = task.verify(result)
            finish = result.finish
        except Exception as exc:  # noqa: BLE001 — the gate records, never dies
            ok, detail, finish = False, f"harness error: {exc}", "error"
        took = time.monotonic() - start
        passed += int(ok)
        steps = result.steps if result is not None else 0
        print(f"task {task.id:>2}: {'PASS' if ok else 'FAIL'}  "
              f"({steps} steps, finish={finish}, {took:.1f}s) — {detail}")
        results.append({"task": task.id, "ok": ok, "finish": finish,
                        "steps": steps, "detail": detail,
                        "text": (result.text[:200]
                                 if result is not None else "")})
    print("=" * 68)
    verdict = "PASS — Phase 1 gate met" if passed == 10 else "FAIL"
    print(f"GATE RESULT: {passed}/10 tasks passed ({verdict})")

    (EVAL_DIR / "phase1_gate_results.json").write_text(json.dumps(
        {"provider": settings.provider.value, "model": gateway.model,
         "passed": passed, "of": len(TASKS), "results": results},
        indent=2), encoding="utf-8")
    engine.close()
    return 0 if passed == 10 else 1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", default="gemini",
                        choices=["gemini", "ollama"])
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run_gate(args.provider)))


if __name__ == "__main__":
    main()
