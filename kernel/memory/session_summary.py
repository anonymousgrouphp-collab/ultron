"""kernel/memory/session_summary.py — Phase I3: session summary generation.

Creates summaries of voice sessions for memory consolidation.  After each
conversation session ends, the summary is:
1. Stored as an episodic memory fact
2. Available for cross-session context injection at prompt-build time

This closes the loop between conversations: what was discussed in session N
is available as context in session N+1.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

__all__ = ["SessionSummary", "generate_session_summary"]


@dataclass(frozen=True)
class SessionSummary:
    """Summary of a voice session."""

    timestamp: str
    user_topics: list[str] = field(default_factory=list)
    tools_used: tuple[str, ...] = ()
    key_decisions: tuple[str, ...] = ()
    summary_text: str = ""


def generate_session_summary(
    *,
    user_messages: list[str],
    tool_calls: list[str],
    assistant_responses: list[str],
    memory: Any = None,
) -> SessionSummary:
    """Generate a summary from session data.

    Parameters
    ----------
    user_messages:
        List of user utterances during the session.
    tool_calls:
        List of tool names called during the session.
    assistant_responses:
        List of assistant responses.
    memory:
        Optional MemoryEngine to store the summary.

    Returns
    -------
    SessionSummary
        The generated summary.
    """
    timestamp = datetime.now().isoformat()

    # Extract topics from user messages (simple keyword extraction)
    topics = _extract_topics(user_messages)

    # Deduplicate tool calls
    tools_used = tuple(dict.fromkeys(tool_calls))

    # Generate summary text
    summary_parts = []
    if topics:
        summary_parts.append(f"Topics discussed: {', '.join(topics[:5])}")
    if tools_used:
        summary_parts.append(f"Tools used: {', '.join(tools_used)}")
    if user_messages:
        summary_parts.append(f"User messages: {len(user_messages)}")
    if assistant_responses:
        summary_parts.append(f"Assistant responses: {len(assistant_responses)}")

    summary_text = ". ".join(summary_parts) if summary_parts else "Empty session"

    summary = SessionSummary(
        timestamp=timestamp,
        user_topics=topics,
        tools_used=tools_used,
        summary_text=summary_text,
    )

    # Store in memory if available
    if memory is not None:
        try:
            memory.remember(
                content=summary_text,
                # Phase W4 fix: the real MemoryEngine.remember takes
                # `entity=`, not `category=` — the mismatch raised TypeError
                # that the except-swallow hid, so summaries never persisted
                # (the audit's "collected but never consumed" finding).
                entity="session_summary",
                source_ref=f"session:{timestamp}",
                importance=0.5,
            )
        except Exception:
            pass  # Memory storage failure must not break the session

    return summary


def _extract_topics(messages: list[str]) -> list[str]:
    """Extract key topics from user messages (simple approach)."""
    if not messages:
        return []

    # Combine all user messages
    text = " ".join(messages).lower()

    # Simple topic extraction: look for noun phrases after common verbs
    topics = []
    topic_markers = [
        "about", "regarding", "concerning", "tell me about",
        "what is", "what are", "how to", "how do", "research",
        "find", "search", "look up", "create", "save", "write",
    ]

    for marker in topic_markers:
        idx = text.find(marker)
        if idx >= 0:
            # Extract the next few words after the marker
            after = text[idx + len(marker):].strip()
            words = after.split()[:4]  # Take up to 4 words
            if words:
                topic = " ".join(words).strip(".,!?;:")
                if len(topic) > 3:
                    topics.append(topic)

    return list(dict.fromkeys(topics))[:5]  # Deduplicate, max 5
