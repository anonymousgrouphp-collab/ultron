"""P2-F tests: briefing sections (injected fetch), notify senders (injected
post), and the queue integration via make_briefing_kind. Fully hermetic."""

import asyncio
import json
from datetime import date, timedelta
from pathlib import Path


from kernel.briefing import (
    BRIEFING_JOB_KIND,
    build_briefing,
    make_briefing_kind,
    render,
)
from kernel.notify import send_briefing, send_ntfy, send_telegram
from kernel.orchestrator import JobQueue, Orchestrator
from kernel.policy import PolicyEngine
from kernel.tools import ToolRegistry

WEATHER_JSON = json.dumps({"daily": {
    "time": ["2026-09-09", "2026-09-10"],
    "temperature_2m_max": [25.3, 27.0],
    "temperature_2m_min": [15.1, 16.2],
    "precipitation_probability_max": [10, 80],
}})

RSS = """<?xml version="1.0"?>
<rss><channel>
<item><title>Fusion breakthrough announced</title></item>
<item><title>Kernel 6.20 released</title></item>
<item><title>Third story</title></item>
<item><title>Fourth story should be dropped</title></item>
</channel></rss>"""



def fake_fetch(url: str) -> tuple[int, str]:
    if "open-meteo" in url:
        return 200, WEATHER_JSON
    if url.endswith("/dead"):
        return 500, "down"
    if url.endswith(".rss"):
        return 200, RSS
    raise OSError("no route")


def run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------- sections

def test_01_full_briefing_assembles_all_sections():
    brief = build_briefing(
        weather={"latitude": 25.2, "longitude": 55.3},
        feeds=["https://example.com/feeds/tech.rss",
               "https://example.com/feeds/dead"],
        calendar_path=None, events=["job: backup failed", "job: report done"],
        memory_highlights=["Follow up on the fusion paper"], fetch=fake_fetch)
    by_title = {s.title: s for s in brief.sections}
    assert set(by_title) == {"Weather", "News", "Overnight", "Memory highlights"}
    assert "25° / 15°C, rain 10%" in by_title["Weather"].lines[0]
    assert "80%" in by_title["Weather"].lines[1]
    news = by_title["News"].lines
    assert any("Fusion breakthrough" in line for line in news)
    assert not any("Fourth story" in line for line in news)  # cap = 3/feed
    assert any("(unavailable)" in line for line in news)     # dead feed degrades
    assert by_title["Overnight"].lines == ("job: backup failed",
                                           "job: report done")


def test_02_calendar_filters_to_today_and_tomorrow(tmp_path: Path):
    today = date.today().strftime("%Y%m%d")
    tomorrow = (date.today() + timedelta(days=1)).strftime("%Y%m%d")
    ics = tmp_path / "cal.ics"
    ics.write_text(f"""BEGIN:VCALENDAR
BEGIN:VEVENT
DTSTART:20990101T090000
SUMMARY:Far future thing
END:VEVENT
BEGIN:VEVENT
DTSTART:{today}T100000
SUMMARY:Dentist\\, cleaning
END:VEVENT
BEGIN:VEVENT
DTSTART:{tomorrow}
SUMMARY:Ship the kernel
END:VEVENT
END:VCALENDAR""", encoding="utf-8")
    brief = build_briefing(calendar_path=str(ics))
    cal = brief.sections[0]
    assert cal.title == "Calendar"
    joined = "\n".join(cal.lines)
    assert "Dentist, cleaning" in joined       # comma unescaped, today
    assert "Ship the kernel" in joined         # tomorrow, all-day (no time)
    assert "Far future thing" not in joined


def test_03_empty_call_yields_empty_briefing():
    assert build_briefing().sections == ()
    assert render(build_briefing()).startswith("ULTRON briefing — ")


def test_04_render_shape():
    brief = build_briefing(events=["a", "b"], memory_highlights=None,
                           fetch=fake_fetch)
    text = render(brief)
    assert "[Overnight]" in text and "  a" in text and "  b" in text
    assert "[Memory highlights]" not in text  # None → section omitted
    assert "Memory" not in text


def test_05_dead_weather_source_degrades_never_raises():
    def dead(url):
        raise OSError("offline")

    brief = build_briefing(weather={"latitude": 1, "longitude": 2}, fetch=dead)
    assert brief.sections[0].lines == ("(unavailable)",)


# ---------------------------------------------------------------- notify

def test_06_ntfy_posts_text_with_title():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        return 200, "OK"

    assert send_ntfy("tony/briefing", "hello", title="ULTRON", post=post) is True
    url, body, headers = calls[0]
    assert url == "https://ntfy.sh/tony/briefing"
    assert body.decode("utf-8") == "hello"
    assert headers["Title"] == "ULTRON"


def test_07_ntfy_failure_returns_false_never_raises():
    def post(url, body, headers):
        raise OSError("down")

    assert send_ntfy("topic", "m", post=post) is False
    assert send_ntfy("", "m", post=post) is False


def test_08_telegram_sends_json():
    calls = []

    def post(url, body, headers):
        calls.append((url, body, headers))
        return 200, '{"ok": true}'

    assert send_telegram("tok123", "chat9", "brief text", post=post) is True
    url, body, _ = calls[0]
    assert url == "https://api.telegram.org/bottok123/sendMessage"
    assert json.loads(body) == {"chat_id": "chat9", "text": "brief text"}


def test_09_send_briefing_channel_matrix():
    def post(url, body, headers):
        return 200, "ok"

    assert send_briefing("text", post=post) == {"ntfy": None, "telegram": None}
    assert send_briefing("text", ntfy_topic="t", post=post)["ntfy"] is True
    assert send_briefing("text", ntfy_topic="", telegram_token="k",
                         telegram_chat_id="c", post=post) == \
        {"ntfy": None, "telegram": True}


# ---------------------------------------------------------------- queue tie

def test_10_briefing_runs_as_a_durable_queue_job():
    q = JobQueue()
    reg = ToolRegistry()
    orch = Orchestrator(q, reg, PolicyEngine(), lease_s=30)
    orch.register_kind(BRIEFING_JOB_KIND, make_briefing_kind(
        weather={"latitude": 25.2, "longitude": 55.3},
        feeds=["https://example.com/feeds/tech.rss"],
        events=["overnight: 3 jobs completed"], fetch=fake_fetch))
    jid = q.enqueue("plan", {"plan": [
        {"id": "brief", "kind": BRIEFING_JOB_KIND, "spec": {}}]})
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "done", job.error
    out = job.result["outputs"]["brief"]
    assert out["sections"] == ["Weather", "News", "Overnight"]
    assert "ULTRON briefing — " in out["text"]
    assert "Fusion breakthrough announced" in out["text"]


def test_11_briefing_kind_without_fetch_fails_clean():
    q = JobQueue()
    reg = ToolRegistry()
    orch = Orchestrator(q, reg, PolicyEngine(), lease_s=30)
    orch.register_kind(BRIEFING_JOB_KIND, make_briefing_kind(feeds=[]))
    jid = q.enqueue("plan", {"plan": [
        {"id": "brief", "kind": BRIEFING_JOB_KIND, "spec": {}}]})
    run(orch.run_worker(worker="w", max_jobs=1))
    job = q.get(jid)
    assert job.status == "failed" and "fetch" in (job.error or "")


def test_12_ics_missing_file_degrades():
    brief = build_briefing(calendar_path=str(Path("Z:/nope.ics")))
    assert brief.sections[0].lines == ("(calendar unavailable)",)
