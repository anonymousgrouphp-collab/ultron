"""kernel/orchestrator/plans.py — P2-C: plans, steps, and arg templates.

A Plan is a list of Steps (plain data, fully JSON-able — it lives inside the
job payload). Step kinds are pluggable; the runner ships "tool" and "agent"
and accepts custom kinds (e.g. "briefing").

Step specs may reference earlier outputs with "{step_id.path.to.value}"
templates — resolved by :func:`resolve_template` against the outputs dict
(dot-path segments index dicts by key and lists by position). A missing path
raises a clean ValueError: the step fails, the job fails, the model/operator
sees WHY.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Mapping

_TEMPLATE = re.compile(r"\{([A-Za-z0-9_.\-]+)\}")

__all__ = ["Step", "steps_from_payload", "resolve_template"]


@dataclass(frozen=True)
class Step:
    """One unit of a plan. `spec` content depends on the step kind."""

    id: str
    kind: str
    spec: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.id or not re.fullmatch(r"[A-Za-z0-9_\-]+", self.id):
            raise ValueError(f"step id must be a short slug, got {self.id!r}")
        if not self.kind:
            raise ValueError("step kind is required")


def steps_from_payload(payload: Mapping[str, Any]) -> tuple[Step, ...]:
    """Parse the job payload's plan. Unknown step kinds are kept — the runner
    fails the job cleanly if no handler is registered for them."""
    raw = payload.get("plan")
    if not raw or not isinstance(raw, (list, tuple)):
        raise ValueError("plan jobs need a non-empty 'plan' list in the payload")
    steps = []
    for item in raw:
        if not isinstance(item, Mapping) or "id" not in item or "kind" not in item:
            raise ValueError("every plan step needs at least 'id' and 'kind'")
        steps.append(Step(id=str(item["id"]), kind=str(item["kind"]),
                          spec=dict(item.get("spec") or {})))
    ids = [s.id for s in steps]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate step ids in plan: {ids}")
    return tuple(steps)


def _lookup(path: str, outputs: Mapping[str, Any]) -> Any:
    node: Any = outputs
    for segment in path.split("."):
        if isinstance(node, Mapping) and segment in node:
            node = node[segment]
        elif isinstance(node, (list, tuple)):
            try:
                node = node[int(segment)]
            except (ValueError, IndexError):
                raise ValueError(
                    f"template path {path!r} is not resolvable "
                    f"(bad list index {segment!r})") from None
        else:
            raise ValueError(
                f"template path {path!r} is not resolvable at {segment!r}")
    return node


def resolve_template(value: Any, outputs: Mapping[str, Any]) -> Any:
    """Substitute {step.path} tokens in strings; recurse through dicts/lists.
    Non-string leaves pass through unchanged."""
    if isinstance(value, str):
        def sub(match: re.Match[str]) -> str:
            resolved = _lookup(match.group(1), outputs)
            if isinstance(resolved, str):
                return resolved
            return json.dumps(resolved)
        return _TEMPLATE.sub(sub, value)
    if isinstance(value, Mapping):
        return {k: resolve_template(v, outputs) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [resolve_template(v, outputs) for v in value]
    return value
