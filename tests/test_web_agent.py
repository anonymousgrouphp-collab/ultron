"""research/12 D1 tests: computer-use gateway protocol + web agent loop."""

import asyncio
import json

import pytest

from kernel.gateway.base import GatewayError
from kernel.gateway.cu import (
    ComputerUseGateway,
    CuCall,
    CuTurn,
    DEFAULT_GEMINI_CU_MODEL,
)
from kernel.types import RiskClass, ToolCall
from kernel.tools import ToolRegistry
from kernel.webagent import WebAgent, build_web_agent_tools


# ------------------------------------------------------------- gateway proto

class FakePost:
    """Records requests; returns a scripted body."""

    def __init__(self, body):
        self.body = body
        self.requests = []

    def __call__(self, url, headers, payload, timeout_s):
        self.requests.append({"url": url, "headers": dict(headers),
                              "payload": payload})
        return 200, self.body


def _body_with(parts):
    return {"candidates": [{"content": {"parts": parts}}]}


def test_01_gateway_sends_computer_use_tool_and_parses_calls():
    post = FakePost(_body_with([
        {"text": "looking", "thought": True},
        {"functionCall": {"name": "navigate",
                          "args": {"url": "https://example.com"},
                          "id": "abc-1"}},
    ]))
    gw = ComputerUseGateway(api_key="k", post=post)
    assert gw.model == DEFAULT_GEMINI_CU_MODEL

    task = gw.task("find a mug")
    turn = asyncio.run(task.first(b"PNG1", url="https://google.com"))

    req = post.requests[0]
    assert "computerUse" in json.dumps(req["payload"]["tools"])
    assert req["payload"]["generationConfig"]["thinkingConfig"][
        "includeThoughts"] is True
    assert req["payload"]["contents"][0]["parts"][0]["text"] == "find a mug"
    first_part = req["payload"]["contents"][0]["parts"][1]
    assert first_part["inlineData"]["mimeType"] == "image/png"
    assert turn.thought == "looking"
    assert turn.calls[0].name == "navigate"
    assert turn.calls[0].id == "abc-1"


def test_02_respond_embeds_screenshot_and_echoes_calls():
    post = FakePost(_body_with([{"text": "all done"}]))
    gw = ComputerUseGateway(api_key="k", post=post)
    task = gw.task("do it")

    # seed a model turn manually via first()
    post.body = _body_with([
        {"functionCall": {"name": "click_at", "args": {"x": 500, "y": 250},
                          "id": "c1"}}])
    asyncio.run(task.first(b"PNG0"))
    post.body = _body_with([{"text": "all done"}])

    turn = asyncio.run(task.respond(
        [("c1", "click_at", {"url": "https://x.test"})], b"PNG2",
        url="https://x.test"))

    contents = post.requests[-1]["payload"]["contents"]
    model_turn = contents[-2]
    assert model_turn["role"] == "model"
    assert model_turn["parts"][0]["functionCall"]["name"] == "click_at"
    response_turn = contents[-1]
    fr = response_turn["parts"][0]["functionResponse"]
    assert fr["id"] == "c1"
    assert fr["response"]["url"] == "https://x.test"
    assert fr["parts"][0]["inlineData"]["data"]  # screenshot inside response
    assert turn.text == "all done" and turn.calls == ()


def test_03_http_error_is_clean_gateway_error():
    def post(url, headers, payload, timeout_s):
        return 500, {}

    gw = ComputerUseGateway(api_key="k", post=post)
    task = gw.task("x")
    with pytest.raises(GatewayError):
        asyncio.run(task.first(b"PNG"))


def test_04_gateway_requires_key():
    with pytest.raises(GatewayError):
        ComputerUseGateway(api_key="")


# ------------------------------------------------------------- agent loop

class FakeMouse:
    def __init__(self, page):
        self._page = page

    async def click(self, x, y):
        self._page.events.append(("click", x, y))

    async def move(self, x, y, steps=1):
        self._page.events.append(("move", x, y))

    async def down(self):
        self._page.events.append(("down",))

    async def up(self):
        self._page.events.append(("up",))

    async def wheel(self, dx, dy):
        self._page.events.append(("wheel", dx, dy))


class FakeKeyboard:
    def __init__(self, page):
        self._page = page

    async def press(self, key):
        self._page.events.append(("press", key))

    async def type(self, text):
        self._page.events.append(("type", text))


class FakePage:
    def __init__(self):
        self.url = "https://start.test"
        self.events = []
        self.mouse = FakeMouse(self)
        self.keyboard = FakeKeyboard(self)
        self.navigations = []

    async def goto(self, url):
        self.navigations.append(url)
        self.url = url

    async def screenshot(self, type="png"):
        assert type == "png"
        return b"PNG"

    async def go_back(self):
        self.events.append(("back",))

    async def go_forward(self):
        self.events.append(("forward",))


class ScriptedGateway:
    """Duck-typed ComputerUseGateway: scripted turns, records instructions."""

    def __init__(self, turns):
        self._turns = list(turns)
        self.instructions = []

    def task(self, instruction):
        self.instructions.append(instruction)

        class _Task:
            def __init__(self, outer):
                self._outer = outer

            async def first(self, screenshot, url=""):
                return self._outer._turns.pop(0)

            async def respond(self, results, screenshot, url=""):
                self._outer.last_results = results
                return self._outer._turns.pop(0)

        return _Task(self)


async def _fake_factory(page):
    closed = []

    async def close():
        closed.append(True)

    return page, close


def test_05_agent_executes_actions_and_finishes_on_text():
    page = FakePage()
    gw = ScriptedGateway([
        CuTurn(thought="opening", calls=(
            CuCall(id="1", name="navigate",
                   args={"url": "https://example.com"}),
            CuCall(id="2", name="click_at", args={"x": 500, "y": 250}),
        )),
        CuTurn(text="found it"),
    ])
    agent = WebAgent(gw, browser_factory=lambda w, h: _fake_factory(page),
                     settle_s=0)
    outcome = asyncio.run(agent.run_task("find a mug"))

    assert outcome["status"] == "success"
    assert outcome["summary"] == "found it"
    assert outcome["turns"] == 2
    assert page.navigations == ["https://example.com"]
    # 500/1000 * 1440 = 720, 250/1000 * 900 = 225 — coordinates denormalized
    assert ("click", 720, 225) in page.events


def test_06_agent_reports_failures_as_observations_not_crashes():
    page = FakePage()
    gw = ScriptedGateway([
        CuTurn(calls=(CuCall(id="1", name="navigate",
                             args={"url": "https://ok.test"}),
                      CuCall(id="2", name="teleport",
                             args={"x": 1, "y": 2}))),
        CuTurn(text="done anyway"),
    ])
    agent = WebAgent(gw, browser_factory=lambda w, h: _fake_factory(page),
                     settle_s=0)
    outcome = asyncio.run(agent.run_task("try the weird action"))
    assert outcome["status"] == "success"
    assert any("unimplemented action 'teleport'" in a for a in outcome["actions"])


def test_07_turn_limit_fails_cleanly():
    page = FakePage()
    gw = ScriptedGateway([
        CuTurn(calls=(CuCall(id=str(i), name="open_web_browser", args={}),))
        for i in range(5)
    ])
    agent = WebAgent(gw, browser_factory=lambda w, h: _fake_factory(page),
                     max_turns=3, settle_s=0)
    outcome = asyncio.run(agent.run_task("loop forever"))
    assert outcome["status"] == "failure"
    assert "turn limit" in outcome["summary"]
    assert outcome["turns"] == 3


def test_08_empty_prompt_is_a_clean_failure():
    page = FakePage()
    gw = ScriptedGateway([])
    agent = WebAgent(gw, browser_factory=lambda w, h: _fake_factory(page))
    outcome = asyncio.run(agent.run_task("   "))
    assert outcome["status"] == "failure"


# ----------------------------------------------------------------- the tool

def _registry_with(**kw):
    reg = ToolRegistry()
    build_web_agent_tools(
        reg,
        gateway_factory=kw.pop("gateway_factory", lambda: object()),
        **kw)
    return reg


def _tool_call(prompt="find a mug"):
    return ToolCall(id="c-wa", name="run_web_agent", args={"prompt": prompt},
                    source="test")


def test_09_tool_runs_to_success_with_fakes():
    page = FakePage()
    gw = ScriptedGateway([CuTurn(text="done")])
    reg = _registry_with(
        gateway_factory=lambda: gw,
        enabled=True,
        browser_factory=lambda w, h: _fake_factory(page))
    result = asyncio.run(reg.execute(_tool_call()))
    assert result.ok
    assert result.data["status"] == "success"
    assert result.risk == RiskClass.WRITE


def test_10_tool_denied_when_gate_off():
    reg = _registry_with(enabled=False)
    result = asyncio.run(reg.execute(_tool_call()))
    assert not result.ok
    assert "disabled" in result.error


def test_11_tool_fails_cleanly_without_gateway():
    reg = _registry_with(gateway_factory=lambda: None, enabled=True)
    result = asyncio.run(reg.execute(_tool_call()))
    assert not result.ok
    assert "Gemini API key" in result.error


def test_12_broken_gate_denies():
    def broken():
        raise RuntimeError("config exploded")

    reg = _registry_with(consent=broken)
    result = asyncio.run(reg.execute(_tool_call()))
    assert not result.ok
