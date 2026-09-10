"""evals/killlist_check.py — R2: Kill-List enforcement as a CI gate.

ROADMAP §5 bans (the ones checkable statically):
  - No LLM call outside the gateway        → any `google.genai` / OpenAI /
    anthropic SDK usage in the app layer is a violation; `actions/_llm.py`
    is the ONE seam and routes through `kernel.gateway`.
  - No model name outside the gateway      → quoted model strings
    (`"gemini-...` / `"gpt-...` / `"qwen...` / `"claude-...`) must not
    appear outside `kernel/` (the gateway is their authorized home).
  - No `exec()` of model-derived code in the app layer (P0-B5 hygiene).

Scan scope = the app layer: `actions/`, `core/`, `utils/`, `dashboard/`,
`main.py`, `ui.py`. `kernel/` and `evals/` are allowed by design (gateway
homes + eval tooling that builds GatewaySettings explicitly).

Usage: `python evals/killlist_check.py [paths...]` — exits 1 listing
file:line violations, 0 when clean. Plain stdlib, runs in CI (gating).
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# Windows consoles default to cp1252 and cannot print the arrows/emojis in
# reasons — reconfigure instead of crashing mid-gate (p5-evals precedent).
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]

BASE = Path(__file__).resolve().parent.parent
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

DEFAULT_SCOPE = [
    "actions",
    "core",
    "utils",
    "dashboard",
    "main.py",
    "ui.py",
]

# 1. LLM SDK imports/calls that bypass the gateway.
_SDK_PATTERNS = [
    (re.compile(r"\bfrom\s+google\s+import\s+genai\b"),
     "google.genai SDK import (must go through actions/_llm.py → kernel.gateway)"),
    (re.compile(r"\bfrom\s+google\.genai\b"),
     "google.genai SDK import (must go through actions/_llm.py → kernel.gateway)"),
    (re.compile(r"\bgenai\.Client\b"),
     "google.genai SDK client (must go through actions/_llm.py → kernel.gateway)"),
    (re.compile(r"\.generate_content\s*\("),
     "direct generate_content LLM call (must go through actions/_llm.py → kernel.gateway)"),
    (re.compile(r"\bfrom\s+openai\s+import\s+OpenAI\b"),
     "openai SDK import (must go through kernel.gateway)"),
    (re.compile(r"\bfrom\s+anthropic\b"),
     "anthropic SDK import (must go through kernel.gateway)"),
    (re.compile(r"\bchat\.completions\.create\s*\("),
     "direct chat.completions LLM call (must go through kernel.gateway)"),
]

# 2. Quoted model strings outside the gateway. A model id must contain a
#    digit — "gemini-live" (a provenance tag) and "llamacpp" (a provider
#    label) are not model ids and never trip this.
_MODEL_STRING = re.compile(r"""["'](?P<name>(?:gemini|gpt|o\d+|qwen|claude|llama)[\w.:-]*\d[\w.:-]*)["']""")

# 3. exec() in the app layer (P0-B5 — code execution of model output).
#    Qt's QApplication/QDialog `.exec()` event-loop calls are not exec().
_EXEC = re.compile(r"(?<!\.)\bexec\s*\(")

_SKIP_FILES = {
    "actions/_llm.py",  # the seam itself — routes through kernel.gateway only
}

# Documented residuals allowed to use the google SDK directly: main.py's
# Gemini Live audio session client (the voice modality adapter — P1-H
# doctrine keeps the audio pipeline untouched; the gateway has no Live
# adapter yet). Everything else in main.py (model strings, exec) still
# trips the gate.
_SDK_ALLOWED_IN = {
    "main.py": "Gemini Live audio session client (P1-H modality-adapter residual)",
}


def _iter_py_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root] if root.suffix == ".py" else []
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in str(p))


def check_path(path: Path) -> list[tuple[str, int, str]]:
    """Return [(file, line, reason)] violations for one file."""
    try:
        rel = path.relative_to(BASE).as_posix()
    except ValueError:
        rel = path.name  # outside the repo (e.g. a temp file in a test)
    if rel in _SKIP_FILES:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    findings: list[tuple[str, int, str]] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # comments/prose never trigger the gate
        for pattern, reason in _SDK_PATTERNS:
            if pattern.search(line) and rel not in _SDK_ALLOWED_IN:
                findings.append((rel, lineno, reason))
        m = _MODEL_STRING.search(line)
        if m:
            findings.append((rel, lineno,
                             f"model string {m.group('name')!r} outside the gateway"))
        if _EXEC.search(line):
            findings.append((rel, lineno, "exec() in the app layer"))
    return findings


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    scope = [Path(p) for p in (argv or DEFAULT_SCOPE)]
    violations: list[tuple[str, int, str]] = []
    for entry in scope:
        path = entry if entry.is_absolute() else BASE / entry
        for py in _iter_py_files(path):
            violations.extend(check_path(py))
    violations.sort()
    if not violations:
        print(f"kill-list check PASS — {len(scope)} scope entries clean")
        return 0
    print(f"kill-list check FAIL — {len(violations)} violation(s):")
    for rel, lineno, reason in violations:
        print(f"  {rel}:{lineno}  {reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
