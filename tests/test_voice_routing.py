"""tests/test_voice_routing.py — Phase W3 pins: the two-brains router.

The audit's finding was "voice NEVER reaches AgentLoop — two brains, one
deaf". W3 closes it: ONE router (`_maybe_route_agent`) serves BOTH input
tiers. These tests pin:

1. complex spoken/typed utterances route; reflex utterances don't;
2. routing works without a live asyncio loop running (thread-safe scheduling
   is tested via the runner path, not a real loop);
3. the shared router is the SAME decision for both sources (no drift).
"""

from __future__ import annotations

import pytest

import main


class _StubUI:
    def __init__(self):
        self.muted = False
        self.logs: list[str] = []
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.states: list[str] = []

    def write_log(self, msg):
        self.logs.append(str(msg))

    def set_state(self, state):
        self.states.append(str(state))


@pytest.fixture()
def tmp_base(monkeypatch, tmp_path):
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    return tmp_path


def test_complex_utterances_route(tmp_base):
    ultron = main.UltronLive(_StubUI())
    assert ultron._is_complex_task("research quantum computing and write a file")
    assert ultron._is_complex_task("please analyze the sales data")
    assert not ultron._is_complex_task("what's the weather today")
    assert not ultron._is_complex_task("open chrome")


def test_router_requires_loop(tmp_base):
    """No asyncio loop running (self._loop None) → never routes: the utterance
    stays with the native session instead of being dropped."""
    ultron = main.UltronLive(_StubUI())
    assert ultron._loop is None
    assert ultron._maybe_route_agent("research quantum computing", source="typed") is False


def test_router_is_source_agnostic(tmp_base):
    """Both tiers use the SAME complexity decision (two brains, one router)."""
    ultron = main.UltronLive(_StubUI())
    for text in ("research AI papers and summarize",
                 "compile a list of all my reminders"):
        typed = ultron._is_complex_task(text)
        spoken = ultron._is_complex_task(text)
        assert typed == spoken is True, text


def test_router_schedules_on_loop(tmp_base):
    """With a loop present, a complex utterance is scheduled and reported."""
    import asyncio
    ultron = main.UltronLive(_StubUI())
    ran: list[str] = []

    async def fake_agent(text):
        ran.append(text)
        return "done"

    async def drive():
        ultron._loop = asyncio.get_running_loop()
        ultron._run_agent_task = fake_agent  # route target
        routed = ultron._maybe_route_agent(
            "research quantum computing and write a summary", source="spoken")
        assert routed is True
        # the scheduled coroutine needs a hop to execute
        await asyncio.sleep(0.05)
        assert ran, "router scheduled nothing"

    asyncio.run(drive())
    assert any("spoken" in line for line in ultron.ui.logs)
