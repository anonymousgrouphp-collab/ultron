"""kernel/coding/projects.py — research/12 D11: named project workspaces.

ada_v2's ProjectManager insight: an agent doing real work needs NAMED,
persistent project folders with their own artifacts, a "current project"
pointer, and a one-call context injection (file walk + text contents) so
the model knows what it is working on. ULTRON's coding tools are already
jailed to the workspace root; projects live under `projects/` inside it —
the existing jail holds, these tools add the organization layer:

- `project_create(name)`  — mkdir projects/<name> (safe charset only)
- `project_switch(name)`  — move the current-project pointer
- `project_list()`        — folders + which is current
- `project_context(name?)`— ada's get_project_context port: file walk +
                            text-file contents (size-capped) in one string
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from kernel.types import RiskClass, ToolCall, ToolResult

log = logging.getLogger(__name__)

__all__ = ["build_project_tools"]

_MAX_FILE_BYTES = 10_000
_MAX_CONTEXT_CHARS = 20_000
_TEXT_EXTENSIONS = {".txt", ".py", ".js", ".ts", ".json", ".md", ".html",
                    ".css", ".csv", ".jsonl", ".yaml", ".yml", ".toml", ".ini"}


def _safe_name(raw: str) -> str:
    name = "".join(ch for ch in str(raw)
                   if ch.isalnum() or ch in (" ", "-", "_")).strip()
    return " ".join(name.split()) or ""


def _projects_root(workspace: Path) -> Path:
    return Path(workspace) / "projects"


def _pointer(root: Path) -> Path:
    return root / ".current"


def _current_project(root: Path) -> str:
    try:
        return _pointer(root).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def build_project_tools(registry, workspace) -> None:
    """Register the four project tools under the coding workspace jail."""
    ws = Path(workspace)
    root = _projects_root(ws)
    root.mkdir(parents=True, exist_ok=True)

    @registry.tool(
        name="project_create",
        description="Create a named project folder under the workspace "
                    "(projects/<name>/) and switch to it. Use one project "
                    "per ongoing piece of work; files and scripts then live "
                    "under projects/<name>/.",
        parameters={"type": "object",
                    "properties": {"name": {"type": "string",
                                            "description": "project name"}},
                    "required": ["name"]},
        risk=RiskClass.WRITE,
    )
    def project_create(call: ToolCall) -> ToolResult | dict[str, Any]:
        name = _safe_name(str(call.args.get("name") or ""))
        if not name:
            return ToolResult.fail(call, "a project name is required")
        project = root / name
        if project.exists():
            _pointer(root).write_text(name, encoding="utf-8")
            return {"created": False, "project": name,
                    "path": f"projects/{name}",
                    "note": "project already existed — switched to it"}
        project.mkdir(parents=True)
        _pointer(root).write_text(name, encoding="utf-8")
        return {"created": True, "project": name, "path": f"projects/{name}"}

    @registry.tool(
        name="project_switch",
        description="Switch the current project by name (must already "
                    "exist). Later project_context calls describe the "
                    "active project.",
        parameters={"type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"]},
        risk=RiskClass.WRITE,
    )
    def project_switch(call: ToolCall) -> ToolResult | dict[str, Any]:
        name = _safe_name(str(call.args.get("name") or ""))
        if not name or not (root / name).is_dir():
            available = [p.name for p in root.iterdir() if p.is_dir()]
            return ToolResult.fail(
                call, f"no project named {name!r} "
                      f"(existing: {', '.join(available) or 'none'})")
        _pointer(root).write_text(name, encoding="utf-8")
        return {"switched": True, "project": name}

    @registry.tool(
        name="project_list",
        description="List the project folders and which one is current.",
        parameters={"type": "object", "properties": {}},
        risk=RiskClass.READ,
    )
    def project_list(call: ToolCall) -> ToolResult | dict[str, Any]:
        projects = sorted(p.name for p in root.iterdir() if p.is_dir())
        return {"projects": projects, "current": _current_project(root)}

    @registry.tool(
        name="project_context",
        description="One-call context for a project (default: the current "
                    "one): a file listing plus the text contents of every "
                    "small text file. Use before editing or continuing "
                    "work in a project.",
        parameters={"type": "object",
                    "properties": {"name": {"type": "string",
                                            "description": "optional project "
                                                           "name (default: "
                                                           "current)"}},
        },
        risk=RiskClass.READ,
    )
    def project_context(call: ToolCall) -> ToolResult | dict[str, Any]:
        name = _safe_name(str(call.args.get("name") or "")) \
            or _current_project(root)
        if not name:
            return ToolResult.fail(
                call, "no project is current — call project_create or "
                      "project_switch first (or pass a name)")
        project = root / name
        if not project.is_dir():
            return ToolResult.fail(
                call, f"project {name!r} no longer exists — switch to "
                      "another one")
        files: list[str] = []
        for path in sorted(project.rglob("*")):
            if path.is_file():
                files.append(str(path.relative_to(project)))
        lines = [f"=== project '{name}' — {len(files)} file(s) ==="]
        budget = _MAX_CONTEXT_CHARS
        for rel in files:
            path = project / rel
            lines.append(f"--- {rel} ---")
            try:
                if (path.suffix.lower() not in _TEXT_EXTENSIONS
                        or path.stat().st_size > _MAX_FILE_BYTES):
                    lines.append("(binary or oversized — skipped)")
                    continue
                text = path.read_text(encoding="utf-8", errors="replace")
                if len(text) > budget:
                    lines.append("(context budget reached — contents omitted)")
                    break
                lines.append(text)
                budget -= len(text)
            except OSError:
                lines.append("(unreadable — skipped)")
        return {"project": name, "files": files,
                "context": "\n".join(lines)[:_MAX_CONTEXT_CHARS]}
