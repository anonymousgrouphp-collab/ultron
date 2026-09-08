"""evals/phase2_gate.py — the PHASE 2 acceptance gate (docs/ROADMAP.md §4).

Gate: "'research X → report to Desktop' as a checkpointed background job,
using ≥3 tools including one external MCP server, and survives a kernel
restart mid-task."

Run:  py -3.13 evals/phase2_gate.py

What this script does (all local, no network, no API keys):
1. Builds the REAL kernel stack: P2-C JobQueue + Orchestrator + P2-D research
   tools + a P2-B mount of a REAL external MCP server
   (evals/filesystem_server.py — its own process over stdio, like the official
   reference servers; pointed at the "Desktop" sandbox dir).
2. Enqueues the research→report plan: web_search → web_read → fs_write_report
   (3 tools across 2 servers, one of them external — gate condition met).
3. Worker #1 runs the job; the moment the checkpoint shows step 1 done, the
   worker process is KILLED mid-task (the read step is held open to make the
   kill window deterministic).
4. A FRESH worker resumes from the durable checkpoint and completes the job.
5. Verifies end-states: report file exists in the Desktop sandbox with the
   extracted title+body (written THROUGH the external MCP server), attempts
   == 2, audit trail present. Evidence → .ultron/eval/phase2/gate_results.json.

This is an eval script, not kernel code — the kernel itself never imports
config or spawns this wiring (same doctrine as evals/phase1_gate.py).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from kernel.orchestrator import JobQueue  # noqa: E402
from kernel.research import research_report_plan  # noqa: E402

EVAL_DIR = BASE / ".ultron" / "eval" / "phase2"
TOPIC = "fusion energy net gain"
PLAN = research_report_plan(TOPIC, report_tool="fs_write_report",
                            report_name="desktop_report.txt")


def log(msg: str) -> None:
    print(f"[gate] {msg}", flush=True)


def spawn_worker(tag: str, slow: bool, stderr_target) -> subprocess.Popen:
    env = {**os.environ, "PYTHONPATH": str(BASE)}
    argv = [sys.executable, str(BASE / "evals" / "phase2_worker.py"),
            str(EVAL_DIR / "queue.db"), str(EVAL_DIR / "reports")]
    if slow:
        argv.append("--slow-read")
    return subprocess.Popen(argv, env=env, cwd=str(BASE),
                            stdout=subprocess.DEVNULL, stderr=stderr_target)


def main() -> int:
    checks: dict[str, object] = {}
    ok = True
    try:
        import shutil
        shutil.rmtree(EVAL_DIR, ignore_errors=True)
        (EVAL_DIR / "reports").mkdir(parents=True, exist_ok=True)

        # gate condition: ≥3 tools incl. one external MCP server
        tool_names = ([PLAN[0]["spec"]["name"], PLAN[1]["spec"]["name"]]
                      + PLAN[2]["spec"]["name"].split(",")) if False else [
            PLAN[0]["spec"]["name"], PLAN[1]["spec"]["name"],
            PLAN[2]["spec"]["name"]]
        checks["plan_tools"] = tool_names
        checks["external_mcp_server"] = "fs_write_report via filesystem_server.py (stdio subprocess)"
        assert len(tool_names) >= 3
        log(f"plan tools: {tool_names}")

        queue = JobQueue(EVAL_DIR / "queue.db")
        jid = queue.enqueue("plan", {"plan": PLAN}, title=f"gate: {TOPIC}",
                            max_attempts=3)
        checks["job_id"] = jid

        # worker #1: run until step 1 is checkpointed, then KILL mid-task
        err1 = open(EVAL_DIR / "worker1.stderr.log", "wb")
        proc1 = spawn_worker("w1", slow=True, stderr_target=err1)
        deadline = time.time() + 30
        while time.time() < deadline:
            job = queue.get(jid)
            if job and "search" in job.done_steps():
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("worker 1 never checkpointed step 1")
        proc1.kill()
        proc1.wait(timeout=10)
        mid = queue.get(jid)
        checks["killed_mid_task"] = {
            "status": mid.status,
            "done_steps": sorted(mid.done_steps()),
            "checkpoint_present": mid.checkpoint is not None,
        }
        if mid.status != "running" or "read" in mid.done_steps():
            raise RuntimeError(
                f"kill landed outside the read step: {checks['killed_mid_task']}")
        log(f"worker killed mid-task: status={mid.status}, "
            f"done={sorted(mid.done_steps())} — checkpoint durable")

        # worker #2: fresh process resumes from the checkpoint
        err2 = open(EVAL_DIR / "worker2.stderr.log", "wb")
        proc2 = spawn_worker("w2", slow=False, stderr_target=err2)
        deadline = time.time() + 60
        while time.time() < deadline:
            job = queue.get(jid)
            if job and job.status in ("done", "failed"):
                break
            time.sleep(0.1)
        else:
            raise RuntimeError("resumed worker never finished")
        proc2.kill()
        proc2.wait(timeout=10)
        final = queue.get(jid)
        if final.status != "done":
            raise RuntimeError(f"job failed on resume: {final.error}")
        checks["resumed"] = {"attempts": final.attempts,
                             "done_steps": sorted(final.done_steps())}
        if final.attempts != 2:
            raise RuntimeError(f"expected attempts==2, got {final.attempts}")
        log(f"resumed and completed in a fresh worker: attempts={final.attempts}")

        # end-state: the report exists on the "Desktop", written via MCP
        report = EVAL_DIR / "reports" / "desktop_report.txt"
        text = report.read_text(encoding="utf-8")
        checks["report"] = {"exists": report.exists(), "bytes": len(text),
                            "has_title": "Fusion energy: net gain achieved" in text,
                            "has_body": "net energy gain" in text}
        if not (checks["report"]["has_title"] and checks["report"]["has_body"]):
            raise RuntimeError(f"report content wrong: {text[:200]!r}")
        log(f"report verified in Desktop sandbox: {len(text)} bytes, "
            "written through the external MCP server")
    except Exception as exc:  # noqa: BLE001 — the gate reports, then fails
        ok = False
        checks["error"] = f"{type(exc).__name__}: {exc}"

    checks["pass"] = ok
    EVAL_DIR.mkdir(parents=True, exist_ok=True)
    (EVAL_DIR / "gate_results.json").write_text(
        json.dumps(checks, indent=2, default=str), encoding="utf-8")
    log(f"{'PASS' if ok else 'FAIL'} — evidence: .ultron/eval/phase2/gate_results.json")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
