"""Checklist B — auth & security regression against the LIVE app.

No-token page never mints; plaintext commands -> 400; encrypted round-trip;
unauth /ws -> 4001; forged token -> 401; token cap 64; TLS 8001 serves;
uploads/auth walls. Evidence -> docs/audits/evidence/B_auth/.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import ssl
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from pw_common import BASE, evdir, mint, write_log  # noqa: E402

D = evdir("B_auth")
RESULTS: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    line = f"{'PASS' if ok else 'FAIL'} | {name} | {detail}"
    RESULTS.append(line)
    print(line)


def http(method: str, url: str, body: dict | None = None,
         token: str | None = None) -> tuple[int, str, dict]:
    req = urllib.request.Request(url, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = json.dumps(body).encode() if body is not None else None
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    try:
        with urllib.request.urlopen(req, data=data, context=ctx,
                                    timeout=15) as r:
            return r.status, r.read().decode("utf-8", "replace"), dict(r.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), dict(e.headers)


def aes_encrypt(session_key: str, text: str) -> str:
    """Mirror server._decrypt_cbc: PBKDF2-HMAC-SHA256(salt,100k)->AES-CBC."""
    from cryptography.hazmat.primitives import padding as sym_pad
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    key = hashlib.pbkdf2_hmac(
        "sha256", session_key.encode(), b"ULTRON-DASHBOARD-v1", 100000)
    iv = b"\x3a" * 16  # fixed IV fine for audit round-trip proof
    raw = text.encode()
    padder = sym_pad.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(iv + ct).decode()


def get_session() -> tuple[str, str]:
    """mint -> auto-login -> (token, session_key)."""
    m = mint()
    status, html, _ = http("GET", f"{BASE}/auto-login?key={m['key']}")
    assert status == 200 and "sessionStorage" in html, (status, html[:200])
    tok = re.search(r"ultron_token','([^']+)'", html).group(1)
    key = re.search(r"ultron_key','([^']+)'", html).group(1)
    return tok, key


def main() -> None:
    # 1. GET / with no token: serves shell with __TOKEN__ placeholder, mints nothing
    s, body, hdrs = http("GET", f"{BASE}/")
    check("GET / no token -> 200 shell, __TOKEN__ placeholder (no mint)",
          s == 200 and "__TOKEN__" in body and "set-cookie" not in
          {k.lower() for k in hdrs}, f"status={s}")

    # 2. GET /login serves the login page
    s, body, _ = http("GET", f"{BASE}/login")
    check("GET /login -> 200 login page", s == 200 and "login" in body.lower())

    # 3. Command endpoint auth wall
    s, body, _ = http("POST", f"{BASE}/api/command", {"enc": ""})
    check("POST /api/command unauth -> 401", s == 401, f"status={s}")

    # 4. Forged token
    s, body, _ = http("POST", f"{BASE}/api/command", {"enc": "x"},
                      token="FORGED" + "a" * 40)
    check("POST /api/command forged token -> 401", s == 401, f"status={s}")

    # 5. Plaintext rejected (authed, no enc)
    tok, key = get_session()
    s, body, _ = http("POST", f"{BASE}/api/command", {"text": "open notepad"},
                      token=tok)
    check("POST /api/command authed PLAINTEXT -> 400", s == 400,
          f"status={s} body={body[:80]}")

    # 6. Garbage ciphertext -> 400
    s, body, _ = http("POST", f"{BASE}/api/command", {"enc": "AAAA"}, token=tok)
    check("POST /api/command garbage ciphertext -> 400", s == 400,
          f"status={s}")

    # 7. Valid encrypted round-trip -> ok:true, command reaches app
    enc = aes_encrypt(key, "system status")
    s, body, _ = http("POST", f"{BASE}/api/command", {"enc": enc}, token=tok)
    check("POST /api/command AES-256-CBC encrypted -> 200 ok",
          s == 200 and json.loads(body).get("ok") is True,
          f"status={s} body={body[:80]}")

    # 8. Other authed endpoints
    s, _, _ = http("GET", f"{BASE}/api/files", token=None)
    check("GET /api/files unauth -> 401", s == 401)
    s, _, _ = http("POST", f"{BASE}/api/upload")
    check("POST /api/upload unauth -> 401", s == 401)
    s, _, _ = http("GET", f"{BASE}/uploads/whatever.txt")
    check("GET /uploads/... unauth -> 401", s == 401)
    s, _, _ = http("POST", f"{BASE}/api/device-login",
                   {"device_token": "bogus"})
    check("POST /api/device-login bogus device token -> 401", s == 401)
    s, _, _ = http("POST", f"{BASE}/api/revoke-devices")
    check("POST /api/revoke-devices unauth -> 401", s == 401)

    # 9. Login with wrong pin -> 401; expired key -> Link Expired page
    s, body, _ = http("POST", f"{BASE}/login", {"pin": "ZZZZZZ"})
    check("POST /login wrong PIN -> 401", s == 401, f"status={s}")
    s, body, _ = http("GET", f"{BASE}/auto-login?key=NOPE12")
    check("GET /auto-login expired key -> Link Expired page",
          s == 200 and "Link Expired" in body)

    # 10. /static/crypto.js served locally (no CDN dependency over HTTP)
    s, body, _ = http("GET", f"{BASE}/static/crypto.js")
    check("GET /static/crypto.js -> 200 vendored asset", s == 200,
          f"bytes={len(body)}")

    # 11. Token cap: mint 70 sessions; behavioral proof of prune —
    # earliest token must be dropped (401), newest must survive (200)
    toks: list[str] = []
    for i in range(70):
        t, _k = get_session()
        toks.append(t)
    s_first, _, _ = http("GET", f"{BASE}/api/files", token=toks[0])
    s_last, _, _ = http("GET", f"{BASE}/api/files", token=toks[-1])
    check("token cap 64 enforced (70 mints -> oldest pruned, newest valid)",
          s_first == 401 and s_last == 200,
          f"oldest={s_first} newest={s_last}")

    # 12. TLS alias 8001 accepts + serves (self-signed accepted here)
    s, body, _ = http("GET", "https://127.0.0.1:8001/login")
    check("TLS 8001 serves /login (self-signed accept path)", s == 200)
    s, body, _ = http("GET", f"{BASE}/login")
    check("TLS 8000 serves /login", s == 200)

    # 13. WS close codes via browser (Playwright part)
    ws_check()

    # 14. Static greps: no secrets, no 0.0.0.0
    static_greps()

    write_log(D, "B_results.log", RESULTS)
    print(f"\n{sum(1 for r in RESULTS if r.startswith('PASS'))}/"
          f"{len(RESULTS)} checks passed — evidence in {D}")


def ws_check() -> None:
    from playwright.sync_api import sync_playwright
    tok, _key = get_session()
    with sync_playwright() as pw:
        b = pw.chromium.launch()
        ctx = b.new_context(ignore_https_errors=True)
        page = ctx.new_page()
        page.goto(f"{BASE}/login", wait_until="domcontentloaded")

        unauth = page.evaluate(
            "new Promise(res => { const w = new WebSocket('wss://127.0.0.1:8000/ws');"
            " w.onclose = e => res(e.code); w.onerror = () => {};"
            " setTimeout(() => res('timeout'), 6000); })")
        check("WS /ws unauth -> close 4001", unauth == 4001, f"code={unauth}")

        auth = page.evaluate(
            "code => new Promise(res => { const w = new WebSocket("
            "'wss://127.0.0.1:8000/ws?token=' + encodeURIComponent(code));"
            " w.onopen = () => { res('open'); w.close(); };"
            " w.onclose = e => res(e.code); w.onerror = () => {};"
            " setTimeout(() => res('timeout'), 6000); })", tok)
        check("WS /ws authed token -> open", auth == "open", f"result={auth}")

        # history replay on connect (authed WS receives backlog)
        backlog = page.evaluate(
            """code => new Promise(res => {
                 const w = new WebSocket('wss://127.0.0.1:8000/ws?token=' +
                                         encodeURIComponent(code));
                 const got = [];
                 w.onmessage = e => { got.push(e.data);
                   if (got.length >= 1) { res(got.length); w.close(); } };
                 w.onclose = () => res(got.length);
                 setTimeout(() => res(got.length), 4000); })""", tok)
        check("WS authed connect receives history replay", backlog >= 1,
              f"replayed={backlog}")
        b.close()


def static_greps() -> None:
    import subprocess
    static = Path(__file__).resolve().parents[3] / "dashboard" / "static"
    blob = "\n".join(
        p.read_text(encoding="utf-8", errors="replace")
        for p in static.iterdir() if p.suffix in (".html", ".js"))
    secret_like = re.findall(
        r"AIza[0-9A-Za-z_\-]{20,}|sk-[0-9A-Za-z]{20,}|Bearer [A-Za-z0-9_\-]{20,}",
        blob)
    check("no secrets in static JS/HTML", not secret_like,
          f"hits={secret_like[:2]}")
    repo_root = Path(__file__).resolve().parents[3]
    targets = list((repo_root / "dashboard").rglob("*")) + [repo_root / "main.py"]
    host_hits = []
    for t in targets:
        if t.is_file() and t.suffix in (".py", ".html", ".js", ".json"):
            txt = t.read_text(encoding="utf-8", errors="replace")
            for idx, line in enumerate(txt.splitlines(), 1):
                if "0.0.0.0" in line:
                    host_hits.append(f"{t.name}:{idx}: {line.strip()}")
    check("no 0.0.0.0 binding in dashboard/main", not host_hits,
          "; ".join(host_hits[:2]) or "clean")
    binds = subprocess.run(
        ["netstat", "-ano"], capture_output=True, text=True).stdout
    listen = [ln for ln in binds.splitlines()
              if (":8000" in ln or ":8001" in ln) and "LISTEN" in ln]
    loopback_only = all("127.0.0.1:" in ln for ln in listen)
    check("live listeners 8000/8001 bound 127.0.0.1 only", loopback_only and listen,
          "; ".join(x.strip()[:60] for x in listen[:2]))


if __name__ == "__main__":
    main()
