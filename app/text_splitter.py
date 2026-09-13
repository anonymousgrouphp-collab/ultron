"""app/text_splitter.py — stream-safe sentence splitter for TTS.

Python port of kokoro.js `TextSplitterStream` (hexgrad/kokoro, Apache-2.0),
chosen in docs/research/10_tts_research.md (adopt item A2). Designed for
streaming LLM output: push text deltas, pull complete sentences as soon as
they are unambiguous. Guards: abbreviations (Dr./a.m./etc.), URLs and
emails, middle initials ("J. R. R. Tolkien"), quote/bracket nesting,
decimals and currency ("$4.99"), scientific notation, numbered lists,
lowercase lookahead ("e.g. word"), standalone ellipsis.

Same-module usage (batch):      split(text) -> list[str]
Streaming (deltas from a model): feeder = SentenceFeeder(); feeder.feed(delta) -> list[str]
                                 ... feeder.flush() -> list[str]   (call when stream ends)
"""

from __future__ import annotations

import re

# ".!?…" + CJK terminators; newlines only when include_newlines.
_SENTENCE_TERMINATORS = ".!?…。？！"
_TRAILING_CHARS = "\"')]}」』"  # attach to the terminator (closing quotes/brackets)

_ABBREVIATIONS = {
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "sgt", "col", "gen", "rep",
    "sen", "gov", "lt", "maj", "capt", "st", "mt", "etc", "co", "inc", "ltd",
    "dept", "vs", "p", "pg", "jan", "feb", "mar", "apr", "jun", "jul", "aug",
    "sep", "sept", "oct", "nov", "dec", "sun", "mon", "tu", "tue", "tues",
    "wed", "th", "thu", "thur", "thurs", "fri", "sat",
}

# closing char -> its opening char
_MATCHING = {
    ")": "(", "]": "[", "}": "{", "》": "《", "〉": "〈", "›": "‹", "»": "«",
    "〉": "〈", "」": "「", "』": "『", "〕": "〔", "】": "【",
}
_OPENING = set(_MATCHING.values())

_NUMBERED_LIST = re.compile(r"(?:^|\n)\d+$")
_INITIALS = re.compile(r"^(?:[A-Za-z]\.)+$")
_URL_OR_EMAIL = re.compile(r"https?[,:]//")


def _is_sentence_terminator(c: str, include_newlines: bool = True) -> bool:
    return c in _SENTENCE_TERMINATORS or (include_newlines and c == "\n")


def _is_trailing_char(c: str) -> bool:
    return c in _TRAILING_CHARS


def _is_abbreviation(token: str) -> bool:
    token = re.sub(r"['’]s$", "", token, flags=re.IGNORECASE)
    token = re.sub(r"\.+$", "", token)
    return token.lower() in _ABBREVIATIONS


def _update_stack(c: str, stack: list[str], i: int, buffer: str) -> None:
    """Track quote/bracket nesting; mid-word apostrophes are ignored."""
    if c in ('"', "'"):
        if (
            c == "'"
            and 0 < i < len(buffer) - 1
            and buffer[i - 1].isalpha()
            and buffer[i + 1].isalpha()
        ):
            return
        if stack and stack[-1] == c:
            stack.pop()
        else:
            stack.append(c)
        return
    if c in _OPENING:
        stack.append(c)
        return
    expected = _MATCHING.get(c)
    if expected and stack and stack[-1] == expected:
        stack.pop()


def _scan_boundary(buffer: str, idx: int) -> tuple[int, int]:
    """Consume contiguous terminators, then trailing closers, then whitespace.
    Returns (end, next_non_space)."""
    n = len(buffer)
    end = idx
    while end + 1 < n and _is_sentence_terminator(buffer[end + 1], False):
        end += 1
    while end + 1 < n and _is_trailing_char(buffer[end + 1]):
        end += 1
    nxt = end + 1
    while nxt < n and buffer[nxt].isspace():
        nxt += 1
    return end, nxt


class TextSplitterStream:
    """Accumulates text deltas; emits complete, unambiguous sentences."""

    def __init__(self) -> None:
        self._buffer = ""
        self._ready: list[str] = []
        self._closed = False

    def push(self, *texts: str) -> None:
        for text in texts:
            if not text:
                continue
            self._buffer += text
        self._process()

    def close(self) -> None:
        """Flush remaining buffer as a final sentence."""
        self._closed = True
        remainder = self._buffer.strip()
        if remainder:
            self._ready.append(remainder)
        self._buffer = ""

    def pop_ready(self) -> list[str]:
        """Return (and clear) sentences completed so far."""
        out, self._ready = self._ready, []
        return out

    def _process(self) -> None:
        buffer = self._buffer
        n = len(buffer)
        sentence_start = 0
        i = 0
        stack: list[str] = []

        while i < n:
            c = buffer[i]
            _update_stack(c, stack, i, buffer)

            if stack or not _is_sentence_terminator(c):
                i += 1
                continue

            segment = buffer[sentence_start:i]
            # numbered list ("1. First item") — not a sentence end
            if _NUMBERED_LIST.search(segment):
                i += 1
                continue

            end, next_non_space = _scan_boundary(buffer, i)
            # mid-token period ("$9.99", "3.2") — keep scanning
            if i == next_non_space - 1 and c != "\n":
                i += 1
                continue
            # boundary is the last char seen so far — wait for more text
            if next_non_space >= n and not self._closed:
                break

            token_start = i - 1
            while token_start >= 0 and not buffer[token_start].isspace():
                token_start -= 1
            token_start = max(sentence_start, token_start + 1)
            token_end = token_start
            while token_end < n and not buffer[token_end].isspace():
                token_end += 1
            token = buffer[token_start:token_end]
            if not token:
                i += 1
                continue

            # URL / email protection
            if (_URL_OR_EMAIL.search(token) or "@" in token) and not _is_sentence_terminator(token[-1], False):
                i = token_start + len(token)
                continue
            if _is_abbreviation(token):
                i += 1
                continue
            # "J. R. R." followed by a capitalized surname
            if _INITIALS.match(token) and next_non_space < n and buffer[next_non_space].isupper():
                i += 1
                continue
            # "e.g. word" / sentence-folded period
            if c == "." and next_non_space < n and buffer[next_non_space].islower():
                i += 1
                continue

            sentence = buffer[sentence_start : end + 1].strip()
            if sentence in ("...", "…"):
                i += 1
                continue
            if sentence:
                self._ready.append(sentence)
            i = sentence_start = end + 1

        self._buffer = buffer[sentence_start:]


def split(text: str) -> list[str]:
    """Batch split: returns all sentences in `text`."""
    s = TextSplitterStream()
    s.push(text)
    s.close()
    return s.pop_ready()


class SentenceFeeder:
    """Convenience wrapper for streaming sources (LLM deltas)."""

    def __init__(self) -> None:
        self._s = TextSplitterStream()

    def feed(self, delta: str) -> list[str]:
        self._s.push(delta)
        return self._s.pop_ready()

    def flush(self) -> list[str]:
        self._s.close()
        return self._s.pop_ready()
