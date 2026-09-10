"""kernel/persona — Phase O3: prompt assembly and persona management.

Provides automatic context assembly for the voice session, including
memory retrieval at prompt-build time (auto-RAG).
"""

from kernel.persona.prompt_assembler import PromptAssembler

__all__ = ["PromptAssembler"]
