"""research/12 D2 tests: per-tool policy overrides + ConsentGate external resolver."""

import asyncio


from app.consent import ConsentGate
from kernel import RiskClass, ToolCall, ToolRegistry
from kernel.policy import Decision, Policy, PolicyEngine
from kernel.tools.base import Tool


def stub_registry():
    executed = []

    def make(name, risk):
        def handler(c):
            executed.append(c.name)
            return {"ran": c.name}
        return Tool(name=name, description=f"{name} stub",
                    parameters={"type": "object", "properties": {}},
                    handler=handler, risk=risk)

    reg = ToolRegistry()
    reg.register(make("write_file", RiskClass.WRITE))
    reg.register(make("weather_report", RiskClass.READ))
    return reg, executed


def call(name):
    return ToolCall(id=f"c-{name}", name=name, args={}, source="test")


# ----------------------------------------------------------------- tool rules

def test_01_tool_rule_allow_beats_ask_class():
    policy = Policy.default()
    tightened = Policy(rules=dict(policy.rules),
                       tool_rules={"write_file": Decision.ALLOW})
    assert tightened.decide(RiskClass.WRITE, "write_file") is Decision.ALLOW
    # other tools keep the class posture
    assert tightened.decide(RiskClass.WRITE, "anything_else") is Decision.ASK


def test_02_tool_rule_deny_beats_class_allow():
    tightened = Policy.default()
    hardened = Policy(rules=dict(tightened.rules),
                      tool_rules={"weather_report": Decision.DENY})
    assert hardened.decide(RiskClass.READ, "weather_report") is Decision.DENY


def test_03_engine_runs_allowed_tool_without_consent():
    reg, executed = stub_registry()
    policy = Policy(rules=dict(Policy.default().rules),
                    tool_rules={"write_file": Decision.ALLOW})
    engine = PolicyEngine(policy)
    result = asyncio.run(engine.run(call("write_file"), reg, consent=None))
    assert result.ok and executed == ["write_file"]


def test_04_engine_deny_blocks_without_consent_prompt():
    reg, executed = stub_registry()
    policy = Policy(rules=dict(Policy.default().rules),
                    tool_rules={"weather_report": Decision.DENY})
    engine = PolicyEngine(policy)
    result = asyncio.run(engine.run(call("weather_report"), reg, consent=None))
    assert not result.ok and executed == []


def test_05_decide_without_tool_name_unchanged():
    p = Policy.default()
    assert p.decide(RiskClass.READ) is Decision.ALLOW
    assert p.decide(RiskClass.WRITE) is Decision.ASK


# ----------------------------------------------------------------- ConsentGate

class FakeBus:
    def __init__(self):
        self.events = []

    async def publish(self, event):
        self.events.append(event)


class FakeUI:
    def __init__(self):
        self._win = None


class FakeWin:
    """Records _consent_request calls; auto-answers via the dialog callback
    only when `dialog_answer` is not None."""

    def __init__(self, dialog_answer=None):
        self.calls = []
        self.dialog_answer = dialog_answer

    def _consent_request(self, name, risk, args, on_answer):
        self.calls.append((name, risk, args, on_answer))
        if self.dialog_answer is not None:
            on_answer(self.dialog_answer)


def _write_call():
    return ToolCall(id="c-w", name="write_file", args={"path": "x"},
                    source="test")


def test_06_dialog_answer_true():
    ui = FakeUI()
    ui._win = FakeWin(dialog_answer=True)
    gate = ConsentGate(ui, timeout_s=2.0)
    assert asyncio.run(gate.request(_write_call(), RiskClass.WRITE)) is True


def test_07_remote_resolve_true_without_dialog():
    ui = FakeUI()
    ui._win = FakeWin(dialog_answer=None)  # dialog stays silent
    gate = ConsentGate(ui, timeout_s=5.0)

    async def scenario():
        task = asyncio.create_task(gate.request(_write_call(), RiskClass.WRITE))
        for _ in range(100):
            if gate.pending_requests():
                break
            await asyncio.sleep(0.01)
        assert len(gate.pending_requests()) == 1
        rid = gate.pending_requests()[0]
        assert gate.resolve(rid, True) is True
        return await task

    assert asyncio.run(scenario()) is True


def test_08_first_answer_wins_remote_then_dialog():
    ui = FakeUI()
    win = FakeWin(dialog_answer=None)
    ui._win = win
    gate = ConsentGate(ui, timeout_s=5.0)

    async def scenario():
        task = asyncio.create_task(gate.request(_write_call(), RiskClass.WRITE))
        for _ in range(100):
            if gate.pending_requests():
                break
            await asyncio.sleep(0.01)
        rid = gate.pending_requests()[0]
        assert gate.resolve(rid, True) is True  # remote approves first
        for _ in range(100):
            if not gate.pending_requests():
                break
            await asyncio.sleep(0.01)
        # late dialog denial must NOT flip the settled answer
        if win.calls:
            win.calls[0][3](False)
        return await task

    assert asyncio.run(scenario()) is True


def test_09_remote_resolve_unknown_id_is_false():
    gate = ConsentGate(FakeUI())
    assert gate.resolve("nonexistent", True) is False


def test_10_timeout_denies():
    ui = FakeUI()
    ui._win = FakeWin(dialog_answer=None)
    gate = ConsentGate(ui, timeout_s=0.05)
    assert asyncio.run(gate.request(_write_call(), RiskClass.WRITE)) is False


def test_11_missing_ui_fails_closed():
    gate = ConsentGate(FakeUI())  # _win is None
    assert asyncio.run(gate.request(_write_call(), RiskClass.WRITE)) is False


def test_12_bus_events_carry_request_and_resolution():
    ui = FakeUI()
    ui._win = FakeWin(dialog_answer=True)
    bus = FakeBus()
    gate = ConsentGate(ui, timeout_s=2.0, bus=bus)
    asyncio.run(gate.request(_write_call(), RiskClass.WRITE))
    types = [e.type for e in bus.events]
    assert "tool.confirmation_request" in types
    assert "tool.confirmation_resolved" in types
    req = next(e for e in bus.events if e.type == "tool.confirmation_request")
    assert req.payload["name"] == "write_file"
    assert req.payload["risk"] == "write"
    assert req.payload["id"]
