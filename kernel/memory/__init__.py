"""kernel/memory — P1-D: the memory engine v0 (the moat's foundation).

SQLite WAL + five stores (research/04 §10), FTS5 ⊕ vector recall fused by RRF,
injectable local embedders (hashing now, BGE-M3 optional), and the scripted
long_term.json migration. `register_memory_tools` exposes memory_search /
memory_page as kernel tools (RiskClass.READ) so the agent loop can recall.
"""

from kernel.memory.engine import MemoryEngine, SearchHit
from kernel.memory.embedders import (
    BGEM3Embedder,
    Embedder,
    HashingEmbedder,
    make_embedder,
)
from kernel.memory.migration import migrate_long_term_json
from kernel.tools import ToolRegistry
from kernel.types import RiskClass


def register_memory_tools(registry: ToolRegistry, engine: MemoryEngine) -> None:
    """Expose recall as kernel tools. Both are READ-risk: memory never writes
    from a model request — writes go through the engine/consolidation APIs."""

    @registry.tool(
        name="memory_search",
        description="Search ULTRON's long-term memory for facts relevant to a "
                    "query. Returns the top matches with content and score.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "what to recall, in natural language"},
                "k": {"type": "integer",
                      "description": "maximum number of facts to return"},
            },
            "required": ["query"],
        },
        risk=RiskClass.READ,
    )
    def memory_search(call):
        hits = engine.search(
            str(call.args.get("query", "")),
            k=max(1, min(int(call.args.get("k", 8)), 25)),
        )
        return [
            {"id": hit.id, "content": hit.content, "entity": hit.entity,
             "topic": hit.topic, "score": round(hit.score, 4)}
            for hit in hits
        ]

    @registry.tool(
        name="memory_page",
        description="List long-term memory facts, newest first, optionally "
                    "filtered by entity (e.g. a person, project, or category).",
        parameters={
            "type": "object",
            "properties": {
                "entity": {"type": "string",
                           "description": "optional entity filter"},
                "limit": {"type": "integer",
                          "description": "maximum rows to return"},
            },
            "required": [],
        },
        risk=RiskClass.READ,
    )
    def memory_page(call):
        entity = call.args.get("entity")
        limit = max(1, min(int(call.args.get("limit", 20)), 100))
        rows = engine.page(
            entity=str(entity) if entity else None, limit=limit,
        )
        return [
            {"id": row.id, "content": row.content, "entity": row.entity,
             "topic": row.topic}
            for row in rows
        ]


__all__ = [
    "BGEM3Embedder",
    "Embedder",
    "HashingEmbedder",
    "MemoryEngine",
    "SearchHit",
    "make_embedder",
    "migrate_long_term_json",
    "register_memory_tools",
]
