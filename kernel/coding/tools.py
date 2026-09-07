"""kernel/coding/tools.py — P2-E: the workspace-jailed coding toolset (J-11)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kernel.coding.sandbox import run_sandboxed
from kernel.orchestrator.queue import JobQueue
from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

__all__ = ["CODING_TOOLS", "build_coding_tools", "spawn_coding_job"]

CODING_TOOLS = ("list_files", "read_file", "write_file", "run_command")
_MAX_FILE_BYTES = 256_000
_SKIP_DIRS = {".git", "__pycache__", ".venv", "node_modules", ".ultron"}
_BLOCKED_FRAGMENTS = ("pip install", "pip3 install")


def _jailed(workspace: Path, relative: str) -> Path:
    """Resolve `relative` inside the workspace; escape attempts fail loudly."""
    if not relative or relative.strip() == "":
        raise ValueError("a path inside the workspace is required")
    candidate = (workspace / relative).resolve()
    root = workspace.resolve()
    if candidate != root and root not in candidate.parents:
        raise ValueError(f"path escapes the coding workspace: {relative!r}")
    return candidate


def build_coding_tools(registry, workspace: Path, *, run_timeout_s: float = 30.0,
                       output_cap: int = 64_000, allow_run: bool = True,
                       memory_limit_mb: int = 2048) -> None:
    """Register the four coding tools, all jailed to `workspace`. `allow_run`
    is the run_command consent knob (off = read/write workspace only)."""
    ws = Path(workspace)
    ws.mkdir(parents=True, exist_ok=True)

    @registry.tool(
        name="list_files",
        description="List files in the coding workspace (relative paths, "
                    "directories skipped from the listing).",
        parameters={"type": "object", "properties": {}},
        risk=RiskClass.READ,
    )
    def list_files(call: ToolCall) -> ToolResult | dict[str, Any]:
        found = []
        for path in sorted(ws.rglob("*")):
            if any(part in _SKIP_DIRS for part in path.parts):
                continue
            if path.is_file():
                found.append(str(path.relative_to(ws)))
        return {"files": found[:500], "total": len(found)}

    @registry.tool(
        name="read_file",
        description="Read a text file from the coding workspace by relative "
                    "path (size-capped).",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string",
                                            "description": "relative path"}},
                    "required": ["path"]},
        risk=RiskClass.READ,
    )
    def read_file(call: ToolCall) -> ToolResult | dict[str, Any]:
        try:
            path = _jailed(ws, str(call.args.get("path", "")))
        except ValueError as exc:
            return ToolResult.fail(call, str(exc))
        if not path.is_file():
            return ToolResult.fail(call, f"no such file: {call.args['path']}")
        raw = path.read_bytes()
        if len(raw) > _MAX_FILE_BYTES:
            return ToolResult.fail(
                call, f"file too large to read ({len(raw)} bytes, cap "
                      f"{_MAX_FILE_BYTES})")
        return {"path": str(call.args["path"]),
                "text": raw.decode("utf-8", errors="replace")}

    @registry.tool(
        name="write_file",
        description="Create or overwrite a text file in the coding workspace "
                    "(relative path; parent directories are created).",
        parameters={"type": "object",
                    "properties": {"path": {"type": "string"},
                                   "text": {"type": "string"}},
                    "required": ["path", "text"]},
        risk=RiskClass.WRITE,
    )
    def write_file(call: ToolCall) -> ToolResult | dict[str, Any]:
        try:
            path = _jailed(ws, str(call.args.get("path", "")))
        except ValueError as exc:
            return ToolResult.fail(call, str(exc))
        text = str(call.args.get("text", ""))
        if len(text.encode("utf-8")) > _MAX_FILE_BYTES:
            return ToolResult.fail(
                call, f"write too large (cap {_MAX_FILE_BYTES} bytes)")
        path.parent.mkdir(parents=True, exist_ok=True)
        # newline="": byte-exact writes — a coding agent's text (and its diff)
        # must survive unchanged, no platform newline translation.
        path.write_text(text, encoding="utf-8", newline="")
        return {"written": str(call.args["path"]), "bytes": len(text.encode("utf-8"))}

    if allow_run:
        @registry.tool(
            name="run_command",
            description="Run a program with arguments inside the coding "
                        "workspace sandbox (no shell, timeout, capped output, "
                        "scrubbed env, no package installation).",
            parameters={"type": "object",
                        "properties": {
                            "argv": {"type": "array",
                                     "items": {"type": "string"},
                                     "description": "program + arguments, "
                                                    "e.g. [\"python\", \"main.py\"]"}},
                        "required": ["argv"]},
            risk=RiskClass.EXECUTE,
        )
        def run_command(call: ToolCall) -> ToolResult | dict[str, Any]:
            argv = call.args.get("argv")
            if (not isinstance(argv, list) or not argv
                    or not all(isinstance(p, str) for p in argv)):
                return ToolResult.fail(
                    call, "run_command needs argv: a non-empty list of strings")
            joined = " ".join(argv).lower()
            if any(fragment in joined for fragment in _BLOCKED_FRAGMENTS):
                return ToolResult.fail(
                    call, "package installation is disabled in the coding "
                          "sandbox (no ambient pip-install)")
            try:
                outcome = run_sandboxed(
                    argv, cwd=ws, timeout_s=run_timeout_s,
                    output_cap=output_cap, memory_limit_mb=memory_limit_mb)
            except (OSError, ValueError) as exc:
                return ToolResult.fail(
                    call, f"could not run {argv[0]} ({type(exc).__name__})")
            if outcome.exit_code is None:  # timeout — tree terminated
                return ToolResult.fail(call, outcome.stderr.strip())
            return {"exit_code": outcome.exit_code, "stdout": outcome.stdout,
                    "stderr": outcome.stderr, "sandbox": outcome.sandbox}


def spawn_coding_job(queue: JobQueue, *, task: str, workspace_label: str = "",
                     max_steps: int = 12, priority: int = 0,
                     max_attempts: int = 2, job_id: str | None = None,
                     extra_tools: list[str] | None = None) -> str:
    """Enqueue the J-11 loop: an orchestrator AGENT step whose allowlist is the
    coding toolset. The workspace itself is bound when build_coding_tools ran;
    the subagent instruction carries the task."""
    if not task.strip():
        raise ValueError("coding task is required")
    tools = list(CODING_TOOLS) + list(extra_tools or [])
    where = f" in the workspace {workspace_label}" if workspace_label else ""
    instruction = (
        f"Coding task{where}: {task}\n"
        "Work iteratively: inspect files, write or edit code, run it with "
        "run_command (python is available), read the output, and fix problems "
        "until the task is done. Package installation is unavailable.")
    from kernel.orchestrator.subagent import spawn_subagent
    return spawn_subagent(
        queue, instruction=instruction, tools=tools, title=task[:60],
        max_steps=max_steps, priority=priority, max_attempts=max_attempts,
        job_id=job_id)
