# TLS certificates — local only, never committed

The dashboard serves HTTPS with a self-signed certificate from this folder
(`ultron.key` + `ultron.crt`, see `dashboard/server.py`).

Private keys in this folder are **untracked on purpose** (`.gitignore`): an
earlier pair was committed to a public repository and is considered burned —
it was removed from the current tree and rotated on 2026-09-07; history purge remains scheduled separately
(PROGRESS.md → P0-B1). Never re-add keys, certs, or tokens to git.

If `ultron.key`/`ultron.crt` are missing, the dashboard falls back to plain
HTTP on `127.0.0.1` (safe default). To regenerate a fresh self-signed pair
(Git Bash / OpenSSL):

    MSYS_NO_PATHCONV=1 openssl req -x509 -newkey rsa:2048 -nodes \
      -keyout config/certs/ultron.key -out config/certs/ultron.crt \
      -days 3650 -subj "/CN=ULTRON"

Browsers will show a self-signed-certificate warning once per device;
that warning is expected for a local assistant.

The current dashboard binds loopback (`127.0.0.1`) only. These certificates do
not authorize LAN or remote control; that capability is unavailable until a
separate remote-security design is implemented.
