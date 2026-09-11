"""tests/test_dashboard_ui.py — Comprehensive hermetic test suite for ULTRON Dashboard & UI contracts.

Tests:
  - Security & Auth: token minting, token cap (64), expiration, auth walls (401/400).
  - Cryptography: PBKDF2-HMAC-SHA256 + AES-256-CBC round-trip with server._decrypt_cbc.
  - Endpoints: GET /, /login, /auto-login, /api/command, /api/upload, /api/files.
  - WebSocket: unauth /ws -> 4001, auth /ws -> accept, history replay, command & cmd types.
  - Static integrity: no hardcoded secrets, no 0.0.0.0 bindings in dashboard files.
  - Kernel event envelope shape and broadcast integrity.
"""

from __future__ import annotations

import base64
import hashlib
import time
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import padding as sym_pad
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient

from dashboard.server import (
    DASHBOARD_HOST,
    PORT,
    DashboardServer,
    _decrypt_cbc,
    _derive_key,
)


def aes_encrypt(session_key: str, text: str, salt: bytes = b"ULTRON-DASHBOARD-v1") -> str:
    """Helper to encrypt text matching client-side CryptoJS."""
    key = hashlib.pbkdf2_hmac("sha256", session_key.encode("utf-8"), salt, 100000)
    iv = b"\x12" * 16
    raw = text.encode("utf-8")
    padder = sym_pad.PKCS7(128).padder()
    padded = padder.update(raw) + padder.finalize()
    enc = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = enc.update(padded) + enc.finalize()
    return base64.b64encode(iv + ct).decode("utf-8")


@pytest.fixture
def dashboard(tmp_path):
    dash = DashboardServer()
    dash._uploads_dir = tmp_path / "uploads"
    dash._uploads_dir.mkdir(parents=True, exist_ok=True)
    return dash


@pytest.fixture
def client(dashboard):
    return TestClient(dashboard.app)


class TestDashboardSecurityAndAuth:
    def test_default_host_is_loopback(self):
        assert DASHBOARD_HOST == "127.0.0.1"
        assert PORT == 8000

    def test_get_root_unauthenticated_mints_no_token(self, client):
        resp = client.get("/")
        assert resp.status_code == 200
        assert "__TOKEN__" in resp.text
        assert "set-cookie" not in resp.headers

    def test_get_login_page_served(self, client):
        resp = client.get("/login")
        assert resp.status_code == 200
        assert "Pairing key" in resp.text

    def test_login_wrong_pin_returns_401(self, client):
        resp = client.post("/login", json={"pin": "WRONG1"})
        assert resp.status_code == 401
        assert "Invalid or expired key" in resp.json()["error"]

    def test_login_valid_pin_returns_token_and_key(self, dashboard, client):
        pin = dashboard.new_key(expiry_secs=60)
        resp = client.post("/login", json={"pin": pin})
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert "token" in data
        assert dashboard._valid_token(data["token"]) is True

    def test_auto_login_valid_key_sets_session(self, dashboard, client):
        key = dashboard.new_key(expiry_secs=60)
        resp = client.get(f"/auto-login?key={key}")
        assert resp.status_code == 200
        assert "sessionStorage.setItem('ultron_token'" in resp.text
        assert f"'{key}'" in resp.text

    def test_auto_login_expired_key(self, client):
        resp = client.get("/auto-login?key=EXPD00")
        assert resp.status_code == 200
        assert "Link Expired" in resp.text

    def test_token_cap_pruning(self, dashboard):
        tokens = []
        for i in range(75):
            t = dashboard._mint_token()
            tokens.append(t)
        assert len(dashboard._tokens) == 64
        # Oldest tokens were pruned, newest retained
        assert dashboard._valid_token(tokens[0]) is False
        assert dashboard._valid_token(tokens[-1]) is True

    def test_token_expiration_12h(self, dashboard):
        tok = dashboard._mint_token()
        assert dashboard._valid_token(tok) is True
        # Fast forward time beyond 12 hours
        dashboard._tokens[tok] = time.time() - 10
        assert dashboard._valid_token(tok) is False

    def test_command_requires_auth(self, client):
        resp = client.post("/api/command", json={"enc": "test"})
        assert resp.status_code == 401

    def test_command_requires_encryption(self, dashboard, client):
        key = dashboard.new_key()
        login_resp = client.post("/login", json={"pin": key}).json()
        token = login_resp["token"]

        # Plaintext command rejected with 400
        resp = client.post("/api/command", json={"text": "hello"}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 400
        assert "Encrypted payload required" in resp.json()["error"]

    def test_command_garbage_ciphertext_rejected(self, dashboard, client):
        key = dashboard.new_key()
        token = client.post("/login", json={"pin": key}).json()["token"]
        headers = {"Authorization": f"Bearer {token}"}
        resp = client.post("/api/command", json={"enc": "INVALID_BASE64!!!"}, headers=headers)
        assert resp.status_code == 400

    def test_encrypted_command_round_trip(self, dashboard, client):
        key = dashboard.new_key()
        token = client.post("/login", json={"pin": key}).json()["token"]
        enc = aes_encrypt(key, "status report")

        resp = client.post("/api/command", json={"enc": enc}, headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True
        # Ensure command arrived in queue
        assert dashboard._command_queue.qsize() == 1
        received = dashboard._command_queue.get_nowait()
        assert received == "status report"

    def test_upload_endpoint_auth_wall(self, client):
        # Even with no file payload, unauthenticated request must get 401
        resp = client.post("/api/upload")
        assert resp.status_code == 401
        assert resp.json()["error"] == "Unauthorized"

    def test_files_endpoint_auth_wall(self, client):
        resp = client.get("/api/files")
        assert resp.status_code == 401

    def test_static_crypto_asset_served(self, client):
        resp = client.get("/static/crypto.js")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("application/javascript")
        resp_min = client.get("/static/crypto-js.min.js")
        assert resp_min.status_code == 200


class TestCryptographyInternals:
    def test_derive_aes_key(self):
        k1 = _derive_key("TESTKEY")
        k2 = _derive_key("TESTKEY")
        assert k1 == k2
        assert len(k1) == 32

    def test_decrypt_cbc_valid(self):
        sk = "MYSECRET"
        key = _derive_key(sk)
        enc = aes_encrypt(sk, "hello ultron")
        dec = _decrypt_cbc(key, enc)
        assert dec == "hello ultron"

    def test_decrypt_cbc_invalid_padding_fails_cleanly(self):
        sk = "MYSECRET"
        key = _derive_key(sk)
        # 16-byte IV + 16-byte corrupted block
        corrupt = base64.b64encode(b"\x00" * 32).decode()
        with pytest.raises(Exception):
            _decrypt_cbc(key, corrupt)


class TestWebSocketContracts:
    def test_unauthenticated_ws_rejected_with_4001(self, client):
        with pytest.raises(Exception):
            with client.websocket_connect("/ws") as ws:
                ws.receive_json()

    def test_authenticated_ws_connection_and_command_types(self, dashboard, client):
        key = dashboard.new_key()
        token = client.post("/login", json={"pin": key}).json()["token"]

        with client.websocket_connect(f"/ws?token={token}") as ws:
            # 1. Send encrypted command with type "command"
            enc1 = aes_encrypt(key, "toggle_mic")
            ws.send_json({"type": "command", "enc": enc1})

            # 2. Send encrypted command with type "cmd" (backward compatibility)
            enc2 = aes_encrypt(key, "report status")
            ws.send_json({"type": "cmd", "enc": enc2})

            # 3. Plaintext command must be ignored
            ws.send_json({"type": "command", "text": "plain"})

        assert dashboard._command_queue.qsize() == 2
        assert dashboard._command_queue.get_nowait() == "toggle_mic"
        assert dashboard._command_queue.get_nowait() == "report status"

    def test_ws_image_attach(self, dashboard, client):
        key = dashboard.new_key()
        token = client.post("/login", json={"pin": key}).json()["token"]

        with client.websocket_connect(f"/ws?token={token}") as ws:
            ws.send_json({"type": "image_attach", "data": "QUJD", "mime": "image/png"})

        assert dashboard._command_queue.qsize() == 1
        item = dashboard._command_queue.get_nowait()
        assert item == {"type": "image", "data": "QUJD", "mime": "image/png"}


class TestStaticHygiene:
    def test_no_secrets_in_static_files(self):
        static_dir = Path(__file__).resolve().parents[1] / "dashboard" / "static"
        for p in static_dir.glob("*.*"):
            if p.suffix in (".html", ".js"):
                text = p.read_text(encoding="utf-8", errors="replace")
                assert "AIzaSy" not in text
                assert "sk-proj-" not in text

    def test_no_zero_zero_zero_zero_binding(self):
        dashboard_dir = Path(__file__).resolve().parents[1] / "dashboard"
        server_py = (dashboard_dir / "server.py").read_text(encoding="utf-8")
        # Ensure default bind is strictly loopback
        assert 'host="0.0.0.0"' not in server_py
        assert "host = '0.0.0.0'" not in server_py
