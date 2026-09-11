"""Checklist D (+F parts) — remote control flows on the live dashboard.

Mute/unmute, text command → Gemini, image upload path, reconnect after
server restart (kill+relaunch), malformed WS message handling, history
replay duplication. Evidence -> docs/audits/evidence/D_remote/.
"""

from __future__ import annotations

import base64
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import (evdir, helper_get, mint, shot, write_log)  # noqa: E402

D = evdir("D_remote")
R: list[str] = []

# 1x1 red PNG (no PIL needed)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def check(name, ok, detail=""):
    line = f"{'PASS' if ok else 'FAIL'} | {name} | {detail}"
    R.append(line)
    print(line)


def wait_boot(page):
    page.goto(mint()["auto_login"], wait_until="domcontentloaded")
    page.wait_for_timeout(3000)
    page.wait_for_selector("#boot.hidden", state="attached", timeout=15000)
    page.wait_for_function(
        "document.querySelector('#net-status')?.textContent === 'STABLE'",
        timeout=25000)


def main() -> None:
    from playwright.sync_api import sync_playwright
    png = D / "audit_probe.png"
    png.write_bytes(PNG)

    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(viewport={"width": 390, "height": 844},
                            ignore_https_errors=True)   # PHONE view
        page = ctx.new_page()
        from pw_common import Captured
        cap = Captured(page)
        wait_boot(page)
        shot(page, D, "D0_phone_390x844")

        # 1. mute/unmute from the phone
        page.evaluate("toggleVoiceListening()")
        page.wait_for_timeout(1500)
        m1 = helper_get("/state")
        page.evaluate("toggleVoiceListening()")
        m2 = helper_get("/state")
        for _ in range(10):
            if m2.get("muted") is False:
                break
            page.wait_for_timeout(500)
            m2 = helper_get("/state")
        check("phone mute/unmute round-trip reaches the app",
              m1["muted"] is True and m2["muted"] is False,
              f"muted {m1['muted']} -> {m2['muted']}")
        shot(page, D, "D1_after_mute_toggle")

        # 2. text command from the phone -> Gemini session
        page.evaluate("openModal('chat')")
        page.fill("#chat-input-field", "Reply with exactly: AUDIT-D-OK")
        page.evaluate("sendChatMessage()")
        got = False
        for _ in range(5):
            page.wait_for_timeout(1000)
            txt = page.evaluate(
                "document.querySelector('#chat-feed').innerText")
            if "AUDIT-D-OK" in txt:
                got = True
                break
        check("phone text command -> Gemini session reply rendered", got,
              "reply 'AUDIT-D-OK' seen" if got else "no reply (session dormant / command dropped)")
        shot(page, D, "D2_text_command_reply")

        # 3. image upload -> 'Image received. ULTRON analyzing image...'
        page.evaluate("closeModal('chat')")
        page.set_input_files("#image-upload-input", str(png))
        img_ok = False
        for _ in range(5):
            page.wait_for_timeout(1000)
            st = helper_get("/state")
            if any("Image received" in ln for ln in st["log_tail"]):
                img_ok = True
                break
        check("image upload reaches app ('Image received...' log)", img_ok,
              "server log confirms" if img_ok else "no log (session dormant / command dropped)")
        shot(page, D, "D3_image_upload")

        # 4. phone mic relay: NO client surface — static evidence only
        html = (Path(__file__).resolve().parents[3]
                / "dashboard" / "static" / "app.html").read_text("utf-8")
        check("phone-mic WS client absent from app.html (gap finding)",
              "phone-audio" not in html,
              "server /ws/phone-audio has no dashboard caller")

        # 5. malformed WS message: server must not crash/drop silently (F)
        malformed = page.evaluate("""() => new Promise(res => {
          const w = new WebSocket('wss://127.0.0.1:8000/ws?token=' +
              encodeURIComponent(sessionStorage.getItem('ultron_token')));
          w.onopen = () => { w.send('this is not json'); };
          w.onclose = e => res('closed:' + e.code);
          w.onerror = () => {};
          setTimeout(() => res(w.readyState === 1 ? 'stayed-open' : 'unknown'), 4000);
        })""")
        check("malformed WS text frame handled gracefully", malformed == "stayed-open",
              f"result={malformed}")

        # 6. reconnect after server restart (kill + relaunch)
        rows_before = page.evaluate(
            "document.querySelectorAll('#memory-log-list > div').length")
        import os
        netstat = subprocess.run(["netstat", "-ano"], capture_output=True, text=True).stdout
        boot_pid = None
        for ln in netstat.splitlines():
            if ":39153" in ln and "LISTEN" in ln:
                boot_pid = ln.strip().split()[-1]
                break
        if boot_pid and boot_pid != str(os.getpid()):
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"Stop-Process -Id {boot_pid} -Force"],
                           capture_output=True)
        page.wait_for_timeout(4000)
        net_during = page.text_content("#net-status")
        close_codes = [entry for entry in cap.ws_pages if "(closed)" in entry]
        shot(page, D, "D4_reconnecting_after_kill")
        check("WS drop detected -> RECONNECTING UX", net_during == "RECONNECTING",
              f"net-status='{net_during}' closes={len(close_codes)}")

        repo_dir = str(Path(__file__).resolve().parents[3])
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"Start-Process -FilePath 'py' -ArgumentList '-3.14','-u',"
                        f"'docs\\audits\\harness\\boot_app.py' -WorkingDirectory '{repo_dir}'"],
                       capture_output=True)
        time.sleep(20)
        stable = page.evaluate(
            "document.querySelector('#net-status')?.textContent")
        # give the 2s retry loop time to land
        for _ in range(20):
            if stable == "STABLE":
                break
            page.wait_for_timeout(2000)
            stable = page.evaluate(
                "document.querySelector('#net-status')?.textContent")
        rows_after = page.evaluate(
            "document.querySelectorAll('#memory-log-list > div').length")
        check("dashboard reconnects after app restart", stable == "STABLE",
              f"net-status='{stable}'")
        check("history replay after reconnect DUPLICATES feed rows (finding)",
              rows_after > rows_before * 1.5,
              f"rows {rows_before} -> {rows_after} (replay re-renders backlog)")
        cap.dump(D)
        ctx.close()
        b.close()

    write_log(D, "D_results.log", R)
    print(f"\n{sum(1 for x in R if x.startswith('PASS'))}/{len(R)} passed")


if __name__ == "__main__":
    main()
