"""kernel/memory — P1-D engine + P3-A write policy & consolidation (the moat).

SQLite WAL + five stores (research/04 §10), FTS5 ⊕ vector recall fused by RRF,
injectable local embedders (hashing now, BGE-M3 optional), and the scripted
long_term.json migration. `register_memory_tools` exposes memory_search /
memory_page as kernel tools (RiskClass.READ) so the agent loop can recall.
P3-A adds the write path: the judge (policy.propose_ops over the gateway) and
the idle-time Consolidator (extract → decay → reflection, tombstones+undo).
"""

from kernel.memory.consolidation import (
    REFLECT_SCHEMA,
    ConsolidationReport,
    Consolidator,
)
from kernel.memory.engine import (
    MemoryEngine,
    ProcedureHit,
    ProcedureRecord,
    SearchHit,
)
from kernel.memory.evals import (
    RecallReport,
    build_eval_corpus,
    recall_gate_ok,
    run_recall_eval,
    seed_eval_engine,
)
from kernel.memory.embedders import (
    BGEM3Embedder,
    Embedder,
    HashingEmbedder,
    make_embedder,
)
from kernel.memory.improve import (
    ImproveOutcome,
    SkillCaptureListener,
    capture_from_run,
    improve_run,
    task_slug,
)
from kernel.memory.migration import migrate_long_term_json
from kernel.memory.policy import (
    OPS_SCHEMA,
    MemoryOp,
    ProposedOps,
    WritePolicy,
    parse_ops_json,
    propose_ops,
)
from kernel.memory.procedural import (
    capture_procedure,
    prime_messages,
    recall_similar,
    record_outcome,
    register_procedural_tools,
    replay,
    should_prune,
)
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
    "ConsolidationReport",
    "Consolidator",
    "Embedder",
    "HashingEmbedder",
    "ImproveOutcome",
    "MemoryEngine",
    "MemoryOp",
    "OPS_SCHEMA",
    "ProcedureHit",
    "ProcedureRecord",
    "ProposedOps",
    "REFLECT_SCHEMA",
    "RecallReport",
    "SearchHit",
    "SkillCaptureListener",
    "WritePolicy",
    "build_eval_corpus",
    "capture_from_run",
    "capture_procedure",
    "improve_run",
    "make_embedder",
    "migrate_long_term_json",
    "parse_ops_json",
    "prime_messages",
    "propose_ops",
    "recall_gate_ok",
    "recall_similar",
    "record_outcome",
    "register_memory_tools",
    "register_procedural_tools",
    "replay",
    "run_recall_eval",
    "seed_eval_engine",
    "should_prune",
    "task_slug",
]
