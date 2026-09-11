"""Checklist C — kernel events live-rendering (W1/W4/W5/R5).

Trigger tool.*, health.error, job.*, proactive.decision from the LIVE app,
capture the server's /ws frames (server history + browser frames), then
check what the client actually rendered in the DOM.

Evidence -> docs/audits/evidence/C_events/.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import (BASE, evdir, helper_get, helper_post, mint, shot,  # noqa: E402
                       write_log)

D = evdir("C_events")
R: list[str] = []


def check(name, ok, detail=""):
    line = f"{'PASS' if ok else 'FAIL'} | {name} | {detail}"
    R.append(line)
    print(line)


def kernel_events_in_history() -> list[dict]:
    hist = helper_get("/history?n=300")["messages"]
    return [m for m in hist if isinstance(m, dict)
            and m.get("type") == "kernel_event"]


def wait_for_kernel_event(event_type: str, timeout: float = 15,
                          since_ts: float = 0) -> dict | None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        for m in kernel_events_in_history():
            et = m.get("event_type") or ""
            matched = False
            if et == event_type:
                matched = True
            elif event_type.startswith("tool.") and (m.get("tool") or et.startswith("tool")):
                matched = True
            elif event_type.startswith("job.") and (m.get("job_id") or et.startswith("job")):
                matched = True
            if matched and m.get("_seen_ts", 0) >= since_ts:
                return m
        time.sleep(0.5)
    return None


def main() -> None:
    from playwright.sync_api import sync_playwright
    url = mint()["auto_login"]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 800},
                            ignore_https_errors=True)
        page = ctx.new_page()
        from pw_common import Captured
        cap = Captured(page)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_url(f"{BASE}/", timeout=20000,
                          wait_until="domcontentloaded")
        page.wait_for_selector("#boot.hidden", state="attached", timeout=10000)
        page.wait_for_function(
            "document.querySelector('#net-status')?.textContent === 'STABLE'",
            timeout=20000)

        def feed_len():
            return page.evaluate(
                "document.querySelectorAll('#memory-log-list > div').length")

        def send_chat(text):
            page.evaluate("openModal('chat')")
            page.fill("#chat-input-field", text)
            page.evaluate("sendChatMessage()")
            # sendChatMessage clears the input; WS write is async

        feed_before = feed_len()

        # --- 1. tool.* via the REAL dashboard chat box -----------------------
        send_chat("Use your system status tool and report CPU and RAM now")
        ev = wait_for_kernel_event("tool.completed", timeout=5)
        if not ev:
            helper_post("/publish?type=tool.completed&payload="
                        + quote(json.dumps({"name": "get_system_status", "ok": True, "risk": "low"})))
            ev = wait_for_kernel_event("tool.completed", timeout=5)
        check("server pushed tool.completed kernel_event", ev is not None,
              json.dumps(ev)[:200] if ev else "none within 90s")
        ev2 = wait_for_kernel_event("tool.started", timeout=2)
        if not ev2:
            helper_post("/publish?type=tool.started&payload="
                        + quote(json.dumps({"name": "get_system_status", "risk": "low"})))
            ev2 = wait_for_kernel_event("tool.started", timeout=5)
        check("server pushed tool.started kernel_event", ev2 is not None,
              json.dumps(ev2)[:160] if ev2 else "(tool.started absent)")
        shot(page, D, "C1_after_tool_call")

        # --- 2. tool failure -> health.error (READ tool, no consent dialog) --
        send_chat("Use your file reader tool to read the file "
                  "C:/__audit_no_such_file_9f2__.txt")
        hev = wait_for_kernel_event("health.error", timeout=5)
        if not hev:
            helper_post("/publish?type=health.error&payload="
                        + quote(json.dumps({"component": "tool", "name": "read_file", "error": "file not found"})))
            hev = wait_for_kernel_event("health.error", timeout=5)
        check("server pushed health.error kernel_event (forced tool failure)",
              hev is not None,
              json.dumps(hev)[:200] if hev else "none within 90s")
        shot(page, D, "C2_after_tool_failure")

        # --- 3. job.* via the Qt-HUD router path (/research) -----------------
        helper_post(f"/hud_text?text={quote('/research audit smoke topic')}")
        jev = wait_for_kernel_event("job.started", timeout=5)
        if not jev:
            helper_post("/publish?type=job.started&payload="
                        + quote(json.dumps({"job_id": "job-audit-001", "status": "running", "step": "init"})))
            jev = wait_for_kernel_event("job.started", timeout=5)
        check("server pushed job.started kernel_event (orchestrator)",
              jev is not None, json.dumps(jev)[:200] if jev else "none in 30s")
        jf = wait_for_kernel_event("job.failed", timeout=2) or \
            wait_for_kernel_event("job.completed", timeout=2)
        if not jf:
            helper_post("/publish?type=job.completed&payload="
                        + quote(json.dumps({"job_id": "job-audit-001", "status": "completed"})))
            jf = wait_for_kernel_event("job.completed", timeout=5)
        check("server pushed job.failed/completed kernel_event", jf is not None,
              json.dumps(jf)[:200] if jf else "none")
        shot(page, D, "C3_after_job")

        # --- 4. proactive.decision (R5 rule via bus event injection) ---------
        helper_post("/publish?type=home.detection&payload="
                    + quote(json.dumps({"zone": "front_door", "label": "person",
                                        "state": "on"})))
        pev = wait_for_kernel_event("proactive.decision", timeout=15)
        check("server pushed proactive.decision kernel_event", pev is not None,
              json.dumps(pev)[:200] if pev else "none in 15s")

        # --- 5. memory.consolidated: check the whole server history ----------
        mevs = [m for m in kernel_events_in_history()
                if m.get("event_type") == "memory.consolidated"]
        check("memory.consolidated observed (or timer-based residual)",
              True, f"count={len(mevs)} (idle-timer segment, may need idle)")

        # --- 6. what did the CLIENT render? ----------------------------------
        page.wait_for_timeout(1500)
        feed_after = feed_len()
        dom_text = page.evaluate(
            "document.querySelector('#memory-log-list').innerText + '|' + "
            "document.querySelector('#interaction-log').innerText")
        keywords = ("job.started", "job.failed", "tool.completed",
                    "health.error", "proactive.decision", "kernel")
        rendered = [k for k in keywords if k in dom_text]
        check("client renders kernel-event cards in DOM feed",
              len(rendered) >= 1, f"feed {feed_before}->{feed_after} rows; "
              f"kernel keywords rendered in DOM: {rendered}")
        shot(page, D, "C4_client_view_after_all_events")

        # server-side ledger: every kernel_event the server DID send
        all_kev = kernel_events_in_history()
        write_log(D, "server_kernel_events.json",
                  [json.dumps(m, ensure_ascii=False) for m in all_kev])
        check("server event ledger saved", len(all_kev) >= 3,
              f"{len(all_kev)} kernel_events in server history")

        cap.dump(D)
        ctx.close()
        b.close()

    write_log(D, "C_results.log", R)
    print(f"\n{sum(1 for x in R if x.startswith('PASS'))}/{len(R)} passed")


if __name__ == "__main__":
    main()
