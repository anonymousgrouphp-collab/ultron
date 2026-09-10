"""kernel/persona/prompt_assembler.py — Phase O3: prompt assembly with auto-RAG.

Builds the system prompt for the voice session by composing:
1. Voice/personality directives
2. Identity context (name, user name, address style)
3. Time context
4. **Auto-retrieved memory context** (the key O3 addition)
5. The base system prompt (from core/prompt.txt)

This replaces the scattered prompt-building logic that was in main.py's
``_build_config`` method, centralizing it in the kernel package.

Auto-RAG design
---------------
At prompt-build time, the assembler:
1. Takes a summary of the current conversation (or empty for new sessions)
2. Queries ``MemoryEngine.search()`` with key phrases extracted from context
3. Injects the top-K most relevant facts into the system prompt
4. Each fact includes its source category and confidence

This means the model always has relevant memory context WITHOUT having to
explicitly call ``memory_search`` — the single biggest reliability improvement
for a personal assistant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

__all__ = ["PromptAssembler", "AssembledPrompt"]


@dataclass(frozen=True)
class AssembledPrompt:
    """Result of prompt assembly."""

    system_instruction: str
    memory_facts: tuple[str, ...] = ()
    memory_count: int = 0


@dataclass
class PromptAssembler:
    """Builds system prompts with auto-retrieved memory context.

    Parameters
    ----------
    memory:
        A ``MemoryEngine`` instance (or ``None`` to skip auto-RAG).
    base_prompt:
        The base system prompt text (from ``core/prompt.txt``).
    asst_name:
        Assistant name (default ``"ULTRON"``).
    user_name:
        User name (optional).
    memory_k:
        Number of memory facts to retrieve for context (default 8).
    """

    memory: Any = None  # MemoryEngine | None
    base_prompt: str = ""
    asst_name: str = "ULTRON"
    user_name: str = ""
    memory_k: int = 8

    def assemble(
        self,
        conversation_summary: str = "",
        extra_sections: list[str] | None = None,
    ) -> AssembledPrompt:
        """Build the complete system prompt.

        Parameters
        ----------
        conversation_summary:
            Summary of recent conversation turns (for memory retrieval query).
        extra_sections:
            Additional sections to include (e.g. proactive context).
        """
        parts: list[str] = []

        # 1. Voice/personality directive
        parts.append(self._voice_directive())

        # 2. Time context
        parts.append(self._time_context())

        # 3. Identity context
        parts.append(self._identity_context())

        # 4. Auto-RAG memory context
        memory_facts = self._retrieve_memory(conversation_summary)
        if memory_facts:
            parts.append(self._memory_section(memory_facts))

        # 5. Extra sections (proactive, etc.)
        if extra_sections:
            for section in extra_sections:
                parts.append(section)

        # 6. Base system prompt
        if self.base_prompt:
            parts.append(self.base_prompt)

        return AssembledPrompt(
            system_instruction="\n\n".join(parts),
            memory_facts=tuple(memory_facts),
            memory_count=len(memory_facts),
        )

    def _voice_directive(self) -> str:
        """Build voice directive from dynamic persona traits (Phase P2)."""
        from kernel.persona.traits import build_persona_directive
        return build_persona_directive()

    def _time_context(self) -> str:
        now = datetime.now()
        time_str = now.strftime("%A, %B %d, %Y — %I:%M %p")
        return (
            f"[CURRENT DATE & TIME]\n"
            f"Right now it is: {time_str}\n"
            f"Use this to calculate exact times for reminders.\n"
        )

    def _identity_context(self) -> str:
        if self.user_name:
            addr = f"ADDRESS: Always call the user '{self.user_name}'."
        else:
            addr = (
                'ADDRESS: When speaking Turkish → always say "efendim". '
                'When speaking English → say "sir". Never mix languages.'
            )
        return (
            f"[IDENTITY]\n"
            f"Your name is {self.asst_name}. "
            f"Always refer to yourself as {self.asst_name}.\n"
            f"{addr}\n"
        )

    def _retrieve_memory(self, query: str) -> list[str]:
        """Auto-RAG: retrieve relevant memory facts at prompt-build time."""
        if self.memory is None:
            return []

        # Build a query from conversation context or use defaults
        search_query = query.strip() if query else "user preferences and history"

        try:
            results = self.memory.search(search_query, k=self.memory_k)
        except Exception:
            # Memory retrieval failure must never break prompt assembly
            return []

        facts: list[str] = []
        for r in results:
            category = getattr(r, "category", "general")
            content = getattr(r, "content", "")
            if content:
                facts.append(f"[{category}] {content}")
        return facts

    def _memory_section(self, facts: list[str]) -> str:
        """Format retrieved memory facts for injection into the prompt."""
        lines = ["[RELEVANT MEMORY — auto-retrieved at prompt build time]"]
        for i, fact in enumerate(facts, 1):
            lines.append(f"  {i}. {fact}")
        lines.append(
            "\nUse this memory to personalize your responses. "
            "If the user asks about something in memory, reference it naturally."
        )
        return "\n".join(lines)
