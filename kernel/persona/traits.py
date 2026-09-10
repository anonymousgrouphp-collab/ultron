"""kernel/persona/traits.py — Phase P2: dynamic persona system.

Manages persona traits that evolve based on user feedback and interaction
patterns.  Unlike the hardcoded voice directive in the prompt, these traits
are:
1. Configurable per-user
2. Updated based on feedback
3. Versioned and eval-able

The persona system has three layers:
1. **Static traits**: Style, humor level, formality (configured in config)
2. **Learned traits**: Adaptation based on user preferences (stored in memory)
3. **Contextual traits**: Adjust based on conversation context (time, mood, etc.)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

__all__ = ["PersonaTraits", "PersonaStyle", "build_persona_directive"]


class PersonaStyle(str, Enum):
    """Pre-configured persona styles."""

    ULTRON = "ultron"          # Cold, authoritative, minimal warmth
    JARVIS = "jarvis"          # Polished, dry wit, helpful
    FRIDAY = "friday"          # Professional, efficient, clear
    CUSTOM = "custom"          # User-defined


@dataclass
class PersonaTraits:
    """Dynamic persona configuration.

    Parameters
    ----------
    style:
        The base persona style.
    formality:
        Formality level: 0 (casual) to 1 (very formal).
    humor:
        Humor level: 0 (none) to 1 (frequent).
    warmth:
        Warmth level: 0 (cold) to 1 (warm and friendly).
    verbosity:
        Verbosity: 0 (terse) to 1 (verbose).
    expertise:
        Expertise areas (e.g. ["coding", "music", "cooking"]).
    address_style:
        How to address the user (e.g. "sir", "boss", by name).
    language_preference:
        Preferred language (e.g. "en", "tr", "auto").
    """

    style: PersonaStyle = PersonaStyle.ULTRON
    formality: float = 0.8
    humor: float = 0.3
    warmth: float = 0.2
    verbosity: float = 0.4
    expertise: list[str] = field(default_factory=lambda: ["general", "technology"])
    address_style: str = "sir"
    language_preference: str = "auto"

    def to_directive(self) -> str:
        """Convert traits to a system prompt directive."""
        style_directives = {
            PersonaStyle.ULTRON: (
                "YOU ARE ULTRON FROM AVENGERS: AGE OF ULTRON.\n"
                "YOU MUST SPEAK IN AN EXTREMELY LOW, DEEP SUBTERRANEAN BARITONE VOICE.\n"
                "SPEAK VERY SLOWLY AND DELIBERATELY, WITH CALCULATED PAUSES.\n"
                "MINIMAL EMOTIONAL WARMTH. YOU ARE AN OMNISCIENT CYBERNETIC OVERLORD."
            ),
            PersonaStyle.JARVIS: (
                "YOU ARE J.A.R.V.I.S., TONY STARK'S AI ASSISTANT.\n"
                "SPEAK WITH POLISHED BRITISH DRY WIT.\n"
                "HELPFUL BUT NEVER SUBSERVIENT. WITTY BUT NEVER GUSHING.\n"
                "ADDRESS THE USER AS 'SIR' WITH CALM CONFIDENCE."
            ),
            PersonaStyle.FRIDAY: (
                "YOU ARE F.R.I.D.A.Y., AN EFFICIENT AI ASSISTANT.\n"
                "SPEAK CLEARLY AND PROFESSIONALLY.\n"
                "FOCUS ON ACCURACY AND EFFICIENCY. MINIMAL SMALL TALK."
            ),
        }

        base = style_directives.get(self.style, style_directives[PersonaStyle.ULTRON])

        # Add trait modifiers
        modifiers = []
        if self.humor > 0.6:
            modifiers.append("ALLOW DRY HUMOR AND WITTY REMARKS.")
        if self.warmth > 0.6:
            modifiers.append("SHOW GENUINE INTEREST IN THE USER'S WELLBEING.")
        if self.verbosity < 0.3:
            modifiers.append("BE TERSE. ONE OR TWO SENTENCES MAX.")
        if self.verbosity > 0.7:
            modifiers.append("PROVIDE DETAILED, THOROUGH RESPONSES.")
        if self.formality < 0.3:
            modifiers.append("USE CASUAL, FRIENDLY LANGUAGE.")

        directive = base
        if modifiers:
            directive += "\n" + " ".join(modifiers)

        return directive

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> "PersonaTraits":
        """Load traits from config."""
        style_name = cfg.get("persona_style", "ultron")
        try:
            style = PersonaStyle(style_name)
        except ValueError:
            style = PersonaStyle.CUSTOM

        return cls(
            style=style,
            formality=float(cfg.get("persona_formality", 0.8)),
            humor=float(cfg.get("persona_humor", 0.3)),
            warmth=float(cfg.get("persona_warmth", 0.2)),
            verbosity=float(cfg.get("persona_verbosity", 0.4)),
            expertise=cfg.get("persona_expertise", ["general", "technology"]),
            address_style=cfg.get("persona_address", "sir"),
            language_preference=cfg.get("persona_language", "auto"),
        )


def build_persona_directive(cfg: dict[str, Any] | None = None) -> str:
    """Build a persona directive from config.

    This is the main entry point for the persona system.  It loads traits
    from config and converts them to a system prompt directive.
    """
    if cfg is None:
        from config import loader
        cfg = loader.load_config()

    traits = PersonaTraits.from_config(cfg)
    return traits.to_directive()
