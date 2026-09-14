"""kernel/computer/power.py — PJ-02: OS power verbs (research/13).

lock_workstation / shutdown / restart / sign_out / abort — the repo's
surajmaru/PERSONAL-JARVIS-AI-ASSISTANT flow (pending-confirmation globals +
`shutdown /t 5` grace) rebuilt as a consent-gated kernel tool:

- every verb goes through ToolRegistry → PolicyEngine as WRITE risk → the
  ConsentGate asks with the verb visible in the call args;
- shutdown/restart keep the 5-second grace (`shutdown /t N`) so a consent
  mistake is recoverable via the `abort` verb (`shutdown /a`) — the same UX
  the hobbyist repo shipped, minus the guessable yes/no state machine;
- lock_workstation is reversible (the user unlocks) but still WRITE: it
  immediately takes over the session surface;
- subprocess-only: no shell, list argv, output captured, a failure is a
  clean ToolResult.fail — raw exceptions never reach the model (Kill List).
"""

from __future__ import annotations

import logging
import subprocess
import sys

log = logging.getLogger(__name__)

__all__ = ["PowerError", "run_power_verb"]

_POWER_VERBS = ("lock_workstation", "shutdown", "restart", "sign_out", "abort")
_MIN_DELAY_S = 5      # keep the recoverable grace window (abort works inside it)
_MAX_DELAY_S = 600


class PowerError(RuntimeError):
    """Raised for unsupported verbs / unsupported platforms / command failure."""


def _windows_only() -> None:
    if sys.platform != "win32":  # pragma: no cover — CI is Windows
        raise PowerError("power verbs are Windows-only")


def _run(argv: list[str]) -> str:
    try:
        proc = subprocess.run(argv, check=False, capture_output=True, text=True,
                              timeout=10.0)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PowerError(f"power command failed: {type(exc).__name__}") from exc
    if proc.returncode != 0:
        # shutdown.exe prints its reason (e.g. "abort failed: no pending") on stderr
        detail = (proc.stderr or proc.stdout or "").strip()
        raise PowerError(detail[:200] or f"exit code {proc.returncode}")
    return (proc.stdout or "").strip()


def run_power_verb(verb: str, *, delay_s: int = 5) -> dict[str, object]:
    """Execute one power verb. Returns a structured result; raises PowerError
    (never raw exceptions) for anything the caller should see as a failure."""
    _windows_only()
    verb = (verb or "").strip().lower()
    if verb not in _POWER_VERBS:
        raise PowerError(
            f"unsupported verb {verb!r} — supported: {', '.join(_POWER_VERBS)}")
    if verb == "lock_workstation":
        _run(["rundll32.exe", "user32.dll,LockWorkStation"])
        return {"verb": verb, "detail": "workstation locked"}
    if verb == "abort":
        _run(["shutdown.exe", "/a"])
        return {"verb": verb, "detail": "pending shutdown/restart aborted"}
    delay = max(_MIN_DELAY_S, min(_MAX_DELAY_S, int(delay_s)))
    if verb == "shutdown":
        _run(["shutdown.exe", "/s", "/t", str(delay)])
        detail = f"shutting down in {delay}s (abortable via the abort verb)"
    elif verb == "restart":
        _run(["shutdown.exe", "/r", "/t", str(delay)])
        detail = f"restarting in {delay}s (abortable via the abort verb)"
    else:  # sign_out
        _run(["shutdown.exe", "/l"])
        detail = "signing out"
    log.info("power verb: %s (delay %ss)", verb, delay)
    return {"verb": verb, "delay_s": delay, "detail": detail}
