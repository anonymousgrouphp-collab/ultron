"""tests/test_text_splitter.py — port of kokoro.js tests/splitting.test.js cases.

The splitter (app/text_splitter.py) is adopt item A2 of
docs/research/10_tts_research.md; these cases mirror the upstream test suite
plus streaming-feed behavior.
"""

import pytest

from app.text_splitter import SentenceFeeder, TextSplitterStream, split

CASES = [
    ("Basic sentence splitting",
     "This is a test. This is another test.",
     ["This is a test.", "This is another test."]),
    ("Sentence with dash (em dash)",
     "This is a test — yes, it is.",
     ["This is a test — yes, it is."]),
    ("Sentences with quoted speech",
     'She said, "Hello there. How are you?". I replied, "I\'m fine."',
     ['She said, "Hello there. How are you?".', 'I replied, "I\'m fine."']),
    ("Sentences with abbreviations",
     "Dr. Smith is here. At 10 a.m. I saw him.",
     ["Dr. Smith is here.", "At 10 a.m. I saw him."]),
    ("Advanced sentences with abbreviations",
     "I went to Dr. Smith this morning at 10 a.m. and said hi.",
     ["I went to Dr. Smith this morning at 10 a.m. and said hi."]),
    ("Abbreviations with possessive",
     "The Dr.'s office.",
     ["The Dr.'s office."]),
    ("Ellipses in sentences",
     "Wait... what just happened? I don't understand...",
     ["Wait... what just happened?", "I don't understand..."]),
    ("Sentences with numbers and decimals",
     "The price is $4.99. Do you want to buy it?",
     ["The price is $4.99.", "Do you want to buy it?"]),
    ("Sentences starting and ending with numbers",
     "10 people died in 2025. 20 people died in 2026.",
     ["10 people died in 2025.", "20 people died in 2026."]),
    ("Sentences with scientific notation",
     "The star is 3.2×10^4 light-years away.",
     ["The star is 3.2×10^4 light-years away."]),
    ("Sentences with multiple punctuation marks",
     "What?! Are you serious?! This is crazy...",
     ["What?!", "Are you serious?!", "This is crazy..."]),
    ("Sentences with parentheses",
     "This is an example (which is quite useful). Do you agree?",
     ["This is an example (which is quite useful).", "Do you agree?"]),
    ("Nested sentences with parentheses",
     "This is an example (This is pretty cool. Another sentence). Do you agree?",
     ["This is an example (This is pretty cool. Another sentence).",
      "Do you agree?"]),
    ("Sentences with newlines",
     "First sentence.\nSecond sentence.\nThird sentence.",
     ["First sentence.", "Second sentence.", "Third sentence."]),
    ("Sentences with emojis",
     "I love pizza! 🍕 Do you? 😊",
     ["I love pizza!", "🍕 Do you?", "😊"]),
]


@pytest.mark.parametrize(("name", "text", "target"), CASES, ids=[c[0] for c in CASES])
def test_batch_cases(name: str, text: str, target: list[str]) -> None:
    assert split(text) == target


def test_url_and_email_not_split() -> None:
    text = "Visit https://example.com/x. Then mail me at a.b@c.io please."
    out = split(text)
    assert any("https://example.com/x" in s for s in out)
    assert any("a.b@c.io" in s for s in out)


def test_numbered_list_not_split() -> None:
    # "1." must not be separated from its text (the numbered-list guard)…
    assert split("1. First item") == ["1. First item"]
    # …but a newline is still a valid chunk boundary for TTS streaming.
    assert split("1. First item\n2. Second item") == [
        "1. First item",
        "2. Second item",
    ]


def test_streaming_pushes_as_they_complete() -> None:
    s = TextSplitterStream()
    s.push("Short one. The quick brown")
    assert s.pop_ready() == ["Short one."]
    s.push(" fox jumps. Done.")
    # the trailing "Done." waits for more text (ambiguous end), like upstream
    assert s.pop_ready() == ["The quick brown fox jumps."]
    s.close()
    assert s.pop_ready() == ["Done."]


def test_streaming_feed_equals_batch() -> None:
    text = "The price is $4.99. Do you want to buy it? Yes sir."
    feeder = SentenceFeeder()
    out: list[str] = []
    # feed in awkward deltas that cut inside guarded tokens
    for i in range(0, len(text), 5):
        out.extend(feeder.feed(text[i : i + 5]))
    out.extend(feeder.flush())
    assert out == split(text)


def test_empty_and_whitespace() -> None:
    assert split("") == []
    assert split("   \n  ") == []
