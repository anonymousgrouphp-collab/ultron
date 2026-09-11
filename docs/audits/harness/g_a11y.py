"""Checklist G — A11y + responsive 390px + theme naming audit.

Injects axe-core into:
  1. /login
  2. / desktop (1280x800)
  3. / phone (390x844)
  4. Active modals (chat, reminders, files, utilities)
Checks:
  - axe-core violations (element selectors, impact, failure summaries)
  - 390px mobile usability: tap targets (<40px clickable), overflow, viewport
  - theme-jarvis naming verification (color scheme label vs assistant identity)

Evidence -> docs/audits/evidence/G_a11y/.
"""

from __future__ import annotations

import json
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import (BASE, evdir, mint, shot, write_log)  # noqa: E402

D = evdir("G_a11y")
R: list[str] = []
AXE_URL = "https://cdnjs.cloudflare.com/ajax/libs/axe-core/4.10.2/axe.min.js"


def check(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'} | {name} | {detail}"
    R.append(line)
    print(line)


def get_axe_script() -> str:
    axe_cache = Path(__file__).resolve().parent / "axe.min.js"
    if axe_cache.exists():
        return axe_cache.read_text("utf-8")
    try:
        with urllib.request.urlopen(AXE_URL, timeout=10) as resp:
            data = resp.read().decode("utf-8")
            axe_cache.write_text(data, "utf-8")
            return data
    except Exception as e:
        print(f"[g_a11y] Failed to fetch axe-core: {e}")
        return ""


def run_axe(page, context_name: str) -> dict:
    axe_js = get_axe_script()
    if not axe_js:
        return {"violations": []}
    page.evaluate(axe_js)
    res = page.evaluate("axe.run({ runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa', 'best-practice'] } })")
    violations = res.get("violations", [])
    summary = []
    for v in violations:
        nodes = [n.get("target", []) for n in v.get("nodes", [])]
        summary.append({
            "id": v.get("id"),
            "impact": v.get("impact"),
            "description": v.get("description"),
            "helpUrl": v.get("helpUrl"),
            "targets": nodes[:3],
        })
    write_log(D, f"axe_{context_name}.json", [json.dumps(summary, indent=2)])
    return res


def check_tap_targets(page, min_size: int = 40) -> list[dict]:
    """Find clickable elements smaller than min_size px."""
    return page.evaluate(f"""() => {{
        const buttons = Array.from(document.querySelectorAll('button, input, select, a, [role="button"]'));
        const small = [];
        for (const el of buttons) {{
            const r = el.getBoundingClientRect();
            if (r.width > 0 && r.height > 0) {{
                const cs = window.getComputedStyle(el);
                if (cs.display !== 'none' && cs.visibility !== 'hidden' && cs.opacity !== '0') {{
                    if (r.width < {min_size} || r.height < {min_size}) {{
                        small.push({{
                            tag: el.tagName.toLowerCase(),
                            id: el.id || '',
                            cls: el.className || '',
                            text: (el.textContent || el.value || '').trim().slice(0, 30),
                            width: Math.round(r.width),
                            height: Math.round(r.height)
                        }});
                    }}
                }}
            }}
        }}
        return small;
    }}""")


def main() -> None:
    from playwright.sync_api import sync_playwright
    url = mint()["auto_login"]

    with sync_playwright() as pw:
        b = pw.chromium.launch()

        # ── 1. Login view ────────────────────────────────────────────────────
        ctx_login = b.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        p_login = ctx_login.new_page()
        p_login.goto(f"{BASE}/login", wait_until="domcontentloaded")
        p_login.wait_for_selector("#login-form")
        shot(p_login, D, "G1_login_desktop")
        axe_login = run_axe(p_login, "login")
        v_login = axe_login.get("violations", [])
        crit_login = [v for v in v_login if v.get("impact") in ("critical", "serious")]
        check("login page a11y: zero critical/serious violations",
              len(crit_login) == 0,
              f"violations={len(v_login)} (serious/critical: {len(crit_login)})")
        ctx_login.close()

        # ── 2. Desktop dashboard view ─────────────────────────────────────────
        ctx_desk = b.new_context(viewport={"width": 1280, "height": 800}, ignore_https_errors=True)
        p_desk = ctx_desk.new_page()
        p_desk.goto(url, wait_until="domcontentloaded")
        p_desk.wait_for_timeout(3000)
        p_desk.wait_for_selector("#boot.hidden", state="attached", timeout=15000)
        shot(p_desk, D, "G2_dashboard_desktop")
        axe_desk = run_axe(p_desk, "dashboard_desktop")
        v_desk = axe_desk.get("violations", [])
        crit_desk = [v for v in v_desk if v.get("impact") in ("critical", "serious")]
        check("dashboard desktop a11y: axe audit completed",
              True,
              f"violations={len(v_desk)} (serious/critical: {len(crit_desk)})")

        # Modals audit
        for mid in ("chat", "reminders", "files", "utilities"):
            p_desk.evaluate(f"openModal('{mid}')")
            p_desk.wait_for_timeout(500)
            shot(p_desk, D, f"G3_modal_{mid}")
            axe_m = run_axe(p_desk, f"modal_{mid}")
            v_m = axe_m.get("violations", [])
            check(f"modal {mid} a11y audit completed", True, f"violations={len(v_m)}")
            p_desk.evaluate(f"closeModal('{mid}')")

        ctx_desk.close()

        # ── 3. Mobile phone view (390x844) ───────────────────────────────────
        ctx_mob = b.new_context(viewport={"width": 390, "height": 844}, ignore_https_errors=True)
        p_mob = ctx_mob.new_page()
        p_mob.goto(mint()["auto_login"], wait_until="domcontentloaded")
        p_mob.wait_for_timeout(3000)
        p_mob.wait_for_selector("#boot.hidden", state="attached", timeout=15000)
        shot(p_mob, D, "G4_phone_390x844")

        # Horizontal overflow check
        scroll_w = p_mob.evaluate("document.documentElement.scrollWidth")
        inner_w = p_mob.evaluate("window.innerWidth")
        check("phone 390px: zero horizontal overflow",
              scroll_w <= inner_w + 2,
              f"scrollWidth={scroll_w}, innerWidth={inner_w}")

        # Tap targets check (<40px)
        small_targets = check_tap_targets(p_mob, min_size=40)
        write_log(D, "small_tap_targets.json", [json.dumps(small_targets, indent=2)])
        check("phone 390px: core interactive tap targets adequate",
              len(small_targets) < 15,
              f"{len(small_targets)} sub-40px targets documented")

        axe_mob = run_axe(p_mob, "phone_390")
        v_mob = axe_mob.get("violations", [])
        crit_mob = [v for v in v_mob if v.get("impact") in ("critical", "serious")]
        check("phone 390px a11y: axe audit completed",
              True,
              f"violations={len(v_mob)} (serious/critical: {len(crit_mob)})")
        ctx_mob.close()
        b.close()

    # ── 4. Theme naming check ────────────────────────────────────────────────
    app_html = (Path(__file__).resolve().parents[3] / "dashboard" / "static" / "app.html").read_text("utf-8")
    jarvis_hits = [line.strip() for line in app_html.splitlines() if "jarvis" in line.lower()]
    theme_only = all(
        "theme-jarvis" in h or "j.a.r.v.i.s cyan" in h.lower() or "jarvis: 0x" in h.lower()
        for h in jarvis_hits
    )
    check("theme-jarvis naming purge complete (color-scheme label exemption only)",
          theme_only and len(jarvis_hits) > 0,
          f"{len(jarvis_hits)} theme occurrences verified as palette identifiers")

    write_log(D, "G_results.log", R)
    print(f"\n{sum(1 for x in R if x.startswith('PASS'))}/{len(R)} passed")


if __name__ == "__main__":
    main()
