"""kernel/jsonable.py — shared JSON-safety normalization (stdlib-only).

Kernel payloads that cross durable or protocol boundaries (checkpoints, job
results, MCP responses) must be JSON-safe without ever crashing on odd data.
Enums render as their value, dataclasses as dicts, mappings/lists recursively,
and anything else as its repr string — a last resort that prefers information
loss over a raised exception.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping
from enum import Enum
from typing import Any

__all__ = ["jsonable"]


def jsonable(value: Any) -> Any:
    """Reduce a payload to JSON-safe types (HTTP/model/disk safe)."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Enum):
        return jsonable(value.value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [jsonable(item) for item in value]
    return repr(value)  # last resort: a string rendering, never a crash
