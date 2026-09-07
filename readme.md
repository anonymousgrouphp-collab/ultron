# ULTRON

ULTRON is a local-first Windows desktop assistant under active reconstruction.
It is becoming a policy-controlled agent runtime rather than a collection of
desktop-automation demos. Read the [product requirements](docs/PRD.md) and
[technical architecture](docs/TECHNICAL_ARCHITECTURE.md) before enabling more
capabilities.

## Current safety posture

The live tool path now crosses the kernel registry, policy engine, and local
SQLite audit log. Read-only tools may run. Write and execute tools fail closed
until a user-facing consent prompt exists; destructive tools are denied by
default. The following legacy paths are deliberately unavailable:

- unsandboxed code execution and the old coding agent;
- automatic termination of user applications;
- shell-based application launching.

This is a development-stage desktop application. Do not use it to control
accounts, send messages, buy items, or manage irreplaceable files.

## Supported environment

- Windows 11
- CPython 3.13 installed with the Windows Python Launcher (`py`)
- Internet access for dependency installation and Gemini features

Python 3.10, 3.11, 3.12, and 3.14 are not supported launch targets. ULTRON
always runs from its project-local `.venv`; it does not use the first `python`
found on PATH.

## Setup

Clone a complete repository checkout, then double-click `SETUP.bat`. It creates
`.venv`, installs the pinned direct dependencies, and copies
`config/api_keys.json.example` to the ignored `config/api_keys.json` if needed.

From PowerShell:

```powershell
py -3.13 ULTRON_SETUP.py
```

Browser automation binaries are optional and downloaded only when needed:

```powershell
py -3.13 ULTRON_SETUP.py --with-browser
```

Setup never downloads or merges application source. If it reports missing files,
restore a clean checkout through Git or a verified archive rather than accepting
a self-updating installer.

Add a Gemini API key to `config/api_keys.json`, then launch with
`START_ULTRON.bat` or:

```powershell
.venv\Scripts\python.exe main.py
```

## Dashboard

The dashboard binds to `127.0.0.1` only. It is for the same computer only; the
legacy `ULTRON_DASHBOARD_HOST` setting is deliberately ignored. LAN and phone
remote control are not a Release 1 workflow. Reintroducing them requires a
separate remote-security design, not a self-signed certificate and pairing key.

## Wake word listener

`Start_ULTRON_Wake_Word.bat` is experimental. It uses the legacy
SpeechRecognition/PyAudio stack and is not installed by default; the launcher
will stop with a clear message when a compatible PyAudio wheel is unavailable.
The supported voice roadmap is documented in
[docs/research/02_voice_stack.md](docs/research/02_voice_stack.md).

## Development checks

```powershell
.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.venv\Scripts\python.exe -m pytest tests -q
.venv\Scripts\python.exe -m ruff check kernel tests ULTRON_SETUP.py
.venv\Scripts\python.exe -m mypy kernel
```

The release contract is defined by [docs/PRD.md](docs/PRD.md); passing unit tests
does not by itself mean the assistant is ready for autonomous desktop use.

## Repository map

- `kernel/` — typed events, tool registry, policy, audit, and migration seams
- `actions/` — legacy handlers being ported or retired; do not add new handlers
  here
- `dashboard/` — local FastAPI dashboard and static UI
- `config/` — one local configuration path; secrets are ignored by Git
- `docs/` — roadmap, product requirements, architecture, ADRs, and research
- `tests/` — characterization, kernel, migration, and setup regression coverage
