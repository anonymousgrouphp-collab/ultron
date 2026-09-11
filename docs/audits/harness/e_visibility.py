"""Checklist E — NEW-FEATURE VISIBILITY on the dashboard.

E1: typed "/user list" from the DASHBOARD chat box — does it reach the
    command router (_on_text_command) or go straight to the Gemini session
    (app/monitors.py direct send)? Contrast with the Qt-HUD path (/hud_text).
E2/E3: any UI surface for /user or /cost in app.html?
Evidence -> docs/audits/evidence/E_visibility/.
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import (evdir, helper_get, helper_post, mint, shot,  # noqa: E402
                       write_log)

D = evdir("E_visibility")
R: list[str] = []


def check(name, ok, detail=""):
    line = f"{'PASS' if ok else 'FAIL'} | {name} | {detail}"
    R.append(line)
    print(line)


def chat_log_from_history() -> list[tuple[str, str]]:
    hist = helper_get("/history?n=120")["messages"]
    return [(m.get("speaker", m.get("type")), str(m.get("text", "")))
            for m in hist if m.get("type") in ("log", "sys")]


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
        page.wait_for_timeout(3000)
        page.wait_for_selector("#boot.hidden", state="attached", timeout=15000)
        page.wait_for_function(
            "document.querySelector('#net-status')?.textContent === 'STABLE'",
            timeout=20000)

        # --- E1: dashboard-typed slash command ------------------------------
        page.evaluate("openModal('chat')")
        page.fill("#chat-input-field", "/user list")
        page.evaluate("sendChatMessage()")
        page.wait_for_timeout(15000)   # let the Gemini session answer
        shot(page, D, "E1_dashboard_user_list")

        state = helper_get("/state")
        router_seen = any("USER: Users:" in ln for ln in state["log_tail"])
        check("dashboard '/user list' reached the command router",
              router_seen,
              f"router log 'USER: Users:' present: {router_seen}")

        # --- contrast: same command via the Qt-HUD router path --------------
        helper_post(f"/hud_text?text={quote('/user list')}")
        page.wait_for_timeout(6000)
        state2 = helper_get("/state")
        router_seen2 = any("USER: Users:" in ln for ln in state2["log_tail"])
        hud_reply = [t for s, t in chat_log_from_history()
                     if t.startswith("Users:")][-1:] or ["<none>"]
        check("Qt-HUD '/user list' DOES reach the router (contrast)",
              router_seen2, f"reply: {hud_reply[0][:90]}")
        shot(page, D, "E2_hud_path_user_list")

        # static: any /user or /cost surface in app.html?
        html = (Path(__file__).resolve().parents[3]
                / "dashboard" / "static" / "app.html").read_text("utf-8")
        check("app.html has NO /user UI surface (gap finding)", "/user" not in html)
        check("app.html has NO /cost UI surface (gap finding)", "/cost" not in html)

        cap.dump(D)
        ctx.close()
        b.close()

    write_log(D, "E_results.log", R)
    print(f"\n{sum(1 for x in R if x.startswith('PASS'))}/{len(R)} passed")


if __name__ == "__main__":
    main()
