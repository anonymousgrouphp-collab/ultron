"""Boot smoke tests (Phase T2) — the CI gate the repo never had.

e59be6b shipped a composition-root break (LiveSession called without its
required `settings` argument) while CI stayed green, because no test ever
boots `UltronLive`. These tests are the fix:

1. `test_u ltronlive_constructs_hermetically` — the FULL real `__init__`
   (composition root: EventBus, MemoryEngine, LegacyToolRuntime + policy +
   audit, proactive engine, GUI/research runners, health/cost) runs against
   a tmp BASE_DIR and a stub UI. Any future constructor break goes red here.
2. `test_run_passes_gateway_settings_to_live_session` — drives
   `UltronLive.run()` through dashboard init, prompt assembly and
   LiveSession construction, and pins the exact contract e59be6b broke:
   `LiveSession` MUST be constructed with a `settings=` GatewaySettings.
   The fake session raises SystemExit at connect so run() exits before any
   audio task is created (hermetic: no network, no audio, no API).
3. `test_boot_break_goes_red` — negative control proving this file would
   have caught the e59be6b class of breakage: monkeypatch LiveSession to
   a factory that mimics the real signature (TypeError without settings);
   the run must NOT silently swallow it into a reconnect loop.

Read-only on src; everything external is stubbed. No Qt event loop (the UI
stub needs none — the same posture as test_characterization).
"""

import asyncio

import pytest

import main
from kernel.gateway.base import GatewaySettings


class _StubUI:
    """Minimal duck-typed stand-in for ui.UltronUI — only what the
    composition root touches at __init__/boot time."""

    def __init__(self):
        self.muted = False
        self.logs: list[str] = []
        self.on_text_command = None
        self.on_remote_clicked = None
        self.on_interrupt = None
        self.states: list[str] = []

    def write_log(self, msg: str) -> None:
        self.logs.append(str(msg))

    def set_state(self, state: str) -> None:
        self.states.append(str(state))


class _RecordingSession:
    """Fake LiveSession. Records constructor kwargs, then exits run()
    at connect() via SystemExit (the only exception run() lets escape
    its reconnect handler)."""

    calls: list[dict] = []

    def __init__(self, **kwargs):
        if "settings" not in kwargs:
            # Mirrors the real signature — this is the e59be6b breakage.
            raise TypeError(
                "_RecordingSession.__init__() missing 1 required "
                "positional argument: 'settings'"
            )
        self.kwargs = kwargs
        _RecordingSession.calls.append(kwargs)

    async def connect(self, config):
        raise SystemExit("smoke-test-connect-reached")

    async def close(self):
        return None


@pytest.fixture()
def tmp_base(monkeypatch, tmp_path):
    """Point main.BASE_DIR at a tmp dir so __init__ creates its SQLite
    files (memory/audit/proactive state) in the test sandbox."""
    monkeypatch.setattr(main, "BASE_DIR", tmp_path)
    return tmp_path


def test_ultronlive_constructs_hermetically(tmp_base):
    ultron = main.UltronLive(_StubUI())
    # Composition-root sanity: the kernel seam actually got wired.
    assert ultron._memory is not None
    assert ultron._bus is not None
    assert ultron._tool_runtime is not None
    assert ultron._consent_gate is not None
    assert ultron._gui_planner is not None
    assert ultron._research_runner is not None
    assert ultron._health_monitor is not None
    assert ultron._cost_tracker is not None
    # SQLite artifacts landed in the sandbox, not the real .ultron.
    assert (tmp_base / ".ultron" / "memory.sqlite3").exists()
    assert (tmp_base / ".ultron" / "audit.sqlite3").exists()


def test_run_passes_gateway_settings_to_live_session(
    monkeypatch, tmp_base, capsys
):
    _RecordingSession.calls.clear()
    monkeypatch.setattr(main, "LiveSession", _RecordingSession)
    monkeypatch.setattr(main, "_get_api_key", lambda: "test-key")
    # No dashboard server in the smoke run: patch the class run() imports.
    import dashboard.server as dashboard_server

    class _StubDashboard:
        def __init__(self):
            self.url = "http://127.0.0.1:0"
            self._command_queue = asyncio.Queue()

        def set_connect_callback(self, cb):
            pass

        async def serve(self):
            return None

        def get_url(self):
            return self.url

        async def broadcast(self, msg):
            return None

    monkeypatch.setattr(dashboard_server, "DashboardServer", _StubDashboard)

    ultron = main.UltronLive(_StubUI())

    # SystemExit is the agreed escape hatch from run()'s while-loop.
    with pytest.raises(SystemExit, match="smoke-test-connect-reached"):
        asyncio.run(ultron.run())

    assert len(_RecordingSession.calls) == 1, (
        "run() must attempt exactly one LiveSession construction before "
        "the connect SystemExit"
    )
    kwargs = _RecordingSession.calls[0]
    assert "settings" in kwargs, (
        "LiveSession constructed without settings= — the e59be6b breakage "
        "class is back"
    )
    assert isinstance(kwargs["settings"], GatewaySettings)
    assert kwargs.get("api_key") == "test-key"
    # run() reached prompt assembly + connect: proof the whole pre-audio
    # boot path executes.
    out = capsys.readouterr().out
    assert "[ULTRON] Connecting..." in out


def test_boot_break_goes_red(monkeypatch, tmp_base, capsys):
    """Negative control / characterization: a LiveSession factory with a
    broken signature (the e59be6b-era class — rejects `settings=`) must
    crash EVERY boot attempt loudly (printed per retry), never fail
    silently. Pre-T2 this failure mode was invisible to CI; this pin
    documents the runtime behavior and — with the smoke gate first in
    CI — makes the class of breakage red before merge."""
    attempts = {"n": 0}

    def _broken_session_factory(**kwargs):
        attempts["n"] += 1
        raise TypeError(
            "LiveSession.__init__() got an unexpected keyword "
            "argument 'settings' (simulated e59be6b-era signature)"
        )

    monkeypatch.setattr(main, "LiveSession", _broken_session_factory)
    monkeypatch.setattr(main, "_get_api_key", lambda: "test-key")
    import dashboard.server as dashboard_server

    class _StubDashboard:
        def __init__(self):
            self._command_queue = asyncio.Queue()

        def set_connect_callback(self, cb):
            pass

        async def serve(self):
            return None

        async def broadcast(self, msg):
            return None

    monkeypatch.setattr(dashboard_server, "DashboardServer", _StubDashboard)

    ultron = main.UltronLive(_StubUI())

    # run()'s reconnect loop retries broken sessions forever — bound it,
    # then assert the failure was both attempted AND visibly printed.
    async def _run_with_deadline():
        try:
            await asyncio.wait_for(ultron.run(), timeout=4.5)
        except (SystemExit, TimeoutError, asyncio.TimeoutError):
            pass

    asyncio.run(_run_with_deadline())
    assert attempts["n"] >= 2, "broken boot must keep retrying (per design)"
    out = capsys.readouterr().out
    assert "Error (TypeError)" in out, (
        "a broken composition root must at least print every failure — "
        "a silent retry loop is how e59be6b stayed unnoticed"
    )
