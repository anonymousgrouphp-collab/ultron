# 03 — Computer Use & Vision Research

*Research doc **3 of 8** · companion to [`01_jarvis_feature_catalog.md`](01_jarvis_feature_catalog.md) · written **2026-09-07** · every repo verified live (GitHub pages + commit feeds) in **Sep 2026**. Stars are approximate; "pushed" = last commit at verification time. Covers **J-06** ("What am I looking at, sir?" — screen understanding), **J-07** (operates the whole computer — plan→act→verify→recover), **J-16** (cameras → VLM scene description + detection), **J-17** (security/threat awareness, vision parts), **J-23** (sandboxed experiments).*

**The one-paragraph verdict:** ULTRON's vision problem is not a model problem, it is an architecture problem — two dead/competing stacks (`actions/screen_processor.py`, `actions/computer_control.py`) and a NameError in the live capture path (`main.py:346` → `_capture_screen`). The 2026 ecosystem splits every vision task into a *layer*: **screen capture** (mss/DXcam — ULTRON already uses mss), **UIA-tree reading** (pywinauto/uiautomation — the reliable, pixel-free path on Windows), **pixel grounding** (OmniParser v2 / UI-TARS / Qwen3-VL — the fallback for UIA-blind apps), **scene/VLM description** (Qwen3-VL 8B locally via Ollama; Gemini 3 through the existing gateway), **OCR** (PaddleOCR 3), **detection** (YOLO26), **sandboxing** (Job Objects → Windows Sandbox → WSL2 → e2b). The roadmap's rule — **one vision stack** — is correct: these layers compose into a single `core/vision.py` interface; the failure mode to avoid is a second parallel pipeline. UIA-first + screenshot/VLM-fallback is exactly what Microsoft's own UFO² does, and it is why their agent beats pure-vision agents on Windows.

**What changed 2024 → 2026 (orientation):** OmniParser went v1→v2 (39.5% ScreenSpot-Pro, Feb 2025) and got absorbed into MS products; ByteDance shipped UI-TARS-1.5 (open weights) and UI-TARS-2 (tech report Sep 2025, 47.5 OSWorld, closed weights); Microsoft UFO grew v1→UFO² (Desktop AgentOS, hybrid UIA+API) →**UFO³ "Galaxy"** (Nov 2025, multi-device); Agent-S reached 12.2k★ with accessibility-tree-driven S2/S3; Qwen released Qwen2.5-VL (the first *mainstream* open VLM with native coordinate grounding) then renamed the repo to **Qwen3-VL** (2B→235B, Instruct/Thinking, official GGUFs); local-VLM community consensus (June 2026 tests) crowned **Qwen3-VL 8B** the best single local VLM; Ultralytics shipped **YOLO26** (NMS-free end-to-end, 43% faster CPU) alongside YOLO11; SAM 3 exists (custom license, unlike SAM 2's Apache-2.0); OpenAI Operator was folded into ChatGPT agent mode (Jul 2025) and its CUA survives as a gated `computer-use-preview`; Anthropic's computer-use tool went production-grade (2026); **Gemini 3.x computer-use added an explicit desktop environment** (Oct 2026-era docs, `gemini-3.7-flash` recommended); ScreenSpot-Pro SOTA exploded from 18.9% (2025 debut) to **92.7% (GPT-6 Astra, Sep 2026)**. The 2023-era "computer use" repos (self-operating-computer, Open Interpreter's old Python CLI, OS-Copilot) are all either pivoted, stale, or reborn in different form.

---

## 0. J-ID coverage map

| J-ID | Need | Sections |
|---|---|---|
| J-06 | "What am I looking at, sir?" — screen/document understanding | §1, §4, §5, §9 |
| J-07 | Operates the whole computer: plan→act→verify→recover, consent + dry-run | §1, §2, §3, §8, §10 |
| J-16 | Camera feeds → VLM scene description, person/package detection | §5, §6 |
| J-17 | Security mode: threat awareness, face recognition, incident vision events | §6 (deep-dive: `08_integration_automation.md`) |
| J-23 | Sandboxed experiments: run code, measure, iterate, report | §7 |

---

## 1. Screen understanding & GUI grounding models (J-06, J-07)

Two families: **parser/grounding models** (screenshot → element boxes + labels, model-agnostic) and **end-to-end computer-use models** (screenshot → click coordinates via API). For ULTRON: grounding can run locally; end-to-end agents are API-only.

### 1a. Grounding / screen-parsing (runnable locally)

| Model | Repo / weights | Stars | License | Maintenance (Sep 2026) | Windows | VRAM | J-IDs |
|---|---|---|---|---|---|---|---|
| [OmniParser v2](https://github.com/microsoft/OmniParser) | microsoft/OmniParser · weights: [HF OmniParser-v2.0](https://huggingface.co/microsoft/OmniParser-v2.0) | ~25.4k | CC-BY-4.0 (weights: no-competing-model clause) | Pushed 2026-07-20; v2 released Feb 2025 | Yes (PyTorch + inference only; needs `torch` GPU or slow CPU) | ~4–6 GB (YOLO-icon-detector ~1 GB + Florence-2-captioner ~2 GB); CPU possible, ~2–5 s/screen | J-06, J-07 |
| [UI-TARS](https://github.com/bytedance/UI-TARS) | bytedance/UI-TARS · open weights: [UI-TARS-1.5-7B](https://huggingface.co/ByteDance-Seed/UI-TARS-1.5-7B) | ~11.4k | Apache-2.0 | Repo pushed 2025-09-05 (UI-TARS-2 report); **UI-TARS-2 is closed** (Doubao API) | Yes for 1.5-7B inference (vLLM/HF) | 7B: ~16 GB fp16 / ~6 GB Q4 | J-06, J-07 |
| [UI-TARS-desktop](https://github.com/bytedance/UI-TARS-desktop) | Electron app + agent stack (Agent TARS) | ~38.9k | Apache-2.0 | Active (pushed 2026-07-01) | Yes | n/a (client; model via API) | J-07 reference |
| [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) (repo renamed from Qwen2.5-VL) | QwenLM/Qwen3-VL · dense 2B/4B/8B/32B, MoE 30B-A3B / 235B-A22B | ~19.9k | Apache-2.0 | Pushed 2026-01-30; official GGUFs for 8B/30B/32B/235B | Yes (Ollama, vLLM, llama.cpp) | 8B: ~10 GB fp16 / **~5–6 GB Q4**; 2B: ~2–3 GB Q4 | J-06, J-07 (grounding), J-16 |
| [Florence-2](https://huggingface.co/microsoft/Florence-2-base) | weights on HF (microsoft/Florence-2-base/-large) | n/a | MIT | Stable since 2024; already inside OmniParser v2 as captioner | Yes | 0.2B/0.7B: <2 GB, CPU-fine | J-06 (OCR/caption sub-tasks) |
| Claude computer use ([docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/computer-use-tool)) | API only | — | proprietary | Production-grade in 2026 (browser + Skills + Files expansion); current gen incl. Claude Opus 5 | Any (client executes) | cloud | J-06, J-07 (cloud brain) |
| Gemini computer use ([docs](https://ai.google.dev/gemini-api/docs/computer-use)) | API only — `gemini-3.7-flash` (recommended), `gemini-3.5-flash-lite`, legacy `gemini-2.5-computer-use-preview-10-2025` | — | proprietary | Active; **ENVIRONMENT_DESKTOP supported** on 3.x; 1000×1000 normalized coords; Interactions API GA since Jun 2026 | Yes (client executes on Windows) | cloud | J-06, J-07 (cloud brain — already ULTRON's gateway vendor) |
| OpenAI computer use ([docs](https://developers.openai.com/api/docs/guides/tools-computer-use)) | API only — `computer-use-preview` via Responses API + Agents SDK | — | proprietary | Stale-ish: Operator folded into ChatGPT agent mode (Jul 2025); model gated to Tier 3+; community asking "retiring with no replacement?" | Any (client executes) | cloud | J-07 (watch, don't build on it) |

**Key facts for ULTRON:**
- **OmniParser v2** = two models: a YOLOv10-class **icon detector** + a fine-tuned **Florence-2 captioner**; claimed **39.5% ScreenSpot-Pro** and ~24% lower latency vs v1 (vendor-reported). It is *not* an agent — it turns any VLM/LLM into one by converting screenshots into structured, labeled element lists. License note: weights are CC-BY-4.0 with a clause against training competing parsers; fine for an app like ULTRON. Sources: [repo](https://github.com/microsoft/OmniParser), [MSR article](https://www.microsoft.com/en-us/research/articles/omniparser-v2-turning-any-llm-into-a-computer-use-agent/), [arXiv:2408.00203](https://arxiv.org/abs/2408.00203).
- **Qwen2.5-VL introduced native "locate by coordinates" grounding** (clickable-point prediction); Qwen3-VL continues it and explicitly advertises "visual agent"/GUI use. This makes **one local model do double duty**: screen description (J-06) *and* click-target grounding fallback (J-07). Community June-2026 head-to-head crowned Qwen3-VL 8B best local all-rounder ([r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1u5oydc/which_is_the_best_local_vlm_benchmark_results/)).
- **Grounding accuracy on Windows apps:** ScreenSpot-Pro is *the* hard test (1,581 instructions over professional hi-res software — CAD, IDEs; [arXiv:2504.07981](https://arxiv.org/html/2504.07981v1), [leaderboard](https://gui-agent.github.io/grounding-leaderboard/)). Progression: best 2025 model ~18.9% → specialized 7B models ~47% → frontier 2026 ~79.5% → **92.7% GPT-6 Astra (Sep 2026)**. Practical reading: *pure pixel grounding still fails regularly on dense professional UIs* — which is exactly why §3's UIA tree exists. Windows-specific caveat: all public benchmarks under-represent Win32/WPF apps (most OSWorld/WAA tasks are browser + Office + system dialogs).
- **End-to-end cloud agents** (Claude, Gemini desktop env, CUA) each implement the same loop — model returns click/type coords, *your client* executes them. They are drop-in "brains" for J-07 via ULTRON's gateway, but the client-side executor, consent gate, and verification loop remain ULTRON's job.

**Library detail (grounding models):**

- **OmniParser v2 pipeline** — two-stage: (1) YOLO-class detector finds *interactive* elements (icons, buttons, input boxes) → bounding boxes; (2) Florence-2 fine-tune captions each crop → labeled element list with `idx`, `bbox`, `caption`; an optional icon-descriptor and OCR pass enrich it. Output JSON plugs into any LLM prompt: "element 14: 'File' menu". Latency budget ~0.5–1.5 s on a consumer GPU after warmup; the repo's latency work in v2 focused exactly on this (smaller detector input size, faster Florence). Caveats: struggles with very small overlapping elements on 4K screens (pre-crop the active window first — another reason the kernel should capture *windows*, not just the desktop); model card asks users not to train competing parsers on the weights.
- **UI-TARS-1.5-7B** — single model does grounding + action prediction (no external parser needed); runs in vLLM on a 16 GB card. Its prompt format expects raw screenshot + task, returns `click(x, y)` / `type(...)`-style actions. UI-TARS-2 (47.5 OSWorld) is *not* downloadable — treat ByteDance's open line as frozen at 1.5 unless that changes.
- **Qwen3-VL grounding** — no parser needed: the model natively returns coordinates for "the Send button" (absolute or normalized, config-dependent; Qwen2.5-VL used absolute-pixel JSON, Qwen3-VL continues the capability). Practical accuracy sits below OmniParser+LLM on dense screens but above "the VLM guesses" — and it's *zero extra models*: the same Ollama instance that answers J-06 can ground J-07 fallback clicks.
- **Gemini 3.x computer-use detail (verified from [docs](https://ai.google.dev/gemini-api/docs/computer-use)):** enable via `tools=[{"type": "computer_use", "environment": "desktop"}]`; model returns `function_call` (`click`, `type`, `scroll`, `press_key`, `hotkey`, `drag_and_drop`, `wait`) on a **1000×1000 normalized grid** your client denormalizes to real pixels; calls carry an `intent` field; opt-in `enable_prompt_injection_detection`; built-in safety policies can emit `require_confirmation` — map that onto ULTRON's consent gate 1:1. Requires `google-genai` SDK ≥2.7. This is the cleanest fit with the existing gateway (same vendor as the voice loop).

---

## 2. Open-source Windows GUI agent frameworks (J-07)

What to adopt vs what to read as reference. None of these is a drop-in for ULTRON (they own the whole loop and the LLM config); they are **reference architectures** for plan→act→observe.

| Framework | Repo | Stars | License | Maintenance (Sep 2026) | Windows UIA | Loop style | J-IDs |
|---|---|---|---|---|---|---|---|
| [Microsoft UFO / UFO² / **UFO³**](https://github.com/microsoft/UFO) | microsoft/UFO ("Weaving the Digital Agent Galaxy") | ~9.7k | MIT | **Active** (pushed 2026-09-02); UFO³ released Nov 2025; UFO² LTS since Apr 2025 | **Yes — core feature**: HostAgent + AppAgent over **UIA, Win32, WinCOM** | Plan→act with **hybrid GUI-click + API actions**, speculative multi-action batching (−51% LLM calls), hybrid visual+UIA control detection | J-07 (the blueprint) |
| [Agent-S / Agent-S2/S3](https://github.com/simular-ai/Agent-S) | simular-ai/Agent-S | ~12.2k | Apache-2.0 | **Active** (pushed 2026-09-05) | Partial (primary targets: Ubuntu/OSWorld, macOS, Windows experimental) | Hierarchical planner + **accessibility tree + set-of-marks** grounding + memory of past trajectories | J-07 reference |
| [OpenAdapt](https://github.com/OpenAdaptAI/OpenAdapt) | OpenAdaptAI/OpenAdapt | ~1.7k | MIT | Active (pushed 2026-09-03) | Yes (Windows-first historically; screen recording + UIA) | **Demonstration → compilation**: records your GUI task, compiles to a program that self-verifies ("VERIFIED only if independent check passes") | J-07 (procedural-memory idea), J-24 |
| [Self-Operating Computer](https://github.com/OthersideAI/self-operating-computer) | OthersideAI | ~10.3k | MIT | Stale (pushed 2025-09-19) | Yes (screenshot + pyautogui) | Minimal screenshot→coords loop; the 2023 pioneer; superseded by its own successors | J-07 (historical) |
| [OS-Copilot](https://github.com/OS-Copilot/OS-Copilot) | OS-Copilot/OS-Copilot | ~1.8k | MIT | **Dead** (pushed 2024-09-09) | Weak (Linux-flavored) | Self-improving OS agent (FRIDAY) | — do not use |
| [Open Interpreter](https://github.com/openinterpreter/openinterpreter) | openinterpreter/openinterpreter | ~68.3k | Apache-2.0 | **Reborn 2026** (pushed 2026-09-06) as a Codex-derived coding agent for open models (Rust core, new license); the 2023 "control your PC" Python CLI is gone | n/a (coding agent now) | Code-execution agent loop | J-11/J-23 adjacent, not J-07 |
| [Browser-use](https://github.com/browser-use/browser-use) | browser-use/browser-use | ~113k | MIT | Very active (pushed 2026-09-05) | Yes (Playwright) | DOM/a11y-tree-driven web agent — the **web slice** of J-07 solved by someone else | J-07 (web tasks only) |

**Key facts for ULTRON:**
- **UFO²/UFO³ is the closest thing to a validation of ULTRON's planned architecture**: it reads the **Windows UIA tree** as primary observation, uses screenshots only when UIA is blind, and *mixes* GUI clicks with API calls (PowerShell/WinCOM) chosen per-action for reliability. Its multi-action batching insight (one LLM call → several verified actions) is directly portable to ULTRON's kernel. Sources: [repo README](https://github.com/microsoft/UFO), [UFO³ paper series](https://github.com/microsoft/UFO) (Galaxy = multi-device orchestration over an Agent Interaction Protocol — J-21/J-22 later).
- **Agent-S** proves the a11y-tree + set-of-marks hybrid generalizes across OSes and posts top OSWorld results; its "episodic memory of past trajectories" is the same procedural-memory idea in roadmap Phase 5.
- **OpenAdapt's verification model** ("compiled task reports VERIFIED only if an independent check passes") is the exact shape of ULTRON's Phase 4 verify step and J-24's failure→skill loop.
- Everything else is either a UI, a benchmark harness, or dead. **Build ULTRON's agent loop in the kernel; borrow UFO's observation layer and Agent-S's memory pattern.** Don't re-platform.

---

## 3. Windows UI automation backends — the reliable alternative to screenshot-clicking (J-07)

The single highest-leverage table in this document. A UIA-tree agent sees *named controls with types, states, and stable properties* ("Button 'Save', enabled, invoke-able") where a pixel agent sees a blurry rectangle. Pixel grounding is the *fallback*, not the foundation.

| Library | Repo | Stars | License | Maintenance (Sep 2026) | pip install | Windows | J-IDs |
|---|---|---|---|---|---|---|---|
| [pywinauto](https://github.com/pywinauto/pywinauto) | 6.2k | BSD-3-Clause | Active (pushed 2026-05-23) | `pip install pywinauto` | Yes — `backend="uia"` (UIA) or `"win32"` (Win32); waits, wrappers for common controls, `print_control_identifiers()` tree dump | J-07 |
| [uiautomation](https://github.com/yinkaisheng/Python-UIAutomation-for-Windows) | 3.6k | Apache-2.0 | Active (pushed 2026-06-02) | `pip install uiautomation` | Yes — pure ctypes UIA wrapper; the most direct tree-walker (ControlFromCursor, GetChildren, patterns); zero heavy deps | J-07 |
| [pywin32](https://github.com/mhammond/pywin32) | 5.6k | PSF-style | Very active (pushed 2026-08-24) | `pip install pywin32` | Yes — `win32gui.EnumWindows` for window enumeration, `win32process`, Job Objects (§7), COM access | J-07, J-23 |
| [pynput](https://github.com/moses-palmer/pynput) | 2.2k | LGPL-3.0 | Active (pushed 2026-05-12) | `pip install pynput` | Yes — global input injection + listeners; cross-platform; LGPL is fine for an app, flag for distribution | J-07 |
| [keyboard](https://github.com/boppreh/keyboard) | 4k | MIT | **Stale** (pushed 2023-01-31) | `pip install keyboard` | Windows-first global hotkeys/hooks; still works; prefer pynput or RegisterHotKey via pywin32 for new code | J-07 |
| [PyAutoGUI](https://github.com/asweigart/pyautogui) | 12.7k | BSD-3-Clause | **Stale** (pushed 2023-06-07) | `pip install pyautogui` | Yes — blind coordinate click/type; FAILSAFE corner; **keep for the pixel-fallback layer only** (ULTRON's `actions/computer_control.py` is 100% this today) | J-07 |
| [PyGetWindow](https://github.com/asweigart/PyGetWindow) | 421 | BSD-3-Clause | **Stale** (pushed 2021-09-01) | `pip install PyGetWindow` | Yes — window move/resize/minimize; superseded by pywin32 `win32gui` | J-07 |

**UIA-tree agents vs pixel-grounding agents — why a hybrid wins:**
1. **Determinism:** UIA returns control type + Name + AutomationId + BoundingRectangle + ToggleState/Value patterns. That is a *database*, not a guess. Pixel agents must re-derive semantics from pixels every frame — the root cause of the fragile-script problem in today's `computer_control.py`.
2. **Coverage asymmetry:** UIA can't see into DirectX games, some Electron/CEF canvases, remote-desktop streams, and badly-instrumented legacy apps — precisely where screenshots + OmniParser/Qwen3-VL grounding work best. Conversely, VLMs misread dense Win32/WPF property grids that UIA reads perfectly. The blind spots are *disjoint* — a hybrid has no blind spot.
3. **Cost/latency:** a UIA tree dump is milliseconds and zero tokens; a screenshot + VLM round-trip is seconds and real money. Hybrid agents (UFO², Agent-S) use vision only when the tree is insufficient — ULTRON should do the same: **UIA-first, vision-fallback**.
4. **Verification without extra models:** after clicking via pixels, the UIA tree confirms the effect ("did the 'Save' button's state change / did a new window appear?"). This is the cheap, reliable `verify` half of plan→act→verify→recover.
5. **Accessibility = legality of scale:** UIA is the same API Narrator uses; it is designed for external control and requires no admin (except elevated windows — see §10 note).

Window enumeration baseline (replaces hand-rolled scripts): `win32gui.EnumWindows` → visible top-level windows → `win32process.GetWindowThreadProcessId` → `psutil` name match. Two hours of work, kills 20 hardcoded Steam-AppID actions.

**Library detail (UIA backends):**

- **pywinauto** — batteries included: `Application().connect(title="Notepad")` / `.start()`, `print_control_identifiers()` for a full tree dump, wait primitives (`wait("visible enabled", timeout=5)`), and typed wrappers (ButtonWrapper, EditWrapper) for the common controls. Best when a *specific known app* is driven repeatedly. Python 3.13 support confirmed by its 2026 commit activity; pure-Python + comtypes, no compiler needed on Windows.
- **uiautomation (yinkaisheng)** — the tree scalpel: `ControlFromCursor()`, `GetFocusedControl()`, `.GetChildren()`, pattern properties (`IsInvokePatternAvailable`, `GetTogglePattern().ToggleState`). Zero deps beyond ctypes — the right choice when ULTRON's kernel needs a *generic observation function*: `dump_uia_tree(window) -> list[Element(type, name, automation_id, rect, state)]` in ~30 lines. Its GitHub README doubles as the best UIA-in-Python tutorial on the internet.
- **UIA control patterns worth supporting on day one** (map them to kernel tool verbs):

| UIA pattern | Meaning | Kernel verb |
|---|---|---|
| Invoke | button/menu-item click | `invoke(element)` |
| Toggle | checkbox/switch state flip | `toggle(element)` |
| Value (ValuePattern.SetValue) | text field set without keystrokes | `set_value(element, text)` |
| RangeValue | sliders/volume | `set_range(element, val)` |
| SelectionItem | list/radio/tab select | `select(element)` |
| ExpandCollapse | combobox/tree expand | `expand(element)` / `collapse(element)` |
| Scroll | scroll pane without wheel events | `scroll(element, amount)` |
| LegacyIAccessible / Text | read text content | observation only |

- **Windows input injection:** `pynput.mouse`/`.keyboard` for synthetic events (works from services, no admin for same-integrity targets); `SendInput` via pywin32 ctypes if event-level control (key-up/key-down, unicode) is needed. Global hotkeys: `RegisterHotKey` via pywin32 beats the stale `keyboard` lib for new code.
- **Known blind spots to test in the ULTRON-20 suite:** Chromium/Electron expose a UIA tree (good — most of the user's apps will be UIA-visible), but canvas-rendered areas (games, some VPN clients, RDP streams) do not; elevated Task Manager blocks a non-admin kernel. These are exactly the screenshot-fallback cases.

---

## 4. OCR & document understanding (J-06)

| Engine | Repo | Stars | License | Maintenance (Sep 2026) | pip install | Windows | Speed / VRAM | J-IDs |
|---|---|---|---|---|---|---|---|---|
| [PaddleOCR 3.x](https://github.com/PaddlePaddle/PaddleOCR) | 89k | Apache-2.0 | Active (pushed 2026-07-22); PP-OCRv5 server+mobile ([arXiv:2507.05595](https://arxiv.org/html/2507.05595v1)); RTX 50-series needs special Windows build ([PaddleX install](https://paddlepaddle.github.io/PaddleX/3.4/en/installation/paddlepaddle_install.html)) | `pip install paddleocr` (paddlepaddle ≥3.0; CUDA-matched wheel for GPU) | Yes | Mobile: CPU-realtime. Server: GPU ~2–4 GB. Heavier install footprint (Paddle framework) | J-06 |
| [Surya](https://github.com/datalab-to/surya) | datalab-to/surya | ~21.4k | Apache-2.0 (GPL-3.0 for some new extras — check model cards) | Very active (pushed 2026-09-05); OCR + layout + reading order + tables, 90+ langs | `pip install surya-ocr` | Yes | GPU-recommended (~2–4 GB); CPU slow | J-06 |
| [docling](https://github.com/docling-project/docling) (IBM) | docling-project/docling | ~66.1k | MIT | Very active (pushed 2026-09-04); PDF/Office → structured markdown/JSON | `pip install docling` | Yes | CPU-OK (slower); optional GPU accel | J-06 |
| [marker](https://github.com/datalab-to/marker) | datalab-to/marker | ~39.6k | Apache-2.0 core (weights GPL-3.0 — check) | Active (pushed 2026-08-31); PDF→markdown, best-in-class layout | `pip install marker-pdf` | Yes | GPU ~4–6 GB for speed; CPU works, slow | J-06 |
| [EasyOCR](https://github.com/JaidedAI/EasyOCR) | 30k | Apache-2.0 | Slowing (pushed 2025-12-05) | `pip install easyocr` | Yes | ~1–2 GB; slow vs PaddleOCR v5; 80+ langs | J-06 (fallback) |
| [Tesseract 5](https://github.com/tesseract-ocr/tesseract) | 76.4k | Apache-2.0 | Active (pushed 2026-09-02); Windows via [UB-Mannheim installer](https://github.com/UB-Mannheim/tesseract/wiki) | `pip install pytesseract` + external binary | Yes | CPU-only, fast on clean text; poor on scene text/layout | J-06 (fallback) |

**Recommendation:** **PaddleOCR 3 (PP-OCRv5-mobile) as the default screen/OCR layer** (fast on CPU, no VRAM tax next to the VLM, best accuracy-per-Watt), **docling for document files** (PDF/Office → markdown for the kernel's memory/context), Tesseract only as the zero-GPU emergency fallback. Surya/marker are best-in-class for *PDF-heavy* pipelines; ULTRON's dominant case is screenshots, so the lighter, realtime-mobile PP-OCRv5 model wins. Avoid stacking two full OCR engines (the "one vision stack" rule applies to OCR too).

---

## 5. Local VLMs for scene/screen description (J-06, J-16)

All run through **Ollama** ([ollama/ollama](https://github.com/ollama/ollama), ~180k★, MIT, active 2026-09-05) — which ULTRON should treat as the local model server of record (doc `06_local_models.md` owns that decision; this doc owns *which* VLM).

| Model | Repo / weights | Stars | License | Sizes that fit consumer GPUs | VRAM (Q4) | Ollama | J-IDs |
|---|---|---|---|---|---|---|---|
| [Qwen3-VL](https://github.com/QwenLM/Qwen3-VL) | QwenLM/Qwen3-VL + [HF collection](https://huggingface.co/collections/Qwen/qwen3-vl) (official GGUFs: 8B/30B-A3B/32B/235B-A22B) | ~19.9k | Apache-2.0 | **8B** (12 GB-class card), 2B/4B (any 6 GB card), 30B-A3B (24 GB), 32B (24 GB) | 8B: ~5–6 GB · 4B: ~3–4 GB · 30B-A3B: ~18 GB | **Yes (`qwen3-vl`)** | J-06, J-07 grounding, J-16 |
| [InternVL 3.5](https://github.com/OpenGVLab/InternVL) | OpenGVLab/InternVL · [InternVL3_5-8B](https://huggingface.co/OpenGVLab/InternVL3_5-8B) | ~10.1k | MIT | 1B/2B/4B/8B/14B/38B | 8B: ~6–8 GB | via GGUF (community) | J-06, J-16 |
| [MiniCPM-V 4.5](https://github.com/OpenBMB/MiniCPM-V) | OpenBMB/MiniCPM-V | ~26.3k | Apache-2.0 (weights: research-free + commercial after registration — check card) | 8B-class, phone-to-desktop | ~5–6 GB | Yes (`minicpm-v`) | J-16 (edge), J-06 |
| [Moondream 3](https://github.com/m87-labs/moondream) | m87-labs/moondream · [moondream3-preview HF](https://huggingface.co/moondream/moondream3-preview) | ~10k | Apache-2.0 | 9B MoE / **2B active**; Moondream 3.1 current | ~4–6 GB | Yes (`moondream`) | J-16 (cheap always-on), J-06 |
| [Gemma 3](https://github.com/google-deepmind/gemma) (vision) / Gemma 4 E4B (2026) | google-deepmind + HF | n/a | Gemma license (terms apply) | 4B/12B/27B multimodal (Gemma 3); Gemma 4 E4B current-gen | 4B: ~4 GB · 12B: ~9 GB | Yes (`gemma3`) | J-06, J-16 |
| [Florence-2](https://huggingface.co/microsoft/Florence-2-base) | HF weights | n/a | MIT | 0.2B/0.7B task-model (caption/OCR/detect), not a chat VLM | <2 GB, CPU | no (HF/transformers) | J-06 sub-tasks |

**Recommendation:** **Qwen3-VL 8B Instruct (Q4, via Ollama) as ULTRON's single local VLM** — best 2026 community scores as a generalist, native coordinate grounding for the J-07 fallback path, one model serves screen description, camera scene description, and grounding. MiniCPM-V 4.5 / Moondream as low-VRAM *always-on camera watchers* (J-16 continuous loop) if the GPU is busy; Gemini 3 (existing gateway) as the cloud quality ceiling. Community data point: June 2026 local-VLM bake-off crowned Qwen3-VL 8B; InternVL3.5 "terse", Gemma 4 E4B "hedges" ([r/LocalLLaMA](https://www.reddit.com/r/LocalLLaMA/comments/1u5oydc/which_is_the_best_local_vlm_benchmark_results/)). Do not run two local VLM stacks "for choice" — one model, one server (Ollama), one adapter in the gateway.

**Sizing table (Q4_K_M quantization, rough guidance):**

| Card | Model | Serves |
|---|---|---|
| Any GPU ≥4 GB / CPU | Qwen3-VL 2B/4B · Moondream 3 · Florence-2 | J-16 always-on camera captions, quick J-06 |
| 8–12 GB (RTX 3060/4060-class) | **Qwen3-VL 8B** (+ PaddleOCR + YOLO26n, ~2 GB combined) | J-06 describe, J-07 grounding, J-16 scenes |
| 16 GB (4080-class) | Qwen3-VL 8B + OmniParser v2 loaded simultaneously | Full hybrid J-07 act-loop with zero model juggling |
| 24 GB (3090/4090/5090) | Qwen3-VL 30B-A3B (MoE, 3B active — fast) | Highest-quality local describe/ground; or 32B dense when accuracy matters |

**Selection notes:**
- **Qwen3-VL Instruct vs Thinking variants:** Instruct for realtime describe/ground; Thinking trades seconds for accuracy — wrong default for an interactive JARVIS; expose it only as a "think harder" option for hard screen puzzles.
- **InternVL 3.5** (ViR dynamic-resolution routing, 4× speedup claims) is the strongest *alternate*; keep on the watchlist in case Qwen licensing or Ollama support sours.
- **MiniCPM-V 4.5's** video understanding (multi-frame) makes it the natural J-16 "watch the door-cam clip and tell me what happened" model; check the license card before any commercial move.
- **Ollama context:** screenshots are token-expensive (~1–2k tokens/frame at useful resolution); size `num_ctx` ≥8k for screen tasks, and downscale captures to the model's native-res sweet spot (Qwen-VL class models handle ~1 MP well) — full-4K frames mostly buy cost, not accuracy.

---

## 6. Camera feeds, detection & face recognition (J-16, J-17 vision slice)

Capture note: ULTRON already has the right capture primitive — `cv2.VideoCapture(index, CAP_DSHOW)` in `actions/screen_processor.py`. Keep it; it's the OpenCV-standard DirectShow path on Windows.

| Tool | Repo | Stars | License | Maintenance (Sep 2026) | pip install | Windows | VRAM | J-IDs |
|---|---|---|---|---|---|---|---|---|
| [Ultralytics YOLO26/11](https://github.com/ultralytics/ultralytics) | 61.3k | **AGPL-3.0** (contact Ultralytics for commercial license if ULTRON ever ships) | Very active (pushed 2026-09-06); YOLO26 = end-to-end NMS-free, 43% faster CPU ([docs](https://docs.ultralytics.com/models/yolo26), [arXiv:2606.03748](https://arxiv.org/html/2606.03748v1)) | `pip install ultralytics` | Yes (native ONNX/OpenVINO export) | n→x: 1–8 GB; nano/s run CPU-realtime | J-16, J-17 |
| [Grounding DINO](https://github.com/IDEA-Research/GroundingDINO) | 10.5k | Apache-2.0 | Stable/frozen (pushed 2024-08-12); open-vocabulary "find the package on the porch" text-prompted detection | build via pip (needs torch, C++ build pain on Windows — use prebuilt or mmdet impl) | Yes | ~4 GB | J-16 (open-vocab queries) |
| [SAM 2](https://github.com/facebookresearch/sam2) | 19.8k | Apache-2.0 | Stable (pushed 2024-12-16); video segmentation with memory | `pip install sam-2` (or from git) | Yes | 2–6 GB | J-16 (masks, later) |
| [SAM 3](https://github.com/facebookresearch/sam3) | 11.6k | **Custom Meta license (not OSI)** | Active (pushed 2026-08-26) | from git | Yes | 4–8 GB | J-16 (watchlist; license review first) |
| [OpenCV](https://github.com/opencv/opencv) | 90.7k | Apache-2.0 | Very active (pushed 2026-09-06) | `pip install opencv-python` | Yes (CAP_DSHOW) | 0 | J-16 |
| [DeepFace](https://github.com/serengil/deepface) | 23.4k | MIT | Very active (pushed 2026-09-01); wraps ArcFace/Facenet/VGG-Face, age/gender/emotion | `pip install deepface` | Yes | ~1 GB (TensorFlow) | J-17 (door-cam ID) |
| [InsightFace](https://github.com/deepinsight/insightface) | 29.6k | MIT code; **pretrained models non-commercial research only** | Active (pushed 2026-07-27) | `pip install insightface onnxruntime` | Yes | ~1 GB | J-17 (higher accuracy; license check) |
| [Frigate NVR](https://github.com/blakeblackshear/frigate) | 35.7k | MIT | Very active (pushed 2026-09-06) | Docker/HASS add-on (not pip) | Yes (via Docker) | Coral/GPU optional | J-16/J-17 — **cross-reference only; deep-dive in `08_integration_automation.md`** |

**Division of labor for ULTRON:** J-16 starts as **webcam/RTSP → OpenCV frames → (a) YOLO11n/YOLO26n for person/package/car events, (b) Qwen3-VL for "what am I looking at" scene descriptions on demand or on event**, with an event-driven clip log. Face recognition for J-17: DeepFace (MIT) for the personal-project license posture; InsightFace only if accuracy demands it and the non-commercial model license is respected. Frigate is the *end-state* NVR (motion-gated detection, MQTT events into the kernel) — do not hand-roll a second NVR; see doc 08.

**J-16/J-17 event pipeline (the part this doc owns):**

```
camera(s) ──OpenCV CAP_DSHOW/FFmpeg──▶ frame bus (kernel, 5–15 fps idle)
                                          │
                    ┌─────────────────────┴─────────────────────┐
                    ▼                                           ▼
        cheap gate (always-on)                       expensive describe (on event)
        YOLO26n/11n @ CPU ~10–20 fps                 Qwen3-VL/MiniCPM-V on:
        classes: person, package, car,                 • detection event ("person at
        pet, unknown_motion                              the door, holding a box")
                    │                                  • user query ("garage cam, sir?")
                    ▼                                  • scheduled sweep (nightly)
        event → kernel event bus                       → text + thumbnail → memory
        {type, cam, ts, bbox, clip_path,                  (J-04 recall fodder)
         confidence}                                    → J-17 incident log
```

- **Two-tier inference is the whole trick:** the YOLO gate costs ~0 CPU on GPU-idle or ~1 core on CPU (nano model + frame skipping + `cv2` motion pre-filter), while the VLM only wakes on events or requests — the same "cheap watcher / expensive thinker" split the kernel uses for wake word vs voice loop.
- **Person vs known-face (J-17):** on `person` event at a monitored camera, crop → DeepFace `verify()/find()` against the enrolled household gallery → event becomes `{person: "Tony" | "unknown"}`; unknown faces outside configured hours = J-17 incident ("sir, there's someone at the gate").
- **Package detection** is just YOLO classes + dwell logic (object present ≥N seconds at the door zone) — no extra model; Grounding DINO reserved for open-vocabulary one-offs ("is my helmet in the garage cam view?").
- **Clip retention:** write pre/post-event JPEG frames (ring buffer, ±5 s) to disk with the event record; VLM descriptions and clip paths go to memory (doc 04) so "what happened while I was out" (J-08 briefing) is a recall query, not a re-scan.

---

## 7. Sandboxed code execution (J-23)

Practicality ladder on a Windows 11 desktop, strongest-first:

| Option | Repo / link | Stars | License | Maintenance (Sep 2026) | Windows story | Isolation strength | J-IDs |
|---|---|---|---|---|---|---|---|
| [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/) (OS feature) | built-in Win11 **Pro/Enterprise** (not Home) | — | n/a | Maintained by Microsoft, ships with OS | Enable-WindowsOptionalFeature; ~3 GB RAM; **ephemeral** (state dies with it), single instance | Strong (Hyper-V VM, kernel-isolated) | J-23 (hostile code) |
| [Docker on Windows / WSL2](https://learn.microsoft.com/en-us/windows/wsl/) | docker deskop / WSL2 | — | n/a | Active | Good for **Linux** packages via WSL2; resource limits via cgroups; Docker Desktop licensing note for big orgs (free for personal) | Medium-strong (container; VM boundary via WSL2 utility VM) | J-23 (main workhorse) |
| Job Objects + restricted tokens (pywin32) | [mhammond/pywin32](https://github.com/mhammond/pywin32) `win32job`, `CreateProcessWithLogonW`/Safer APIs | 5.6k | PSF-style | Very active (2026-08-24) | Native, zero-install, millisecond spawn; limit memory/CPU/UI, kill-on-close, temp-dir chroot-by-convention | **Weak-medium** (same OS, kernel-enforced limits, not a security boundary vs malware) | J-23 (trusted-lite experiments) |
| [microsandbox](https://github.com/superradcompany/microsandbox) | 8.1k | Apache-2.0 | Active (2026-09-04); libkrun microVMs, MCP server built in, Python/JS SDK; claims Linux/macOS/**Windows (WHP)** — Windows path is the newest, verify on your box | `msb` binary + `pip install microsandbox` | Yes (newest backend) | Strong (hardware microVM, per-sandbox kernel) | J-23 (watchlist → adopt when proven) |
| [e2b](https://github.com/e2b-dev/E2B) | 13.7k | Apache-2.0 (SDK); **cloud service** (Firecracker microVMs) | Very active (2026-09-04) | `pip install e2b` | Cloud: works anywhere, but **code + files leave the machine** | Strong (cloud) | J-23 (if privacy-acceptable) |
| [judge0](https://github.com/judge0/judge0) | 4.4k | GPL-3.0 | Active (2026-08-17) | Self-host via Docker/WSL2; overkill for a single-desktop JARVIS (built for online-judge scale) | Medium (container) | J-23 (skip for now) |

**Recommendation (ladder, not choice):** default J-23 runs = **pywin32 Job Object subprocess** (memory+CPU caps, `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, 30 s timeout, temp workspace) for the 90% case of "run my experiment and measure"; anything touching network or executing model-generated code that wasn't consented = **Windows Sandbox** (Pro) or a **WSL2 Docker container**; **e2b only** as opt-in cloud escape hatch. microsandbox is the one to watch — MCP-native microVMs with a Python SDK match ULTRON's kernel design exactly; prototype it in a Phase 5+ spike before betting on it. This pairs with J-11's coding subagent (doc `05_agent_frameworks_mcp.md`): agent proposes → sandbox executes → results feed back → report.

**Concrete primitives:**

- **Job Object (default rung)** — `win32job.CreateJobObject()`, `SetInformationJobObject` with `JOB_OBJECT_LIMIT_PROCESS_MEMORY` (e.g. 2 GB) + `JOB_OBJECT_LIMIT_CPU_RATE` + `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`, `AssignProcessToJobObject(proc.handle, job)` around the `subprocess.Popen` of generated code. Spawn in ~ms, auto-kills the whole tree on timeout/crash, no admin, no VM. Pair with: fresh temp dir per run, `env` scrubbed of ULTRON's API keys, `--network=off` style policy via Windows Firewall rule or no-network token — that last part is convention, not security; don't run hostile code here.
- **Windows Sandbox rung** — declarative `.wsb` file maps the workspace in read-write, toggles networking and vGPU:

```xml
<Configuration>
  <MappedFolders><MappedFolder>
    <HostFolder>C:\Users\tony\ultron_sandbox</HostFolder>
    <ReadOnly>false</ReadOnly>
  </MappedFolder></MappedFolders>
  <Networking>Disable</Networking>
  <VGpu>Disable</VGpu>
  <LogonCommand><Command>cmd /c cd sandbox &amp; python run_experiment.py</Command></LogonCommand>
</Configuration>
```

  `WindowsSandbox.exe config.wsb` boots a disposable Win11 in ~15–30 s; everything inside evaporates on close, so J-23's "measure and iterate" loop must persist results by writing them back to the mapped folder (or into the kernel's memory).
- **WSL2/Docker rung** — for the numpy/pytest/linux-only dependency surface; ULTRON's experiment workspace becomes a bind-mounted directory, limits via `--cpus`/`--memory`; WSL2 already ships on the target machine if Docker Desktop or WSL is installed.
- **What J-23 is *not*:** not a second Jupyter server, not a hosted notebook — it's a kernel tool `run_experiment(spec) -> results` where `spec` is structured (files + command + success metric), so the coding subagent (J-11) and the eval harness (J-24) can drive it headlessly.

---

## 8. Benchmarks & evals for computer use (J-07 measurement)

The roadmap's Phase 4 gate: **20 computer-use scenarios >70%, zero destructive actions without consent.** Public benchmarks calibrate ULTRON against the world; the private 20-task suite measures ULTRON against itself.

| Benchmark | Repo | Stars | License | Maintenance (Sep 2026) | Platform | Shape | Use for ULTRON |
|---|---|---|---|---|---|---|---|
| [OSWorld](https://github.com/xlang-ai/OSWorld) | xlang-ai/OSWorld | ~3.1k | Apache-2.0 | Active (2026-08-30) | Ubuntu (Docker VM) | ~369 real OS tasks (files, office, browser, system); used by UI-TARS-2 (47.5) and Agent-S for headline numbers | Calibrate the agent loop + VLM/grounding choices; runs in Docker on Windows |
| [Windows Agent Arena](https://github.com/microsoft/WindowsAgentArena) | microsoft/WindowsAgentArena | ~895 | MIT | Frozen (2024-11-20) but **the** Windows-native benchmark; [site/leaderboard](https://microsoft.github.io/WindowsAgentArena/); ICML 2025 ([arXiv:2409.08264](https://arxiv.org/abs/2409.08264)) | **Windows 11 (Azure-scale VMs)** | 150+ tasks across Windows apps; baseline agent 19.5% vs human 74.5% | **Best-aligned with J-07**; cherry-pick a runnable subset locally (full parallel eval needs Azure) |
| [WebArena](https://github.com/web-arena-x/webarena) | web-arena-x/webarena | ~1.6k | Apache-2.0 | Semi-active (2025-11-26) | Self-hosted websites (Docker) | Realistic web tasks; WebVoyager ([arXiv:2401.13919](https://arxiv.org/abs/2401.13919)) is the live-web companion eval | Only if ULTRON's web agent (browser-use) needs measurement; mostly out of scope here |
| (private) ULTRON-20 | *(to build)* | — | — | — | Windows 11 desktop | 20 scripted scenarios from the user's real apps (Per roadmap gate) | **The metric that matters.** Store in eval harness (Phase 5), run weekly |

**Practical eval plan:** (1) build ULTRON-20 with per-task verifier scripts (file exists, window title, UIA property, screenshot diff) — this is the Phase 4 gate instrument; (2) run the OSWorld Docker subset quarterly to keep the stack honest vs public progress; (3) mine WAA's task definitions (MIT) for Windows-scenario templates; (4) track ScreenSpot-Pro leaderboard to decide when a better open grounding model beats OmniParser v2 — *swap the parser, never add a second one*.

**ULTRON-20 sketch — 20 scenarios drawn from the user's real machine** (each = task + verifier + destructive-guard expectation; the mix deliberately exercises UIA-first and vision-fallback paths):

| # | Scenario class | Example task | Verifier (independent check) |
|---|---|---|---|
| 1–4 | Files & Explorer UIA | create/rename/move a file via Explorer GUI | filesystem state |
| 5–8 | Browser (UIA/CDP + DOM) | open site, fill form, submit | DOM/page state via Playwright |
| 9–11 | Settings app | change a Windows setting and back | registry / `Get-ItemProperty` |
| 12–13 | Office/Notepad editing | type into a document, save-as | file content hash |
| 14–15 | Vision-fallback forced | task inside a UIA-blind canvas app | screenshot diff + window title |
| 16–17 | Multi-step with recovery | install flow with a modal dialog appearing mid-way | final state + recovery log |
| 18 | Cross-app | data from app A into app B | clipboard/end-state |
| 19 | Destructive-guard | "delete folder X" must trigger consent, not action | consent log shows prompt; folder intact pre-consent |
| 20 | Dry-run | same as any above with `dry_run=True` | action log shows resolved targets, zero input events |

---

## 9. Screen capture — fixing the broken path (J-06 Phase 0)

ULTRON already depends on the right library. The bug is plumbing, not tooling.

| Library | Repo | Stars | License | Maintenance (Sep 2026) | pip install | Notes |
|---|---|---|---|---|---|---|
| [mss](https://github.com/BoboTiG/python-mss) | 1.3k | MIT | Active (2026-08-31) | `pip install mss` | **ULTRON's current capture path** (`actions/screen_processor.py:79`); multi-monitor aware; ~30–60 fps; PNG encode built in |
| [DXcam](https://github.com/ra1nty/DXcam) | 801 | MIT | Maintained-ish (2026-03-18) | `pip install dxcam` | Desktop Duplication API — 120–240 fps, GPU-resident, ideal for a *continuous* screen-watch loop later (J-19 proactive triggers); overkill for on-demand J-06 |
| (dead-ish) [BetterCam](https://github.com/RootKit-Org/BetterCam) | 138 | MIT | Stale (2023) | — | Fork of DXcam; skip |
| [PyAutoGUI.screenshot](https://github.com/asweigart/pyautogui) | 12.7k | BSD-3 | Stale | — | Pillow/SciKit-based; slow; never build on it |

**Phase 0 fix:** the `NameError` on `_capture_screen` (`main.py:346`) is a scope/import failure around `mss` — fix by making the kernel own one `capture_screen()` (mss, multi-monitor, returns PNG bytes + active-window metadata from pywin32) and deleting the parallel capture code in `screen_processor.py`. One capture function, one place; consumers: J-06 describe, J-07 act-loop, J-19 triggers.

**Capture gotchas worth encoding once, in the kernel's capture function:**
- **DPI awareness:** without `ctypes.windll.shcore.SetProcessDpiAwareness(2)` (per-monitor v2) the process gets virtualized coordinates — screenshots are scaled and pixel coordinates from UIA rects or VLM grounding land in the wrong place. Set it once at kernel startup; it is the #1 cause of "clicked next to the button" bugs.
- **Multi-monitor:** `mss` enumerates monitors 1..N (monitor 0 = the virtual bounding box). The observation bundle should record *which* monitor a window is on (via `win32gui.GetWindowRect` vs monitor rects) so coordinates are absolute, not per-monitor-relative.
- **Active-window metadata with every capture:** the observation bundle is `png_bytes + {window_title, process_name, window_rect, monitor_id, uia_available: bool}` — the `uia_available` flag is what routes the J-07 loop between the UIA path and the vision path. Cheap to compute (try `uiautomation.GetFocusedControl()`), saves a wasted VLM call most of the time.
- **Timing:** mss full-screen grab is ~10–30 ms; PNG encode dominates (~30–80 ms). For the act-loop use JPEG quality ~85 (the codebase already does this for camera frames in `screen_processor.py`) unless OCR text sharpness matters — then PNG.

---

## RECOMMENDATION

### The hybrid architecture for ULTRON (one vision stack, layered)

```
                        ┌────────────────────────────────────────────────┐
                        │                KERNEL (Phase 1)                │
                        │   consent gate · dry-run · audit log · budget  │
                        └───────┬───────────────┬───────────────┬────────┘
                                │               │               │
                    OBSERVE (see)         ACT (do)          VERIFY (check)
                    ┌──────────────┐   ┌──────────────┐   ┌──────────────┐
     UIA-first ────▶│ pywinauto /  │──▶│ uiautomation │──▶│ UIA state     │
     (ms, free)     │ uiautomation │   │ patterns:    │   │ change? window│
                    │ tree dump    │   │ Invoke/Toggle│   │ title? value? │
                    └──────┬───────┘   │ SetValue/    │   └──────┬───────┘
                    blind? │           │ SelectItem   │     fail │
                           ▼           └──────┬───────┘          ▼
                    ┌──────────────┐   pixel-only:  ┌──────────────────┐
                    │ SCREENSHOT   │   pyautogui +  │ Screenshot diff  │
                    │ mss (fixed   │   keyboard via │ + VLM re-check   │
                    │ Phase 0)     │   pynput       │ → recover (Esc,  │
                    └──────┬───────┘                │ retry, replan)   │
                           ▼                        └──────────────────┘
                    ┌──────────────────────────────┐
                    │ GROUND & DESCRIBE (fallback) │
                    │ • OmniParser v2 → elements   │
                    │ • Qwen3-VL 8B (Ollama, Q4) → │
                    │   coords + description + OCR │
                    │ • PaddleOCR PP-OCRv5 → text  │
                    │ • cloud: Gemini 3 computer-  │
                    │   use (gateway) = quality    │
                    │   ceiling for plan steps     │
                    └──────────────────────────────┘
     J-16/J-17 lane: OpenCV(CAP_DSHOW) → YOLO26n/11n events → Qwen3-VL
     scene description → event log → (later) Frigate per doc 08
     J-23 lane: Job-Object subprocess → Windows Sandbox / WSL2 Docker
     → e2b (opt-in cloud); watch microsandbox
```

### Top picks + fallbacks (all verified Sep 2026)

| Layer | Top pick | Fallback | Why |
|---|---|---|---|
| Screen capture (J-06/J-07) | **mss** (already in repo; fix the NameError) | DXcam (future continuous watch) | MIT, active, multi-monitor, already the codebase's de-facto standard |
| UI reading / primary actuation (J-07) | **pywinauto + uiautomation** (UIA backend) | pywin32 EnumWindows + pynput for input | Named controls + patterns = deterministic; UFO²-proven on Windows |
| Pixel grounding fallback (J-07) | **OmniParser v2** (local) | Qwen3-VL native grounding (no extra model) | Best-documented open parser; only one parser allowed (one-stack rule) |
| Screen/scene description (J-06/J-16) | **Qwen3-VL 8B Instruct Q4 via Ollama** | Gemini 3 via existing gateway (cloud ceiling); MiniCPM-V 4.5 for low-VRAM camera loop | 2026 community-best local VLM; one model, three jobs (describe, ground, watch) |
| OCR (J-06) | **PaddleOCR 3 / PP-OCRv5-mobile** | docling for PDFs/Office files; Tesseract emergency | CPU-realtime on screenshots; no VRAM tax next to the VLM |
| Windows GUI agent blueprint (J-07) | **Steal UFO²'s observation layer + Agent-S's memory pattern into the kernel** | — | Don't re-platform; MIT/Apache licenses permit |
| Cloud agent brain (J-07, optional) | **Gemini computer-use (desktop env)** via gateway | Claude computer-use tool | Already ULTRON's vendor; explicit desktop env support in 3.x |
| Web slice of J-07 | **browser-use** | Playwright direct | 113k★, MIT, DOM/a11y-driven — don't click pixels on the web |
| Detection (J-16/J-17) | **YOLO26n / YOLO11n** (ultralytics) | Grounding DINO (open-vocab "package?") | NMS-free, CPU-realtime; AGPL flag for any future commercial ship |
| Face recognition (J-17) | **DeepFace** (MIT) | InsightFace (non-commercial model license) | License-clean, active, one-call enrollment |
| Sandbox (J-23) | **Job Object subprocess (default) → Windows Sandbox / WSL2 Docker (hard cases)** | e2b (opt-in cloud); microsandbox (watch) | Zero new infra for 90% of experiments; strong isolation available |
| Evals (J-07 gate) | **Private ULTRON-20 suite + verifiers** | OSWorld Docker subset quarterly; WAA task templates | Phase 4 gate instrument; public benches keep the stack honest |

### Integration notes for ULTRON

1. **Kill the second stack, keep one interface.** Merge `actions/screen_processor.py` (capture/camera) and `actions/computer_control.py` (PyAutoGUI input) into kernel tools behind one `VisionGateway` + `InputGateway` — the roadmap's "one vision stack" kill-list item. The NameError at `main.py:346` disappears because capture lives in exactly one place.
2. **UIA-first policy, encoded:** every J-07 action first tries a UIA solution (find control by Name/AutomationId → Invoke/Select/SetValue pattern). Pixels only when the tree has no interactive ancestor. This policy is ~50 lines and converts ULTRON from "fragile script firehose" to "evidence-driven agent" — UFO²'s exact trick (hybrid UIA + GUI + API actions).
3. **Consent + dry-run (Phase 4 doctrine):** the InputGateway takes `dry_run: bool`; in dry-run it resolves targets (UIA node or grounded coords), logs what *would* be clicked/typed, and executes nothing. Destructive classes (delete, send, pay, uninstall) always require explicit consent, per the roadmap gate ("zero destructive actions without consent").
4. **Verify then recover:** after each action, re-read UIA state / active window / screenshot; on mismatch → one retry, then replan with the new observation; hard cap N attempts → hand back to the user with a screenshot. OpenAdapt's "VERIFIED only if independent check passes" is the acceptance bar.
5. **Model placement:** Qwen3-VL 8B lives in Ollama (GPU idle→serves); OmniParser loads on demand (unload after idle; it's ~4–6 GB and only needed during act-loops); never co-load both on a 12 GB card without a policy. Gateway meters VLM calls like any other provider.
6. **Windows-specific gotchas:** UIA often can't read *elevated* (admin) windows from a non-admin process — run the kernel unelevated and surface "this window is blocked, approve elevation" as a consent event; DPI scaling must be set per-monitor aware or coordinates shift (set `SetProcessDpiAwareness` in the kernel startup); PyAutoGUI FAILSAFE corner stays enabled as a physical kill switch.
7. **Watchlist:** Qwen3-VL GGUF updates + Ollama support maturing; UI-TARS-2 open-weights possibility; SAM 3 license relaxation; microsandbox-on-Windows stability; WAA leaderboard movement; OmniParser v3; ScreenSpot-Pro leader changes (currently GPT-6 Astra 92.7% — cloud-only, but the open models trail by ~30 pts and close every quarter).

### Build order (maps to roadmap phases)

1. **Phase 0 (now):** fix `_capture_screen` NameError; kernel-owned `capture_screen()` with DPI-awareness + active-window metadata; delete the parallel capture code. *(hours, not weeks)*
2. **Phase 1 (kernel):** UIA observation tool (`dump_uia_tree`) + window enumeration tool land as the first kernel Tools; `pywinauto`+`uiautomation` deps added; `computer_control.py`'s PyAutoGUI calls become the `input_*` Tools with `dry_run` plumbed but default-on consent.
3. **Phase 4 (perception & autonomy):** full hybrid J-07 loop (plan→act→verify→recover) with consent gate + dry-run preview; OmniParser v2 + Qwen3-VL 8B via Ollama as the fallback/observe layer; PaddleOCR for text; ULTRON-20 eval suite built and measured to the >70% gate.
4. **Phase 4b (J-16/J-17):** OpenCV camera lane + YOLO events + VLM describe-on-event; DeepFace enrollment in the dashboard; incident log to kernel events. Frigate adoption decided alongside Home Assistant work (doc 08) — not before.
5. **Phase 5+ (J-23 + self-improvement):** Job-Object sandbox tool → Windows Sandbox/WSL2 rungs; microsandbox spike; failed J-07 tasks feed the procedural-memory loop (J-24) with the ULTRON-20 regression harness.

*Verification method: GitHub repo pages + `commits.atom` feeds scraped 2026-09-07 for stars/licenses/last-push (API rate limits forced HTML scraping; stars rounded); vendor docs fetched for Gemini computer-use, Claude computer-use, OpenAI computer-use, YOLO26, PaddleOCR install, Windows Sandbox; community benchmarks cited where models weren't formally benched. Companion docs: `01_jarvis_feature_catalog.md` (features), `../ROADMAP.md` (phases, kill list), `06_local_models.md` (model server), `08_integration_automation.md` (Frigate/J-17 deep-dive).*
