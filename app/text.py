"""app/text.py — shared transcript-cleaning helpers (Phase W0).

Moved verbatim from main.py so app/ modules can use them without a
circular import (main imports app/, so app/ must not import main).
main.py re-exports `_clean_transcript` for its callers.
"""

from __future__ import annotations

import re

_CTRL_RE = re.compile(r"<ctrl\d+>", re.IGNORECASE)


def clean_transcript(text: str) -> str:
    text = _CTRL_RE.sub("", text)
    text = re.sub(r"[\x00-\x08\x0b-\x1f]", "", text)
    return text.strip()
