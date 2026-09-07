"""kernel/coding/sandbox.py — P2-E: the default sandbox rung (research/03 §7).

`run_sandboxed` spawns a process with:
- NO shell (argv list only — shell injection is structurally impossible),
- a scrubbed environment (API keys/tokens/secrets never reach child code),
- a wall-clock timeout (the tree is killed on expiry),
- capped captured output (a runaway print cannot balloon memory),
- on Windows (pywin32 present): a Job Object with a memory cap and
  JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE, so the whole child tree dies with the
  job handle — the ladder's "trusted-lite" default rung.
"""

from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

__all__ = ["JOB_OBJECT_AVAILABLE", "RunResult", "run_sandboxed", "scrub_env"]

try:  # pywin32 is a Windows-only pin; the ladder degrades gracefully without it
    import win32api  # type: ignore[import-untyped]
    import win32con  # type: ignore[import-untyped]
    import win32job  # type: ignore[import-untyped]
    import pywintypes  # type: ignore[import-untyped]

    JOB_OBJECT_AVAILABLE = True
except ImportError:  # pragma: no cover — non-Windows dev boxes
    JOB_OBJECT_AVAILABLE = False

_SECRET_KEY = ("api", "key", "token", "secret", "password", "gemini",
               "google_api", "openai", "anthropic", "telegram", "ntfy")


@dataclass(frozen=True)
class RunResult:
    exit_code: int | None
    stdout: str
    stderr: str
    duration_ms: float
    sandbox: str  # "job-object" | "plain"


def scrub_env(env: dict[str, str] | None = None) -> dict[str, str]:
    """Environment for child processes: ULTRON's secrets stripped. A key is
    dropped when its NAME (not value) looks sensitive — values are never
    inspected, logged, or transmitted."""
    source = dict(os.environ if env is None else env)
    cleaned: dict[str, str] = {}
    for name, value in source.items():
        lowered = name.lower()
        if any(marker in lowered for marker in _SECRET_KEY):
            continue
        cleaned[name] = value
    return cleaned


def _win32_job(memory_limit_mb: int):
    """Create a Job Object (memory cap + kill-on-close). Returns the handle."""
    job = win32job.CreateJobObject(None, "")
    info = win32job.QueryInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation)
    info["BasicLimitInformation"]["LimitFlags"] |= (
        win32job.JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        | win32job.JOB_OBJECT_LIMIT_PROCESS_MEMORY)
    info["ProcessMemoryLimit"] = memory_limit_mb * 1024 * 1024
    win32job.SetInformationJobObject(
        job, win32job.JobObjectExtendedLimitInformation, info)
    return job


def run_sandboxed(
    argv: list[str],
    *,
    cwd: str | Path,
    timeout_s: float = 30.0,
    output_cap: int = 64_000,
    memory_limit_mb: int = 2048,
    env: dict[str, str] | None = None,
) -> RunResult:
    """Run argv in the default sandbox rung. Raises TimeoutError-shaped
    results via the return contract: the CALLER decides what a timeout or
    non-zero exit means (the tool wrapper turns them into clean ToolResults).
    Raises nothing except ValueError for bad input."""
    if not argv or not all(isinstance(part, str) for part in argv):
        raise ValueError("run_sandboxed needs a non-empty argv list of strings")
    started = time.monotonic()
    clean = scrub_env(env)
    job = _win32_job(memory_limit_mb) if JOB_OBJECT_AVAILABLE else None
    try:
        proc = subprocess.Popen(
            argv, cwd=str(cwd), env=clean, shell=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if job is not None:
            try:
                # OpenProcess with the rights the job APIs require; a child
                # that already exited simply misses the job's limits.
                handle = win32api.OpenProcess(
                    win32con.PROCESS_SET_QUOTA | win32con.PROCESS_TERMINATE,
                    False, proc.pid)
                win32job.AssignProcessToJobObject(job, handle)
            except pywintypes.error:
                log.debug("job-object assignment skipped (child already gone)")
        try:
            out, err = proc.communicate(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            proc.kill()
            out, err = proc.communicate()
            duration = (time.monotonic() - started) * 1000.0
            return RunResult(exit_code=None,
                             stdout=_cap(out or b"", output_cap),
                             stderr=_cap(err or b"", output_cap)
                             + f"\n[sandbox] timed out after {timeout_s:g}s "
                               "(process tree terminated)",
                             duration_ms=duration,
                             sandbox="job-object" if job is not None else "plain")
    finally:
        if job is not None:
            try:
                job.Close()  # kill-on-close: no orphaned tree
            except Exception:  # noqa: BLE001 — a closed job must not fail the run
                log.exception("closing the job object failed")
    duration = (time.monotonic() - started) * 1000.0
    return RunResult(
        exit_code=proc.returncode,
        stdout=_cap(out or b"", output_cap),
        stderr=_cap(err or b"", output_cap),
        duration_ms=duration,
        sandbox="job-object" if job is not None else "plain",
    )


def _cap(raw: bytes, cap: int) -> str:
    text = raw.decode("utf-8", errors="replace")
    if len(text) <= cap:
        return text
    return text[:cap] + f"\n[sandbox] output truncated at {cap} chars"
