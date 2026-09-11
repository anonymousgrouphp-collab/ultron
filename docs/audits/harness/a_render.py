"""Checklist A — load & render on the live dashboard (desktop 1280x800).

First paint, boot overlay lift, console errors, asset statuses, HUD mirror
(round-trip: dashboard toggle_mic -> app state -> broadcast state back).
Evidence -> docs/audits/evidence/A_render/ (+ A_hud for the Qt window).
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import (BASE, evdir, helper_get, mint, shot,  # noqa: E402
                       write_log)

D = evdir("A_render")
R: list[str] = []


def check(name, ok, detail=""):
    line = f"{'PASS' if ok else 'FAIL'} | {name} | {detail}"
    R.append(line)
    print(line)


def main() -> None:
    from playwright.sync_api import sync_playwright
    url = mint()["auto_login"]
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 1280, "height": 800},
                            ignore_https_errors=True)
        page = ctx.new_page()
        cap = None

        from pw_common import Captured
        cap = Captured(page)
        t0 = time.time()
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_url(f"{BASE}/", timeout=15000,
                          wait_until="domcontentloaded")
        # boot overlay timing
        page.wait_for_selector("#boot", state="attached")
        lifted = page.wait_for_selector("#boot.hidden", state="attached",
                                        timeout=10000) is not None
        check("boot overlay lifts (~2.6s script)", lifted,
              f"t={time.time()-t0:.1f}s incl. navigation")
        page.wait_for_function(
            "document.querySelector('#net-status')?.textContent === 'STABLE'",
            timeout=20000)  # WS connected
        page.wait_for_timeout(2000)  # reactor canvas window

        shot(page, D, "A1_desktop_1280x800")

        # core HUD elements visible
        for sel, name in [
            (".brand", "brand ULTRON"), ("#sys-status-badge", "status badge"),
            (".voice", "voice bar"), ("#memory-log-list", "core activity feed"),
            (".reactor-readout", "reactor overlay"),
        ]:
            vis = page.locator(sel).first.is_visible()
            check(f"render: {name} visible", vis)

        # canvas (three.js reactor) actually rendering?
        has_canvas = page.evaluate(
            "!!document.querySelector('#webgl canvas')")
        check("three.js reactor canvas mounted", has_canvas)

        # state round-trip: dashboard mute -> app -> broadcast back to ALL views
        badge_before = page.text_content("#sys-status-badge")
        page.evaluate("toggleVoiceListening()")     # product path: /toggle_mic
        page.wait_for_timeout(1500)
        state = helper_get("/state")
        badge_after = page.text_content("#sys-status-badge")
        check("HUD mirror: dashboard /toggle_mic -> app muted=true",
              state["muted"] is True, f"muted={state['muted']}")
        check("HUD mirror: state broadcast back to dashboard view",
              "MUTED" in (badge_after or "") or "MUTED" in (badge_before or ""),
              f"badge '{badge_before}' -> '{badge_after}', ui_state="
              f"{state['ui_state']}")
        shot(page, D, "A2_after_mute_state")
        # restore
        page.evaluate("toggleVoiceListening()")
        page.wait_for_timeout(800)

        # console errors on load (exclude the CDN three.js deprecation noise)
        errs = cap.console_errors()
        check("no console errors on load", not errs, "; ".join(errs[:3]))

        # static assets + CDN
        bad = [r for r in cap.responses if r.startswith(("4", "5"))]
        cdn = [r for r in cap.responses if "jsdelivr" in r]
        check("no 4xx/5xx asset responses", not bad, "; ".join(bad[:4]))
        check("three.js CDN modules loaded (reactor)", len(cdn) >= 1,
              f"{len(cdn)} CDN responses")

        cap.dump(D)
        ctx.close()

        # unauth view: '/' without token redirects to /login
        ctx2 = b.new_context(viewport={"width": 1280, "height": 800},
                             ignore_https_errors=True)
        p2 = ctx2.new_page()
        cap2 = Captured(p2)
        p2.goto(f"{BASE}/", wait_until="domcontentloaded")
        p2.wait_for_timeout(800)
        check("no-token view redirected to /login",
              p2.url.endswith("/login"), p2.url)
        shot(p2, D, "A3_login_redirect")
        cap2.dump(D)
        ctx2.close()
        b.close()

    write_log(D, "A_results.log", R)
    print(f"\n{sum(1 for x in R if x.startswith('PASS'))}/{len(R)} passed")


if __name__ == "__main__":
    main()
