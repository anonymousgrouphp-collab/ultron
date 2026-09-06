# 06 — Local Models & Runtimes Research

*Research doc 6 of 8, companion to `01_jarvis_feature_catalog.md`. Serves the model
gateway bet (Roadmap §3.2: provider-agnostic, no model strings outside the gateway),
**J-16** (local VLM for cameras), **J-23** (sandboxed local compute), **J-13**
(multilingual local STT/TTS — deep-dive in `02_voice_stack.md`), and privacy-first
perception. Written 2026-09-07; sources verified Sep 2026.*

**ULTRON context:** today everything rides Gemini Live. The gateway needs a *local
adapter* for: offline fallback, privacy-sensitive perception (cameras, screen, memory
embeddings), cheap bulk work (summarize, classify, embed), and the eval harness
(reproducible runs). This doc picks the runtimes and the model menu per hardware tier.

---

## 1. Runtimes (the "how it runs" layer)

| Runtime | What it is (Sep 2026) | License | Windows story | Use in ULTRON |
|---|---|---|---|---|
| **Ollama** | Model server w/ model registry, OpenAI-compatible API, structured outputs (JSON-schema `format`), tool calling | MIT | First-class installer; runs as a service | **Primary local adapter.** One HTTP client covers chat/tools/vision/embeddings. Auto-pull models at setup |
| **LM Studio** | GUI + local server (OpenAI-compatible), model manager | Free, closed-source | Excellent | Great for the user to browse/test models; optional, not a dependency |
| **llama.cpp** | The engine under everything; GGUF, GBNF grammars, server binary | MIT | Prebuilt Windows binaries; slower to integrate directly | Compile target knowledge; use via Ollama instead |
| **Unsloth / text-generation-webui / GPT4All** | Fine-tuning stack / general GUI / desktop chat | Mixed | Vary | Not needed for the kernel; GPT4All declining in 2026 rankings |
| **vLLM** | High-throughput serving, continuous batching | Apache-2.0 | Via WSL2 only | Only if we ever run a shared/multi-client box; not desktop default |
| **sherpa-onnx** | ONNX speech/models runtime (k2-fsa) | Apache-2.0 | Solid, CPU-friendly | Powers some local STT options (doc 02) |
| **ONNX Runtime + DirectML** | Microsoft's ONNX inference on any GPU vendor | MIT | Native | For small models (VAD, embeddings, YOLO) without CUDA dependency |

**Pick:** Ollama as the only local *server* dependency (auto-installed by
`ULTRON_SETUP.py`), ONNX Runtime for tiny perception models (VAD, embeddings, YOLO),
llama.cpp only as knowledge. LM Studio recommended to the user, never required.

---

## 2. Model menu (September 2026)

### 2.1 General chat + tool calling (the local brain)

| Model | Sizes (GGUF quants) | License | Why it matters |
|---|---|---|---|
| **Qwen3 family** (Alibaba) | 0.6B → 235B MoE incl. 4B/8B/14B/30B | Apache-2.0 | 2026 community consensus "best overall local family." Qwen3-4B runs on 8 GB RAM; Qwen3-7B/8B posts HumanEval 76.0 — best under 8B |
| **gpt-oss-20b / gpt-oss-120b** (OpenAI, Aug 2025) | 20B MoE (~14 GB q4, ~3.6B active) / 120B (~5.1B active, 80 GB GPU) | Apache-2.0 | OpenAI's open-weight reasoning models; agentic-tooling oriented; 20b is the best "reasoning on a gaming PC" pick; near o4-mini parity at 120b |
| **Llama family** (Meta) | 3.2 3B → 4-class | Llama license | Broadest tool compatibility; the safe default everyone supports |
| **Qwen3-Coder-30B** | 30B MoE | Apache-2.0 | Best local coding model on a single 24 GB GPU (Ollama-friendly) |
| **DeepSeek-R1 distills** | 7B/14B/32B | MIT | Cheap reasoning; verbose thinking traces — trim for latency |
| **Gemma 3** (Google) | 1B–27B + vision | Gemma license | Good multimodal small models; ties into Google ecosystem |
| **Minimax M2.5** | 100+ GB VRAM | — | Leader-tier but multi-GPU only — not a desktop target; listed for completeness |

### 2.2 Vision (VLM) — cross-ref `03_computer_use_vision.md`

- **Qwen3-VL 8B Instruct (Q4, via Ollama)** — the doc-03 pick: one local model for
  screen description, UI grounding assist, camera scene description (J-06/J-16).
- Gemma 3 vision / MiniCPM-V — lighter alternatives for CPU-only boxes.
- Local VLM is for *description & gating*; high-stakes grounding still goes to the
  cloud ceiling (Gemini/Claude computer-use) through the gateway when online.

### 2.3 Speech — cross-ref `02_voice_stack.md`

- STT: faster-whisper (int8) / Parakeet-tdt-0.6b-v3 (non-streaming, CC-BY-4.0).
- TTS: Kokoro-ONNX (CPU <250 ms acks) + Chatterbox for the signature voice.
- Wake/VAD: openWakeWord + Silero VAD — both ONNX, CPU-trivial.

### 2.4 Embeddings & rerankers — cross-ref `04_memory_systems.md`

- Embeddings: BGE-M3 or nomic-embed-text via Ollama/ONNX; all-MiniLM as the
  fast/cheap option.
- Reranker: bge-reranker-v2-m3 (CPU-viable for top-50 → top-8 reranking).

---

## 3. Hardware tiers (the setup matrix)

`ULTRON_SETUP.py` should probe (GPU via WMI/`nvidia-smi`, RAM via psutil) and select a
tier. The gateway exposes the tier in its health blob; the dashboard shows it.

| Tier | Hardware | Local brain | Local VLM | Embeddings | Voice |
|---|---|---|---|---|---|
| **N** (no dGPU) | CPU, 8–16 GB RAM | Qwen3 4B Q4 (CPU) | skip (cloud only) | all-MiniLM (ONNX) | faster-whisper tiny/int8 + Kokoro CPU |
| **S** (entry) | 6–8 GB VRAM, 16 GB RAM | Qwen3 8B / Llama 3.2 3B Q4 | MiniCPM-V / Moondream | nomic-embed | faster-whisper small + Kokoro |
| **M** (gaming) | 12–16 GB VRAM | Qwen3 14B / gpt-oss-20b Q4 | Qwen3-VL 8B Q4 | BGE-M3 | faster-whisper medium + Chatterbox |
| **L** (enthusiast) | 24 GB VRAM | Qwen3-Coder-30B / Qwen3 30B MoE | Qwen3-VL 8B + YOLO concurrent | BGE-M3 | everything local incl. Chatterbox clone |

Rules: perception models (wake/VAD/STT) stay resident; the *brain* loads on demand and
unloads (`keep_alive` tuned) to avoid VRAM contention with the voice loop. Cloud is
always the fallback when a local model is missing — never a hard dependency either way.

---

## 4. Structured output & tool calling locally (gateway requirements)

The kernel's tool protocol needs schema-tight outputs from local models:

- **Ollama structured outputs**: pass a JSON schema as `format` — constrained
  generation, not "hope for JSON." Good first implementation for the local adapter.
- **llama.cpp GBNF grammars**: the finest control (regex/grammar-level); used when we
  need strict enum/regex fields; Ollama exposes the same idea via `format`.
- **xgrammar / outlines** (libraries): grammar-constrained decoding for HuggingFace/serve
  stacks — relevant only if we move beyond Ollama.
- **Tool calling**: Qwen3, Llama 3.x, gpt-oss all emit standard tool-call JSON via
  Ollama's `/api/chat` tools parameter — the gateway's tool-call shape must be
  provider-neutral so Gemini Live and Ollama produce the same `ToolCall` structs.

**Gateway acceptance test (Phase 1):** the same 10 scripted kernel tasks pass on
(a) Gemini, (b) Ollama+Qwen3-8B, with zero model-specific code outside the gateway.

---

## 5. What stays cloud-only (honesty section)

- **Real-time duplex voice** (Gemini Live-class semantic VAD + barge-in) has no local
  equivalent that feels as good; local voice = wake+VAD+STT+LLM+TTS pipeline
  (doc 02's Option A) — great offline fallback, higher latency, less natural turn-taking.
- **Top-tier GUI grounding** (ScreenSpot-Pro SOTA ~93% is cloud-only; best open ~47%) —
  local VLM describes, cloud grounds, until open models close the gap.
- Long-context synthesis (whole-repo / huge PDF reasoning) — cloud for years to come.

The gateway's routing policy (per-request): `privacy_class × capability_needed ×
latency_budget → local | cloud`. Memory embeddings and camera footage default local;
personality-critical conversation defaults to the best available model.

---

## 6. Integration notes for ULTRON

1. **Setup:** `ULTRON_SETUP.py` gains an optional Ollama install + tier detection +
   `ollama pull` of the tier's models (Qwen3 4B minimum viable brain).
2. **Gateway (Phase 1):** `LocalAdapter` (Ollama HTTP) alongside `GeminiAdapter`; the
   Live audio loop becomes a modality adapter; model strings live in one config enum.
3. **Perception (Phase 4):** Qwen3-VL + YOLO behind the kernel as consented tools
   (J-06/J-16); embeddings for memory (Phase 3) always local — they're free and private.
4. **Evals:** run the 50-task suite on both adapters; the local tier's score is a
   tracked metric (it must *not* silently regress when we upgrade models — pin by
   digest, not tag).
5. **Watch monthly:** Qwen3 VL/Coder updates, gpt-oss refreshes, GGUF quant releases —
   the local menu rotates faster than any other layer; the gateway isolates us from it.

*Cross-refs: `02_voice_stack.md` (local voice pipeline) · `03_computer_use_vision.md`
(VLM grounding, YOLO, sandbox) · `04_memory_systems.md` (embeddings/rerankers) ·
`05_agent_frameworks_mcp.md` (gateway vs frameworks) · `08_integration_automation.md`
(HA/local hub integration).*
