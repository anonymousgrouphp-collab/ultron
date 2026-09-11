"""Checklist F (part 1) — 30-minute soak: DOM growth, WS backlog, console
error accumulation. Samples every 60s; survives app restarts (reconnect
observation is part of the scenario). Evidence -> docs/audits/evidence/F_soak/.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import evdir, mint, write_log  # noqa: E402

import os

D = evdir("F_soak")
DURATION_S = int(os.environ.get("SOAK_DURATION", "120"))
SAMPLE_S = int(os.environ.get("SOAK_SAMPLE", "15"))


def main() -> None:
    from playwright.sync_api import sync_playwright
    url = mint()["auto_login"]
    rows: list[str] = [
        "t_s | ws_ready | feed_rows | chat_rows | dom_nodes | "
        "console_msgs | console_errors | ws_frames"]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 800},
                            ignore_https_errors=True)
        page = ctx.new_page()
        from pw_common import Captured
        cap = Captured(page)
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_timeout(3000)          # auto-login redirect (400ms) + nav
        page.wait_for_selector("#boot.hidden", state="attached", timeout=15000)
        shot = D / "soak_start.png"
        page.screenshot(path=str(shot))

        t0 = time.time()
        reconnects = 0
        prev_ws_open = True
        while time.time() - t0 < DURATION_S:
            time.sleep(SAMPLE_S)
            try:
                m = page.evaluate("""() => ({
                  ws: (window.ws && ws && ws.readyState) ?? -1,
                  feed: document.querySelectorAll('#memory-log-list > div').length,
                  chat: document.querySelectorAll('#chat-feed > div').length,
                  nodes: document.getElementsByTagName('*').length,
                  net: document.querySelector('#net-status')?.textContent
                })""")
            except Exception as e:
                rows.append(f"{int(time.time()-t0)} | EVAL-FAIL {e}")
                continue
            ws_open = m.get("ws") == 1
            if prev_ws_open and not ws_open:
                reconnects += 1
            prev_ws_open = ws_open
            errs = cap.console_errors()
            rows.append(
                f"{int(time.time()-t0)} | {m.get('ws')} | {m.get('feed')} | "
                f"{m.get('chat')} | {m.get('nodes')} | {len(cap.console)} | "
                f"{len(errs)} | {len(cap.ws_frames)} | net={m.get('net')} "
                f"reconnects={reconnects}")

        page.screenshot(path=str(D / "soak_end.png"))
        ctx.close()
        b.close()

    errs = cap.console_errors()
    rows.append("")
    rows.append(f"console errors total: {len(errs)}")
    rows.extend(errs[:20])
    rows.append(f"ws frames total: {len(cap.ws_frames)}")
    write_log(D, "soak_log.txt", rows)
    print("SOAK DONE:", len(rows) - 2, "samples")


if __name__ == "__main__":
    main()
