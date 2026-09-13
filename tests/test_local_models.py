"""research/12 D7 tests: OllamaModelManager lifecycle (fake transport)."""

import asyncio

from app.local_models import OllamaModelManager


class FakeOllama:
    """Records generate posts; /api/ps served by setting `running`."""

    def __init__(self):
        self.running = []
        self.generate_calls = []

    def post(self, url, payload, timeout_s):
        if url.endswith("/api/generate"):
            self.generate_calls.append(payload)
            if payload.get("keep_alive") == 0:
                target = payload["model"]
                self.running = [m for m in self.running if not m.startswith(target)]
            return {}
        raise AssertionError(f"unexpected url {url}")


def _manager(fake, idle_s=60.0, clock=lambda: 0.0):
    return OllamaModelManager("http://127.0.0.1:11434", idle_s=idle_s,
                              post=fake.post,
                              list_running=lambda: list(fake.running),
                              clock=clock)


def test_01_unload_posts_keep_alive_zero():
    fake = FakeOllama()
    fake.running = ["qwen3:8b", "llama3:8b"]
    mgr = _manager(fake)
    assert mgr.unload("qwen3:8b") is True
    assert fake.generate_calls[0]["keep_alive"] == 0
    assert fake.running == ["llama3:8b"]


def test_02_ensure_exclusive_spares_keep_list():
    fake = FakeOllama()
    fake.running = ["qwen3:8b", "qwen3:4b-included", "minicpm-v:8b"]
    mgr = _manager(fake)
    evicted = mgr.ensure_exclusive(["qwen3"])
    # prefix-tolerant keep: both qwen models survive, minicpm evicted
    assert evicted == ["minicpm-v:8b"]
    assert fake.running == ["qwen3:8b", "qwen3:4b-included"]


def test_03_warm_sends_single_token_lease_and_marks_used():
    fake = FakeOllama()
    now = [100.0]
    mgr = _manager(fake, clock=lambda: now[0])
    assert mgr.warm("qwen3:8b") is True
    call = fake.generate_calls[0]
    assert call["options"]["num_predict"] == 1
    assert call["keep_alive"] == "30m"
    # mark_used took effect (idle sweeper would not evict immediately)
    with mgr._lock:
        assert mgr._last_used == 100.0


def test_04_idle_sweeper_evicts_after_idle():
    fake = FakeOllama()
    fake.running = ["qwen3:8b"]
    now = [100.0]
    mgr = _manager(fake, idle_s=60.0, clock=lambda: now[0])
    mgr.warm("qwen3:8b")

    async def drive():
        task = asyncio.create_task(mgr.run_idle_sweeper(poll_s=0.01))
        now[0] = 100.0 + 61.0        # jump past the idle window
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert fake.running == []        # evicted by the sweep
    unload = [c for c in fake.generate_calls if c.get("keep_alive") == 0]
    assert len(unload) == 1


def test_05_sweeper_noops_when_recently_used():
    fake = FakeOllama()
    fake.running = ["qwen3:8b"]
    now = [100.0]
    mgr = _manager(fake, idle_s=60.0, clock=lambda: now[0])
    mgr.warm("qwen3:8b")

    async def drive():
        task = asyncio.create_task(mgr.run_idle_sweeper(poll_s=0.01))
        now[0] = 100.0 + 10.0        # still inside the idle window
        await asyncio.sleep(0.05)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    asyncio.run(drive())
    assert fake.running == ["qwen3:8b"]


def test_06_from_config_none_for_cloud_provider():
    assert OllamaModelManager.from_config({"llm_provider": "gemini"}) is None
    mgr = OllamaModelManager.from_config({
        "llm_provider": "ollama",
        "ollama_base_url": "http://127.0.0.1:11434/",
        "ollama_idle_unload_s": "300",
    })
    assert mgr is not None
    assert mgr._idle_s == 300.0
