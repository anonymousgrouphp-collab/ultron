"""Regression tests for the legacy paths that remain during the kernel migration.

R2 (kill-list enforcement) additions: the app layer must never call an LLM
SDK or name a model string — `actions/_llm.py` is the ONE seam and it
routes through `kernel.gateway`. Tests are source-level (hermetic, no
network) plus wiring pins with a fake gateway.
"""

from __future__ import annotations

import base64
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

MODEL_ID = re.compile(r"""["'](?P<name>(?:gemini|gpt|o\d+|qwen|claude|llama)[\w.:-]*\d[\w.:-]*)["']""")
SDK_HITS = ("google.genai", "genai.Client", "generate_content",
            "chat.completions.create", "from anthropic")


def test_windows_app_launcher_never_constructs_a_shell_command(monkeypatch):
    from actions import open_app

    seen = {}

    monkeypatch.setattr(open_app.shutil, "which", lambda _: "C:/tools/safe.exe")
    monkeypatch.setattr(open_app.time, "sleep", lambda _: None)

    def popen(args, **kwargs):
        seen["args"] = args
        seen["shell"] = kwargs.get("shell")

    monkeypatch.setattr(open_app.subprocess, "Popen", popen)

    assert open_app._launch_windows("safe") is True
    assert seen == {"args": ["C:/tools/safe.exe"], "shell": False}


def test_legacy_file_processor_refuses_code_execution(tmp_path: Path):
    from actions.file_processor import _process_code

    script = tmp_path / "untrusted.py"
    script.write_text("raise RuntimeError('must not execute')", encoding="utf-8")

    result = _process_code(script, "run", {}, None)

    assert "disabled" in result.lower()
    assert "sandboxed" in result.lower()


def test_legacy_coding_agent_is_disabled():
    from actions.dev_agent import dev_agent

    result = dev_agent({"description": "write a program"})

    assert "disabled" in result.lower()
    assert "sandboxed" in result.lower()


# ── R2 kill-list enforcement ───────────────────────────────────────────────────


def test_no_llm_sdk_or_model_strings_in_actions():
    """Kill List §5: no LLM call outside the gateway, no model name outside
    the gateway. Source-level scan of every action module."""
    files = sorted(p for p in (REPO / "actions").glob("*.py")
                   if p.name != "_llm.py")  # the seam — checked by the wiring tests below
    assert files, "expected action modules to exist"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for lineno, line in enumerate(text.splitlines(), 1):
            if line.strip().startswith("#"):
                continue
            assert not any(hit in line for hit in SDK_HITS), (
                f"{path.name}:{lineno} still calls an LLM SDK directly: {line.strip()}")
            assert not MODEL_ID.search(line), (
                f"{path.name}:{lineno} hardcodes a model string: {line.strip()}")


def test_killlist_checker_flags_violations(tmp_path: Path):
    """The CI checker itself must catch a planted violation (no false negative)."""
    from evals.killlist_check import check_path

    bad = tmp_path / "bad.py"  # outside the repo — check_path must still work
    bad.write_text(
        "from google import genai\n"
        "client = genai.Client(api_key='x')\n"
        "client.models.generate_content(model=\"gemini-3.6-flash\", contents='hi')\n",
        encoding="utf-8",
    )
    findings = check_path(bad)
    assert findings, "planted violations must be caught"
    reasons = [r for _, _, r in findings]
    assert any("SDK" in r for r in reasons)
    assert any("model string" in r for r in reasons)


def test_killlist_checker_ignores_qt_exec_and_provenance_tags(tmp_path: Path):
    """No false positives: Qt event-loop .exec(), the 'gemini-live'
    provenance tag, and the 'llamacpp' provider label are not violations."""
    from evals.killlist_check import check_path

    ok = tmp_path / "ok.py"
    ok.write_text(
        "box.exec()\n"
        'source = "gemini-live"\n'
        'provider = "llamacpp"\n',
        encoding="utf-8",
    )
    assert check_path(ok) == []


def test_llm_seam_routes_through_gateway(monkeypatch):
    """actions._llm.complete_text builds GatewaySettings.from_config + the
    kernel gateway — never an SDK — and fails loudly on empty output."""
    import actions._llm as llm
    from kernel.gateway import Response

    class FakeGateway:
        def __init__(self):
            self.messages = None

        async def complete(self, messages, tools=(), response_schema=None):
            self.messages = messages
            return Response(text="  hello world  ")

    fake = FakeGateway()
    monkeypatch.setattr(llm, "build_gateway", lambda settings, api_key: fake)
    monkeypatch.setattr(llm, "load_config", lambda: {"llm_provider": "gemini"})

    assert llm.complete_text("prompt", system="sys") == "hello world"
    assert [(m.role, m.text) for m in fake.messages] == [
        ("system", "sys"), ("user", "prompt")]

    class Empty:
        async def complete(self, messages, tools=(), response_schema=None):
            return Response(text="  ")

    monkeypatch.setattr(llm, "build_gateway", lambda settings, api_key: Empty())
    try:
        llm.complete_text("prompt")
    except ValueError as exc:
        assert "empty" in str(exc)
    else:
        raise AssertionError("empty model output must raise")


def test_llm_seam_vision_carries_inline_parts(monkeypatch):
    """complete_vision puts bytes into Message.parts so the Gemini adapter
    can render inlineData — the multimodal path without the google SDK."""
    import actions._llm as llm
    from kernel.gateway import InlineData, Response

    seen = {}

    class FakeGateway:
        async def complete(self, messages, tools=(), response_schema=None):
            seen["parts"] = messages[0].parts
            return Response(text="x,y")

    monkeypatch.setattr(llm, "build_gateway", lambda settings, api_key: FakeGateway())
    monkeypatch.setattr(llm, "load_config", lambda: {"llm_provider": "gemini"})

    assert llm.complete_vision("find it", data=b"\x89PNG") == "x,y"
    assert seen["parts"] == (InlineData(mime_type="image/png", data=b"\x89PNG"),)


def test_gemini_adapter_renders_inline_parts_and_google_search_tool():
    """The adapter payload: user parts → inlineData base64; the
    google_search tool passes through verbatim next to function
    declarations (web_search's grounded search rides the gateway)."""
    from kernel.gateway import (
        GatewaySettings,
        GeminiAdapter,
        InlineData,
        Message,
        Provider,
    )

    class FakePost:
        def __init__(self):
            self.calls = []

        def __call__(self, url, headers, payload, timeout_s):
            self.calls.append(payload)
            return 200, {"candidates": [
                {"content": {"parts": [{"text": "ok"}]},
                 "finishReason": "STOP"}]}

    post = FakePost()
    adapter = GeminiAdapter(
        GatewaySettings(provider=Provider.GEMINI, gemini_model="gemini-3.6-flash"),
        api_key="k", post=post,
    )

    import asyncio
    asyncio.run(adapter.complete([
        Message(role="user", text="describe",
                parts=(InlineData(mime_type="image/png", data=b"abc"),)),
    ], tools=(
        {"google_search": {}},
        {"name": "web_search", "description": "d", "parameters": {}},
    )))

    payload = post.calls[0]
    parts = payload["contents"][0]["parts"]
    assert parts[0] == {"text": "describe"}
    assert parts[1] == {"inlineData": {
        "mimeType": "image/png",
        "data": base64.b64encode(b"abc").decode("ascii"),
    }}
    assert payload["tools"] == [
        {"google_search": {}},
        {"functionDeclarations": [
            {"name": "web_search", "description": "d", "parameters": {}}]},
    ]


def test_non_gemini_adapters_refuse_inline_parts():
    """Multimodal calls must fail cleanly on Ollama/OpenAI — never silently
    degrade to text-only."""
    from kernel.gateway import (
        GatewayError,
        GatewaySettings,
        InlineData,
        Message,
        OllamaAdapter,
        OpenAIChatAdapter,
        Provider,
    )

    import asyncio
    msg = Message(role="user", text="t",
                  parts=(InlineData(mime_type="image/png", data=b"x"),))
    for adapter in (
        OllamaAdapter(GatewaySettings(provider=Provider.OLLAMA)),
        OpenAIChatAdapter(GatewaySettings(provider=Provider.OPENAI), api_key="k"),
    ):
        try:
            asyncio.run(adapter.complete([msg]))
        except GatewayError as exc:
            assert "inline data parts" in str(exc)
        else:
            raise AssertionError(f"{type(adapter).__name__} must refuse parts")


def test_computer_settings_intent_goes_through_seam(monkeypatch):
    """A migrated action really uses actions._llm (wiring pin, no network)."""
    from actions import computer_settings
    import actions._llm as llm

    monkeypatch.setattr(
        llm, "complete_json", lambda prompt, system=None: {"action": "volume_set", "value": 42})
    assert computer_settings._detect_action("sesi 30 yap") == {
        "action": "volume_set", "value": 42}


def test_web_search_grounded_search_goes_through_seam(monkeypatch):
    from actions import web_search
    import actions._llm as llm

    monkeypatch.setattr(llm, "complete_grounded_search", lambda q: "answer text")
    assert web_search._gemini_search("latest news") == "answer text"
