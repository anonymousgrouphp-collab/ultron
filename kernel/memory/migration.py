"""kernel/memory/migration.py — P1-D: the one-shot long_term.json migration.

research/04 §10: "long_term.json → semantic_facts once, scripted, verified".
The legacy file is a nested dict of {category: {key: {value: ...}}} (the
save_memory shape) or {category: {key: str}}; this walker flattens every leaf
into one fact with entity=<top-level category> and topic=<path>, tagged
source_ref="long_term.json". Idempotence guard: facts already carrying the
same (source_ref, entity, topic, content) are skipped, so re-running the
migration never duplicates.

CLI:  py -3.13 -m kernel.memory.migration <long_term.json> [--db <path>]
Prints counts only — migrated memory content is user data and is never
printed.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from kernel.memory.engine import MemoryEngine

_SOURCE_REF = "long_term.json"


def _walk(node: Any, entity: str, topic: str,
          engine: MemoryEngine, seen: set[tuple[str, str, str]]) -> int:
    added = 0
    if isinstance(node, dict):
        # legacy save_memory shape: {key: {"value": "...", ...}} → one fact
        if set(node) == {"value"} or ("value" in node and len(node) <= 3):
            content = str(node["value"]).strip()
            if content and (entity, topic, content) not in seen:
                engine.remember(content, entity=entity, topic=topic,
                                source_ref=_SOURCE_REF)
                seen.add((entity, topic, content))
                return 1
            return 0
        for key, child in node.items():
            child_topic = f"{topic}/{key}" if topic else str(key)
            added += _walk(child, entity, child_topic, engine, seen)
        return added
    if isinstance(node, list):
        for index, child in enumerate(node):
            added += _walk(child, entity, f"{topic}/{index}", engine, seen)
        return added
    content = str(node).strip()
    if content and (entity, topic, content) not in seen:
        engine.remember(content, entity=entity, topic=topic,
                        source_ref=_SOURCE_REF)
        seen.add((entity, topic, content))
        return 1
    return added


def migrate_long_term_json(engine: MemoryEngine, path: str | Path) -> int:
    """Flatten a legacy long_term.json into semantic_facts. Returns the number
    of facts actually added (re-runs add 0)."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("long_term.json must be a JSON object at the top level")

    # idempotence: collect what this migration already contributed
    seen: set[tuple[str, str, str]] = {
        (fact["entity"], fact["topic"], fact["content"])
        for fact in engine.export_facts()
        if fact["source_ref"] == _SOURCE_REF
    }
    total = 0
    for category, subtree in data.items():
        total += _walk(subtree, str(category), "", engine, seen)
    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Migrate legacy long_term.json into the memory engine "
                    "(counts printed; memory content never printed).")
    parser.add_argument("source", help="path to long_term.json")
    parser.add_argument("--db", default=".ultron/memory.sqlite3",
                        help="memory engine database path")
    args = parser.parse_args()
    engine = MemoryEngine(args.db)
    try:
        added = migrate_long_term_json(engine, args.source)
        print(f"migration complete: {added} facts added, "
              f"{engine.count()} total (db: {args.db})")
    finally:
        engine.close()


if __name__ == "__main__":
    main()
