# 08 — Integrations & Automation Research (House, Media, Briefing, Presence)

*Research doc 8 of 8, companion to `01_jarvis_feature_catalog.md`. Covers **J-08**
(morning briefing), **J-14** (Home Assistant — "the mansion"), **J-15** (media),
**J-16/J-17** (cameras & security), **J-18** (multi-endpoint reach), **J-19**
(proactive triggers). Written 2026-09-07; sources verified Sep 2026.*

**ULTRON context:** the roadmap calls Home-Assistant-via-MCP "the cheapest wow moment
that exists." This doc picks the integration set and the wiring order. Everything here
plugs into the kernel as MCP servers or typed event sources — no new one-off action
modules (kill-list).

---

## 1. The mansion: Home Assistant (J-14) — the single biggest multiplier

| Option | What it is (Sep 2026) | How ULTRON uses it |
|---|---|---|
| **HA MCP integration / MCP Assist** ([community](https://community.home-assistant.io/t/mcp-assist-95-token-reduction-for-voice-assistants-with-local-cloud-llms/977977), [mike-nott/mcp-assist](https://github.com/mike-nott/mcp-assist)) | MCP server running on HA exposing **dynamic entity-discovery tools** — the LLM fetches only the entities it needs | Reported **~95% token reduction** vs dumping all entity states into the prompt. This is the exact pattern for our Tool Bus: mount HA's MCP server, let the kernel discover lights/climate/switches on demand |
| **HA REST + WebSocket API** (`homeassistant-api` / raw WS) | Direct API control, stable for years | Fallback + for kernel-native event subscriptions (state_changed feed → event bus) |
| **HA Assist pipeline** | HA's own wake→STT→intent→TTS voice stack | Not needed — ULTRON *is* the voice front-end; HA is the device layer behind MCP |

**Verdict:** one integration, thousands of devices. Setup: HA (Docker or HAOS box) →
install MCP server integration → allowlist ULTRON as MCP client → consent-gate
"write" classes (locks, alarms). "House settings" / "movie mode" / "good night"
become one LLM-planned tool sequence, not 35 hardcoded AppIDs.

## 2. Media with taste (J-15)

| Layer | Pick | Notes |
|---|---|---|
| YouTube Music | **ytmusicapi** (v1.12.2, active, MIT) — cookie-auth, search/playlists/library | Zero-cost path; `play <song/mood/artist>` tool; pairs with the existing `youtube_video.py` (retire it into this) |
| Spotify | **Spotify Web API** (`spotipy`, OAuth) | Playback control + library + recommendations; premium accounts only |
| Local library / cast | **Mopidy** or VLC control; HA `media_player` entities | Whole-home audio via HA speakers — arrives free with J-14 |
| Mood-aware selection | LLM picks from library metadata + time-of-day/memory context ("play something by the Bee Gees" = search → queue → play on the right speaker) | Preference memory (J-25) makes this feel uncanny over time |

## 3. Cameras & security (J-16/J-17)

| Component | Pick | Role |
|---|---|---|
| Webcam (USB) | OpenCV (`opencv-python`) capture | Day-1: desk presence, scene snapshots for the VLM (doc 03: Qwen3-VL describe-on-event) |
| IP cameras / NVR | **Frigate NVR** (blakeblackshear/frigate, ~18k★, local, Docker) | Real-time local object detection; feeds never leave the network. ULTRON subscribes to Frigate **MQTT events** instead of running its own detection |
| Event transport | **MQTT** via `paho-mqtt` (+ Mosquitto broker) | The house's nervous system: Frigate events, HA states, ULTRON commands all on one bus; kernel event bus bridges it |
| Notifications | **Frigate Notify** / HA blueprints (LLM recognition, cooldowns, multi-cam) | Proven patterns; ULTRON's proactive engine (J-19) consumes the same events and *speaks* alerts per consent policy |
| Face recognition | DeepFace/insightface (doc 03) on door-cam stills | "Sir, delivery at the door" vs unknown-person alert — gated, opt-in |

**Privacy rule:** cameras default to local-only processing; cloud VLM calls need
explicit consent per event class; stored clips expire.

## 4. The briefing (J-08) — sources pipeline

A briefing is a *scheduled job* (Phase 2 queue) that assembles sections, then speaks:

| Section | Source | Library/API |
|---|---|---|
| Calendar today | Google Calendar (`google-api-python-client`) or Outlook (**Microsoft Graph**, `O365`/`msgraph-sdk`) or local `.ics` (`icalendar`) | Pick per user's actual stack; local ICS is the zero-setup default |
| Weather | **Open-Meteo** (free, no key) | One HTTP call; cached |
| News | RSS feeds (`feedparser`) + optional HN API | Curated list in config; LLM summarizes to 5 headlines |
| Overnight system events | Kernel event log (SQLite) | Crashes, completed jobs, failed backups — the honest "while you were away" |
| Memory highlights | Memory engine recall (doc 04) | "You said you'd follow up on X today" |
| Email | IMAP (`imap-tools`) / Graph | Read-only triage at first — write actions need consent |

## 5. Reaching every endpoint (J-18)

- **Phone:** existing dashboard relay stays; add **ntfy** (`ntfy` push, self-hostable,
  free) for fire-and-forget phone notifications ("started the backup job").
- **Telegram bot** (`python-telegram-bot`): the reliable chat back-channel — commands
  and results from anywhere; pairs with the MCP server (doc 05) so the phone becomes
  another kernel client.
- **Watch/TV/laptop:** same MCP-client pattern later; no new protocol inventing.

## 6. Proactive engine (J-19) — the honest rebuild

Triggers (kernel event bus + scheduler):
`time/cron` · `calendar-reminder` · `mqtt-event` (door, motion, device fault) ·
`system-event` (CPU, disk, backup done) · `memory-trigger` ("you mentioned asking the
boss on Monday") · `job-completion`.

Policy: every proactive speech act declares its trigger + consent class
(always / ask-once / never-when-busy). The mislabeled silence timer and its math bug
(`proactive.py:54-55`) die in Phase 0; this engine is its Phase 4 replacement.

## 7. Windows-native automation (supporting layer)

- **pywinauto/uiautomation** (doc 03) — app control.
- **AutoHotkey v2** interop for stubborn legacy apps (ULTRON shells out to `.ahk`
  scripts as tools — pragmatic, sandboxed by script allowlist).
- **PowerShell + psutil + WMI** — system state, services, scheduled tasks (replaces the
  schtasks hack with the Task Scheduler COM API via `pywin32`).
- **pynput** for keyboard/mouse only where UIA fails.

## 8. Integration notes for ULTRON (wiring order)

1. **Phase 2 (with orchestrator):** ntfy + Telegram + briefing-v1 (calendar-ICS +
   weather + RSS + system events) — all as scheduled jobs, spoken on request.
2. **Phase 3 hook:** memory highlights join the briefing automatically.
3. **Phase 4:** HA via MCP-Assist (the mansion), paho-mqtt bridge on the kernel event
   bus, Frigate events → proactive alerts; media tools (ytmusicapi first).
4. **Consent classes land first** — the house can lock doors and buy nothing without
   the policy engine (roadmap §3.4) being live.

*Cross-refs: `03_computer_use_vision.md` (VLM for cameras, UIA) · `04_memory_systems.md`
(memory triggers) · `05_agent_frameworks_mcp.md` (MCP client/server) ·
`06_local_models.md` (local VLM tier for camera captions) · `07_open_source_jarvis_projects.md`
(HA Assist as pipeline reference).*
