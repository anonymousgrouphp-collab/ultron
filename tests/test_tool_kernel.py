"""P1-B tests: tool kernel (Tool + ToolRegistry). Standalone fake tools, hermetic."""

import asyncio

import pytest

from kernel import EventBus, RiskClass, ToolCall, ToolResult
from kernel.tools import Tool, ToolRegistry, default_registry


def call(name="echo", **args):
    return ToolCall(id=f"c-{name}", name=name, args=args, source="test")


def make_tool(name="echo", **kw):
    defaults = dict(
        name=name,
        description="echoes its arguments",
        parameters={"type": "object", "properties": {"msg": {"type": "string"}}},
        handler=lambda c: {"echo": c.args},
    )
    defaults.update(kw)
    return Tool(**defaults)


# ---------------------------------------------------------------- registration

def test_01_register_and_lookup():
    reg = ToolRegistry()
    reg.register(make_tool())
    assert "echo" in reg and reg.get("echo").description == "echoes its arguments"
    assert reg.names() == ("echo",)


def test_02_duplicate_registration_rejected():
    reg = ToolRegistry()
    reg.register(make_tool())
    with pytest.raises(ValueError, match="already registered"):
        reg.register(make_tool())


def test_03_tool_name_validation():
    with pytest.raises(ValueError):
        make_tool(name="")
    with pytest.raises(ValueError):
        make_tool(name="has space")


def test_04_parameters_must_be_object_schema():
    with pytest.raises(ValueError, match="type 'object'"):
        make_tool(parameters={"type": "string"})


def test_05_decorator_registers_and_fn_stays_callable():
    reg = ToolRegistry()

    @reg.tool(name="greet", description="greets",
              parameters={"type": "object", "properties": {"who": {"type": "string"}}})
    def greet(call):
        return {"hi": call.args["who"]}

    assert "greet" in reg
    assert greet(call("greet", who="sir")) == {"hi": "sir"}  # fn unchanged, still callable


def test_06_declarations_gateway_shape():
    reg = ToolRegistry()
    reg.register(make_tool())
    decls = reg.declarations()
    assert len(decls) == 1
    d = decls[0]
    assert set(d) == {"name", "description", "parameters"}
    assert d["parameters"]["type"] == "object"


def test_07_risk_map_for_policy_engine():
    reg = ToolRegistry()
    reg.register(make_tool(name="read_cfg", risk=RiskClass.READ))
    reg.register(make_tool(name="del_file", risk=RiskClass.DESTRUCTIVE))
    assert reg.risks() == {"read_cfg": RiskClass.READ, "del_file": RiskClass.DESTRUCTIVE}


def test_08_default_registry_exists():
    assert isinstance(default_registry, ToolRegistry)


# ---------------------------------------------------------------- execution

def test_09_unknown_tool_is_clean_fail():
    reg = ToolRegistry()
    res = asyncio.run(reg.execute(call("nope")))
    assert res.ok is False and "unknown tool" in res.error and res.data is None


def test_10_success_wraps_plain_return():
    reg = ToolRegistry()
    reg.register(make_tool())
    res = asyncio.run(reg.execute(call(msg="hello")))
    assert res.ok is True and res.data == {"echo": {"msg": "hello"}}


def test_11_sync_and_async_handlers():
    reg = ToolRegistry()

    async def async_handler(c):
        return {"async": True}

    reg.register(make_tool(name="sync_tool"))
    reg.register(make_tool(name="async_tool", handler=async_handler))
    assert asyncio.run(reg.execute(call("sync_tool"))).ok
    assert asyncio.run(reg.execute(call("async_tool"))).data == {"async": True}


def test_12_toolresult_passthrough_restamped():
    reg = ToolRegistry()

    def handler(c):
        return ToolResult.ok(ToolCall(id="WRONG", name="WRONG"), data={"v": 1})

    reg.register(make_tool(name="bad_id", handler=handler))
    res = asyncio.run(reg.execute(call("bad_id")))
    assert res.ok and res.call_id == "c-bad_id" and res.name == "bad_id"


def test_13_timeout_produces_clean_fail():
    reg = ToolRegistry()

    def slow(c):
        import time as t
        t.sleep(1.0)
        return "never"

    reg.register(make_tool(name="slow", handler=slow, timeout_s=0.05, max_retries=0))
    res = asyncio.run(reg.execute(call("slow")))
    assert res.ok is False and "timed out after 0.05s" in res.error


def test_14_retry_recovers_from_crashes():
    reg = ToolRegistry()
    state = {"n": 0}

    def flaky(c):
        state["n"] += 1
        if state["n"] < 3:
            raise RuntimeError("transient")
        return {"ok": True}

    reg.register(make_tool(name="flaky", handler=flaky, max_retries=2))
    res = asyncio.run(reg.execute(call("flaky")))
    assert res.ok is True and state["n"] == 3


def test_15_retries_exhausted_clean_message():
    reg = ToolRegistry()

    def always_broken(c):
        raise RuntimeError("boom detail")

    reg.register(make_tool(name="broken", handler=always_broken, max_retries=1))
    res = asyncio.run(reg.execute(call("broken")))
    assert res.ok is False
    assert "RuntimeError: boom detail" in res.error and "Traceback" not in res.error


def test_16_expected_failures_not_retried():
    reg = ToolRegistry()
    state = {"n": 0}

    def expected_fail(c):
        state["n"] += 1
        return ToolResult.fail(c, "file not found")

    reg.register(make_tool(name="efail", handler=expected_fail, max_retries=5))
    res = asyncio.run(reg.execute(call("efail")))
    assert res.ok is False and state["n"] == 1  # a returned fail is an answer, not a crash
    assert res.error == "file not found"


# ---------------------------------------------------------------- bus integration

def test_17_bus_events_on_success():
    reg, bus = ToolRegistry(), EventBus()
    seen = []
    bus.subscribe("tool.*", lambda e: seen.append(e.type))
    reg.register(make_tool())
    asyncio.run(reg.execute(call(), bus=bus))
    assert seen == ["tool.started", "tool.completed"]


def test_18_bus_events_on_failure_and_started_payload():
    reg, bus = ToolRegistry(), EventBus()
    events = []
    bus.subscribe("tool.*", lambda e: events.append(e))
    reg.register(make_tool(name="bad", handler=lambda c: 1 / 0, max_retries=0))
    res = asyncio.run(reg.execute(call("bad"), bus=bus))
    assert res.ok is False
    started = events[0]
    assert started.type == "tool.started"
    assert started.payload["risk"] == "read" and started.payload["source"] == "test"
    assert events[-1].type == "tool.failed"


def test_19_duration_recorded():
    import time as t

    reg = ToolRegistry()
    reg.register(make_tool(name="ping", handler=lambda c: (t.sleep(0.02), "pong")[1]))
    res = asyncio.run(reg.execute(call("ping")))
    assert res.duration_ms >= 15.0
