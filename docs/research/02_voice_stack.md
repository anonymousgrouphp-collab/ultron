# 02 — Voice Stack Research

*Research doc **2 of 8** · companion to [`01_jarvis_feature_catalog.md`](01_jarvis_feature_catalog.md) · written **2026-09-07** · every repo verified live (GitHub API, PyPI, Hugging Face) in **Sep 2026**. Stars are approximate; "pushed" = last commit activity at verification time. Covers J-01 (wake + VAD + full-duplex turn-taking), J-02 (speaker ID), J-03 (voice/persona delivery), J-13 (multilingual speech).*

**The one-paragraph verdict:** ULTRON's voice UX is its strongest subsystem (Gemini Live, 50 ms barge-in), but it is hard-locked to one vendor. The 2026 market has matured enough to build a **provider-agnostic voice gateway** where (a) the Gemini Live loop becomes one adapter among several realtime speech-to-speech adapters, and (b) a local cascade (openWakeWord → Silero VAD → faster-whisper/Parakeet → local LLM → Kokoro+Chatterbox) becomes a second, free/offline adapter. Both plug into the same kernel interface. Picovoice — the likely "commercial wake word" pick — just killed its free tier (AccessKeys stop working **June 30, 2026**), which settles the wake-word choice for a personal project: **openWakeWord, which ships a pretrained "hey jarvis" model**.

**What changed 2024 → 2026 (orientation):** Coqui died (2024) but XTTS-v2 lives on in a community fork; Piper was archived (2025) and reborn as `piper1-gpl`; NeMo was renamed to `NVIDIA-NeMo/Speech`; Picovoice killed its free tier (2026-06-30); Vocode died (2024-11); Moonshine went multilingual + streaming; Microsoft VibeVoice (53.8k★) and Qwen3-TTS reset expectations for open TTS; NVIDIA Parakeet/Canary v2/v3 models took the accuracy crown with CC-BY-4.0 licenses; open-source realtime moved from Moshi (research) to Unmute/pipecat/LiveKit (production). Every repo below was re-verified via the GitHub API on 2026-09-07.

---

## 0. J-ID coverage map

| J-ID | Need | Sections |
|---|---|---|
| J-01 | Always-listening wake word, VAD, full-duplex turn-taking, in-session wake, barge-in | §1, §2, §3, §5, §9, §10 |
| J-02 | Speaker identification → per-user memory & persona | §8 |
| J-03 | Voice delivery: deep, calm, British-dry "JARVIS" | §6, §7 |
| J-13 | Multilingual speech (STT + TTS, mid-sentence switching) | §3, §4, §6, §7 |

---

## 1. Wake word engines (J-01)

The wake word must run 24/7 on the user's gaming PC, never phone home, and accept the phrases **"Hey JARVIS" / "Hey ULTRON"**. It should survive ULTRON's main process restarting (in-session wake replaces today's process-launcher in `wake_service.py`).

| Engine | Stars | License | Maintenance (Sep 2026) | Custom phrase | On-device free? | Windows | Notes |
|---|---|---|---|---|---|---|---|
| [openWakeWord](https://github.com/dscripka/openWakeWord) | ~2.7k | Apache-2.0 | Repo pushed 2025-12; PyPI `0.6.0` (Feb 2024, stable) | Yes — train via [openwakeword.com](https://openwakeword.com/) or [dscripka/openWakeWord-training](https://github.com/dscripka/openWakeWord-training) | Yes, fully local | Yes (pure Python + ONNX/tflite) | **Ships pretrained `hey_jarvis` model** (trained against ~30k h negative audio); also `hey_mycroft`, `alexa`. ~1-2% CPU on one core. |
| [Porcupine](https://github.com/Picovoice/porcupine) (Picovoice) | ~4.9k | Apache-2.0 SDK, proprietary models | Very active (pushed 2026-09-03) | Yes, trained in Picovoice Console (seconds) | **No longer** — [free-tier AccessKeys stop working 2026-06-30](https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744); paid ~$899/mo | Yes (excellent SDKs) | Best raw accuracy (97%+ claimed); engines call home to validate license — dealbreaker for offline. |
| [microWakeWord](https://github.com/OHF-Voice/micro-wake-word) | ~930 | Apache-2.0 | Active (pushed 2026-07; moved to Open Home Foundation) | Yes (synthetic training pipeline) | Yes | tflite-micro targets ESP32-S3; models runnable on PC via tflite-runtime | Designed for Home Assistant voice satellites — relevant later for J-18 hardware, not for the PC. |
| [livekit-wakeword](https://livekit.com/blog/livekit-wakeword) | n/a (tool, 2026) | OSS | New 2026 | One-command custom training | Yes | Via sherpa-onnx runtime | Newer entrant from LiveKit; worth tracking for branded phrases like "Hey ULTRON". |
| (dead) Snowboy, Mycroft Precise v1 | — | — | dead | — | — | — | Do not use. |

**Latency/footprint:** openWakeWord processes 80 ms frames, fires in ~0.1-0.3 s from end of phrase on a laptop CPU; Porcupine ~<100 ms. Both are far below human-perceptible thresholds; false-accept rate is the real differentiator, and Porcupine wins it — but not at $899/mo or with a kill switch.

**Key fact for ULTRON:** `hey_jarvis` is literally a stock model in openWakeWord's model zoo ([README example](https://github.com/dscripka/openWakeWord): `openwakeword.Model(wakeword_models=["hey_jarvis"])`). A "Hey ULTRON" custom model can be trained in an afternoon with the synthetic pipeline, and HA's [Wake Word Collective](https://community.home-assistant.io/t/make-the-default-wake-word-of-all-official-home-assistant-voice-hardware-something-other-than-okay-nabu/796312) actively improves shared models.

**Library detail:**

- **openWakeWord** — `pip install openwakeword`. Runtime: ONNX or tflite per model, 80 ms frames, supports running several models simultaneously (e.g. `hey_jarvis` + `hey_ultron` + `alexa` as a decoy). Uses Google's speech_embedding features (needs `speech_embedding` ONNX model downloaded on first run — cache it offline). Windows: pure Python, no compiler needed. False-accept tuning: train-time "false positive penalty" knob + runtime threshold per model. Custom training generates synthetic TTS positives (thousands of speakers via ElevenLabs-style synthesis) + negative mining; the `hey_jarvis` stock model was trained against ~30,000 h of adversarial background audio, so a DIY `hey_ultron` will not match its recall on day one — mitigate with two-wake-phrase support and VAD-gated re-check. Serves J-01.
- **Porcupine** — `pip install pvporcupine`. Free tier: was 3 MAU / 3 custom keywords/month; [discontinued 2026-06-30](https://community.home-assistant.io/t/fyi-picovoice-confirmed-free-tier-accesskeys-will-stop-working-after-june-30-2026/1012744) — AccessKeys now require a paid plan (~$899/mo enterprise; eval trial available). Best-in-class FPR; license validation requires occasional internet. Serves J-01. Only revisit if ULTRON ever ships commercially with a voice budget.
- **microWakeWord** — `pip install` not the model; models are tflite-micro `.tflite` files trained via [esphome/micro-wake-word-models](https://github.com/esphome/micro-wake-word-models). Purpose-built for ESP32-S3 voice satellites under Home Assistant / the Open Home Foundation. Relevant when ULTRON grows hardware endpoints (J-18), not for the PC. Serves J-01 (later), J-18.

**Front-end pattern (always-on, in-process):** one PortAudio callback at 16 kHz mono → ring buffer → openWakeWord frame scorer (2% CPU) → on detection: verify with VAD + energy gate → emit `wake(speaker=?)` event → open STT session. This replaces `wake_service.py`'s phrase-list polling (which also catches its own TTS — the wake service is not echo-aware). Runs inside the kernel process so wake works in-session (J-01's real gap).

---

## 2. VAD (J-01)

VAD gates STT, feeds turn-taking, and (critically) prevents the assistant from transcribing its own TTS. It runs every 32-100 ms, so it must be sub-millisecond.

| Engine | Stars | License | Maintenance | Latency per chunk | Streaming fit | Windows | Notes |
|---|---|---|---|---|---|---|---|
| [Silero VAD](https://github.com/snakers4/silero-vad) | ~10.1k | MIT | Very active — **v6.2.1** (PyPI 2026-02-24; v6.0 Aug 2025) | 512 samples @16 kHz (~32 ms) in **<1 ms** CPU, faster via ONNX | Native — stateful chunked API designed for streams | Yes | The de-facto standard; used by RealtimeSTT, pipecat, Home Assistant. v6 adds ONNX opset-15 export + "ifless" models ([version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models)). |
| [py-webrtcvad](https://github.com/wiseman/py-webrtcvad) | ~2.5k | MIT | Stale (pushed 2024-07; PyPI 2.0.10 from 2017) | microseconds | Yes (frame-based) | Yes | Energy/GMM-based; chokes on music/TV background — false triggers break an always-on assistant. Legacy choice. |
| Cloud semantic VAD (Gemini Live, OpenAI Realtime, [Kyutai Unmute](https://kyutai.org/blog/2025-05-22-unmute/)) | — | — | — | server-side | server-side | — | Predicts *whether you finished your thought*, not just silence — better turn-taking than any local binary VAD. Only available inside the realtime APIs. |

**Pick:** Silero VAD v6 (`pip install silero-vad`), ONNX backend. **Fallback:** webrtcvad only as a zero-dependency pre-filter. For turn-taking quality beyond binary VAD, the realtime-API adapters in §5 provide server-side semantic VAD.

---

## 3. STT — local (J-01, J-13)

Requirements: word timestamps (barge-in alignment, speaker-ID sync), real-time partials (streaming), 24/7 CPU-friendly idle, GPU burst on command, Windows wheels.

| Engine | Stars | License | Maintenance (Sep 2026) | pip install | Timestamps | Streaming partials | Languages | Notes |
|---|---|---|---|---|---|---|---|---|
| [faster-whisper](https://github.com/SYSTRAN/faster-whisper) | ~25.3k | MIT | `1.2.1` (2025-10-31) — mature | `pip install faster-whisper` | Word-level (Whisper cross-attention + VAD) | No (batch) — pair with [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) for partials | ~99 | CTranslate2, ~4x faster than openai/whisper, int8 on CPU. The default local STT. |
| [whisper.cpp](https://github.com/ggml-org/whisper.cpp) | ~53.5k | MIT | Very active (pushed 2026-09-04) | build/`pip install gguf`-style bindings; ships prebuilt Windows binaries | Word-level | Yes (audio-stream example, partials) | ~99 | Best CPU-first option; quantized GGML; used by countless assistants. |
| [WhisperX](https://github.com/m-bain/whisperX) | ~23.9k | BSD-2 | `3.8.6` (2026-05-25) | `pip install whisperx` | Best-in-class word alignment | No (batch) | ~99 | Adds forced alignment + pyannote diarization hooks — ideal for **transcripts feeding J-02/J-04**. |
| [Parakeet TDT 0.6B v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3) | HF: ~759k downloads | **CC-BY-4.0** (weights) / Apache-2.0 (NeMo) | lastModified 2026-08-05 | via [NeMo](https://github.com/NVIDIA-NeMo/Speech) (~18.4k stars, pushed 2026-09-06) or sherpa-onnx | Word+segment (native TDT) | **No — non-streaming** ([sherpa-onnx #2918](https://github.com/k2-fsa/sherpa-onnx/issues/2918)) | 25 European | ~100-2000x real-time on GPU; top of Open ASR Leaderboard. Superb "re-transcribe the session" engine. |
| [Canary-1B-v2](https://huggingface.co/nvidia/canary-1b-v2) | HF: ~13.5k downloads | CC-BY-4.0 | lastModified 2026-08-31 | via NeMo / sherpa-onnx | Segment (NFA) | No | 25 European + translation | Higher accuracy than Parakeet, slower; paper [arXiv:2509.14128](https://arxiv.org/html/2509.14128v1). |
| [Moonshine](https://github.com/moonshine-ai/moonshine) | ~11k | MIT (code + streaming weights; legacy non-English non-streaming weights under community license) | Active (pushed 2026-08-31) | `pip install moonshine-voice` | Yes | **Yes — built for live streaming**, does work while you're still talking | en (legacy) → multilingual v3 models rolling out | Tiny models down to ~1 MB; claims Whisper-large-class accuracy; WASM/iOS/Android/Win/Linux. Great edge pick. |
| [Vosk](https://github.com/alphacep/vosk-api) | ~15.1k | Apache-2.0 | Repo touched 2026-08, PyPI wheels 2022 (stale) | `pip install vosk` | Word | Yes (true streaming) | 20+ | Small CPU models, dated accuracy vs Whisper/Parakeet class. Legacy fallback only. |
| [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | k2-fsa (Next-gen Kaldi) | Apache-2.0 | Very active — `1.13.7` (2026-09-05) | `pip install sherpa-onnx` | Yes | **Yes — streaming Zipformer models with partials**, Windows binaries | many | Runtime, not a model: runs streaming Zipformer, Paraformer, Whisper, Moonshine, Parakeet (non-streaming) via ONNX Runtime. The cleanest "one runtime for all local ASR" bet on Windows. |
| [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | ~10.1k | MIT | `1.1.2` (2026-08-30) | `pip install RealtimeSTT` | via backend | Yes (VAD-gated faster-whisper loop with partials + wake-word hooks) | via backend | An orchestrator, not a model: silero VAD + faster-whisper + optional wakeword. Near drop-in for ULTRON experiments. |
| [distil-whisper](https://github.com/huggingface/distil-whisper) | ~4.1k | MIT | Slowed (pushed 2025-01) — models stable | via faster-whisper/whisper.cpp | Word | No | en | distil-large-v3 ≈ 6x faster than large-v3. Mostly superseded by Parakeet on GPU and faster-whisper small/int8 on CPU. |

**Practical streaming truth:** nothing open-source gives Whisper-class accuracy *and* true token streaming on Windows today. The workable patterns are (1) Silero VAD segments + faster-whisper with `BatchedInferencePipeline`/int8 — first partial in ~200-400 ms on a mid GPU; (2) streaming Zipformer via sherpa-onnx for instant (<100 ms) partials with lower accuracy; (3) Moonshine streaming for low-power/edge. For the "re-think everything offline" path (briefings, J-08; memory ingestion, J-04), Parakeet v3 is the 2026 accuracy/speed king.

**Pick:** faster-whisper (segments via Silero, int8) for the local cascade; Parakeet-tdt-0.6b-v3 for bulk/offline re-transcription. **Fallbacks:** whisper.cpp (CPU-only machines), sherpa-onnx streaming Zipformer (hard real-time partials), Moonshine (tiny edge).

**Library detail (the ones that matter operationally):**

- **faster-whisper** — `pip install faster-whisper`. Settings that matter for a live loop: `WhisperModel("small", device="cuda", compute_type="int8_float16")` or `distil`-equivalents via CT2 HF hub; `vad_filter=True` (built-in Silero), `word_timestamps=True` (needed to align speaker IDs, J-02, and to compute "user spoke at T" for barge-in decisions). Windows: needs CUDA/cuDNN DLLs for GPU (or CPU int8, fine for `small`/`base`). The [RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) wrapper (`pip install RealtimeSTT`) gives the wake→VAD→transcribe loop with `on_transcription_start`/partial callbacks out of the box — the fastest way to prototype the local adapter before wiring it into the kernel. Serves J-01, J-13.
- **whisper.cpp** — prebuilt Windows binaries + `whisper-cli --stream`; quantized models (Q5_0 `small` runs real-time on 4 CPU cores). Best when the machine has no usable GPU budget for STT. Serves J-01, J-13.
- **WhisperX** — batch tool; use for *session transcripts* (the memory-ingestion path, J-04) where its word alignment + pyannote hooks produce speaker-attributed transcripts. Not for the live mic. Serves J-02, J-04 (via J-02 alignment).
- **Parakeet/Canary (NeMo)** — `pip install nemo_toolkit[asr]` (heavy) or run via sherpa-onnx ONNX exports for the Parakeet 0.6B. 60 min of audio transcribes in <1 min on an RTX card; word timestamps native (TDT). Use as the "listen-back" engine: re-transcribe the day's session audio for memory consolidation (J-04) and briefing prep (J-08). v3 covers 25 European languages (J-13 partial). Canary-1B-v2 additionally translates to/from English. Serves J-13, J-01 (offline), J-08.
- **Moonshine** — `pip install moonshine-voice`; streaming models do STT work while the user is still speaking (lowest achievable time-to-final on small hardware). English strongest; multilingual streaming models still rolling out (verify per-language before relying on it for J-13). Serves J-01.
- **Kyutai STT** ([kyutai.org/stt](https://kyutai.org/stt/)) — open streaming STT via "Delayed Streams Modeling" (2.6B + 350M, en/fr, Apache-2.0); word timestamps and semantic-VAD-ish behavior; the STT half of Unmute. Runs through vLLM/rust on GPU. Worth benchmarking against faster-whisper if ULTRON adopts Option A seriously.
- **sherpa-onnx** — `pip install sherpa-onnx` (wheels for Windows x64). One runtime for streaming Zipformer (real-time partials), non-streaming Whisper/Parakeet/Moonshine exports, plus keyword spotting and speaker ID onnx models. If ULTRON wants a *single* local ASR dependency with Windows wheels, this is it — at slightly lower top-end accuracy than native NeMo. Serves J-01.
- **Vosk** — `pip install vosk`. True streaming, tiny, 20+ langs, but accuracy is a generation behind; keep only for constraints where even `small` Whisper is too heavy. Serves J-01 (fallback).

---

## 4. STT — cloud / API (J-01, J-13)

| Provider | Model | Streaming? | Latency | Price (streaming) | Price (batch) | Notes |
|---|---|---|---|---|---|---|
| [Deepgram](https://deepgram.com/learn/deepgram-vs-assemblyai-vs-whisper) | Nova-3 | Yes (WebSocket) | ~150-300 ms (marketed <300 ms; independent [Gradium benchmark](https://gradium.ai/content/stt-api-benchmark-2026-latency-accuracy) shows low variance) | **$0.0077/min** mono, $0.0092/min multilingual (~$0.46-0.55/hr) | $0.0043/min | Claimed 6.84% median WER on real streams; keyterm boosting; the voice-agent default. |
| [AssemblyAI](https://www.assemblyai.com/blog/introducing-universal-streaming) | Universal-Streaming | Yes | ~300 ms median emission | **$0.15/hr** (cheapest) | Universal-2/3.5 ~$0.15-0.21/hr (cut Aug 2026) | Free tier ~185 h batch / 333 h streaming; strong async features (chapters, PII). |
| OpenAI | gpt-4o-transcribe / -mini | **No streaming STT endpoint** — Realtime API only | n/a | via Realtime API (~$0.02-0.06/min audio) | **$0.006/min** ($0.36/hr), mini $0.003/min ([pricing](https://developers.openai.com/api/docs/pricing)) | Best raw accuracy on clips; wrong tool for a live mic loop. |
| Google | Gemini 2.5/3.x Flash (Live API audio input) | Yes (in-session) | ~0.3-1 s turn latency | bundled in Live pricing ≈ $0.03-0.06/min | — | What ULTRON uses today; transcription of the Live session is free to include (`input_audio_transcription` already on). |
| ElevenLabs | Scribe | batch-first | — | — | $0.22/hr | Accuracy leader on some benchmarks; not a streaming mic loop tool. |

**Pick:** Deepgram Nova-3 streaming for the cloud-hybrid mic loop (latency leader + keyterm boost for "JARVIS/ULTRON"); AssemblyAI if cost dominates. Keep Gemini's built-in input transcription for the Live adapter — zero extra cost.

---

## 5. Duplex / speech-to-speech realtime + orchestration (J-01, J-03)

This is the "full-duplex JARVIS feel": user talks over the assistant, it stops, yields, and rejoins. Two architectures: **S2S foundation models** (one model hears audio and speaks) and **orchestrated cascades** (framework wires STT→LLM→TTS with VAD-driven barge-in).

### 5.1 Realtime S2S APIs

| API | Models (2026) | Barge-in / turn-taking | Session limits | Price (audio) | Notes |
|---|---|---|---|---|---|
| [Gemini Live API](https://ai.google.dev/gemini-api/docs/live-api) | `gemini-2.5-flash-native-audio-*` (ULTRON today: `main.py:68` uses `-preview-12-2025`), Gemini 3.1 Flash Live ([preview](https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-3-1-flash-live/)) | Server VAD + interruption events; configurable `start/end_of_speech_sensitivity`, `prefix_padding_ms`, `silence_duration_ms` ([capabilities](https://ai.google.dev/gemini-api/docs/live-api/capabilities)); hybrid + manual VAD modes | 15 min audio-only (unlimited via session-resumption handle); 128k ctx native-audio | ≈ **$0.03-0.06/min** ([pricing page](https://ai.google.dev/gemini-api/docs/pricing); ~$0.04-0.05/min real-world per Google's [Paige Bailey](https://www.linkedin.com/posts/dynamicwebpaige_its-still-wild-to-compare-the-cost-of-google-activity-7404970713645785088-VgKt)) | 70+ languages, native function calling, proactive audio; already proven inside ULTRON with 50 ms interrupt drain. |
| [OpenAI Realtime API](https://openai.com/index/introducing-gpt-realtime/) | gpt-realtime-2.1, -mini | Server VAD + interruption events; production-grade tool calling | Long-lived, reconnect-safe | **$32/1M audio-in + $64/1M audio-out** (~$0.05-0.08/min), mini $10/$20 (~$0.02/min) ([pricing](https://developers.openai.com/api/docs/pricing)) | Excellent instruction-following voice; pricier than Gemini; strong telco-grade ecosystem. |
| [Amazon Nova Sonic / Nova 2 Sonic](https://aws.amazon.com/blogs/aws/introducing-amazon-nova-sonic-human-like-voice-conversations-for-generative-ai-applications/) | nova-2-sonic (late 2025) | Bidirectional streaming, adaptive turn-taking, interruption handling | 300k ctx; 8-min default connection (extendable) | ≈ **$0.27/hr input** + output tokens ≈ cheapest S2S ([Loka case study](https://aws.amazon.com/blogs/machine-learning/how-loka-built-a-natural-low-latency-voice-agent-with-amazon-nova-2-sonic/)) | TTFA ~1.39 s (slower than Gemini/OpenAI); Bedrock-only; tool use supported; LiveKit/Vonage integrations. |

### 5.2 Open-source duplex / local S2S

| Project | Stars | License | Maintenance | What it gives you | Notes |
|---|---|---|---|---|---|
| [Kyutai Moshi](https://github.com/kyutai-labs/moshi) | ~11k | Apache-2.0 | Active (pushed 2026-05) | True **full-duplex** speech-text S2S (Mimi codec), sub-200 ms, parallel user/model speech | 7B model → wants a real GPU (PyTorch/rust/MLX); experimental for production. |
| [Kyutai Unmute](https://github.com/kyutai-labs/unmute) | ~1.5k | MIT | Active (pushed 2026-07) | Open-source cascade wrapper: streaming STT + **semantic VAD** + streaming TTS around *any* text LLM ([blog](https://kyutai.org/blog/2025-05-22-unmute/)) | Runs self-hosted with vLLM; the best open reference architecture for "make any LLM talk". Kyutai STT + [Pocket TTS](https://kyutai.org/tts/) (CPU voice cloning, Jan 2026) round it out. |
| [pipecat](https://github.com/pipecat-ai/pipecat) (Daily) | ~15.3k | BSD-2 | Very active — **1.8.1** (2026-08-27) | Python frame-based pipeline: 100+ STT/LLM/TTS/VAD/transport services, barge-in built-in | Transport-agnostic (local mic, WebRTC, Daily, LiveKit, telco). The best "write your voice stack once, swap vendors" framework — matches ULTRON's gateway strategy exactly. |
| [LiveKit Agents](https://github.com/livekit/agents) | ~14k | Apache-2.0 | Very active — **1.8.0** (2026-09-05) | Agent framework welded to LiveKit WebRTC SFU; plugins for all major S2S APIs; telephony | Best when you want media infrastructure + agents from one vendor; heavier than ULTRON needs on a single PC. |
| [Vocode](https://github.com/vocodedev/vocode-core) | ~3.8k | MIT | **Dead** (pushed 2024-11-15) | — | Was the go-to OSS voice-agent library in 2023-24; unmaintained. Do not adopt. |
| Daily / LiveKit Cloud | — | commercial | active | Managed transports | Only needed for phone/remote endpoints — ULTRON's phone relay already covers the mobile case. |

**Barge-in reality check:** ULTRON already implements client-side barge-in better than most (drain queue in ~50 ms slices, `main.py:540-545`). pipecat formalizes the same pattern (`interruption` frames → stop TTS, clear queue) if ULTRON adopts it as the adapter runtime; the S2S APIs implement it server-side.

**Adapter notes (what each S2S integration actually requires):**

- **Gemini Live** — WebSocket session, 16 kHz PCM in / 24 kHz out (matches `main.py` today). Turn config: `start_of_speech_sensitivity`, `end_of_speech_sensitivity`, `prefix_padding_ms` (don't set 0 — clips first syllable), `silence_duration_ms` (server default ~800 ms; 500-800 recommended; 100-200 fragments utterances). Interruption = `server_content.interrupted` → stop playback + clear queue (ULTRON's drain does this). Sessions cap at 15 min audio-only → **must** implement session-resumption handles (unlimited extension) — check whether `main.py`'s TaskGroup reconnect covers resumption-with-context, not just reconnection. Supports function calling, input/output transcription (already enabled), proactive audio, 70+ languages (J-13 strongest S2S). Model churn is the tax: `-preview-12-2025` strings hard-coded in 8+ modules are exactly what the roadmap's model-gateway wants to eliminate.
- **OpenAI Realtime** — same shape (WebSocket/WebRTC, server VAD, `response.cancel` on interruption). gpt-realtime-2.1 voices are the best "butler-ish" stock voices of any S2S; per-session tool calling works but tool schemas must be re-registered per session. ~2-3x Gemini's price.
- **Nova 2 Sonic** — Bedrock bidirectional streaming (boto3/websocket); cheapest per hour by far; 8-min default connection window (extendable); TTFA ~1.39 s means noticeably slower back-channel than Gemini/OpenAI — a cost adapter, not the flagship.
- **Kyutai Unmute (self-hosted)** — streaming STT + your vLLM-served LLM + streaming TTS with semantic VAD; open source under MIT; needs one decent GPU; the reference implementation to study for how a *local* full-duplex pipeline should schedule chunks. Moshi itself remains the only true full-duplex open S2S (user and model can speak simultaneously) but is research-grade on Windows.
- **pipecat** — if adopted, ULTRON's kernel tool-calls map onto pipecat's `FunctionCallFrame`s; its service registry (Deepgram, ElevenLabs, OpenAI, Gemini, Silero, local LLMs…) is effectively a battle-tested version of the "provider-agnostic gateway" the roadmap asks for. Cost: an extra framework + its abstractions inside the kernel process. Adopt for the voice layer only if the kernel's own gateway proves too thin — do not let it own scheduling/memory (that's the kernel's job).

**Pick:** keep Gemini Live as the flagship S2S adapter (proven, cheapest good S2S, function calling works), add OpenAI Realtime as the premium-quality adapter behind the same interface; track Nova 2 Sonic as the cost leader. Use pipecat only if/when ULTRON wants a full orchestrated cascade with WebRTC/telco endpoints — it's overkill for a single-PC assistant today.

---

## 6. TTS — local (J-03, J-13, J-01)

The JARVIS voice target: **deep, calm, deliberate British male** — same voice every session, low first-audio latency (<300 ms ack, <1 s sentence), runs alongside a GPU already busy with Whisper+LLM.

| Engine | Stars | License | Maintenance (Sep 2026) | pip install | Cloning | Latency CPU / GPU | Languages | JARVIS fit |
|---|---|---|---|---|---|---|---|---|
| [Kokoro-82M](https://github.com/hexgrad/kokoro) (+ [kokoro-onnx](https://github.com/thewh1teagle/kokoro-onnx) ~2.7k, MIT, active 2026-09) | ~8.7k / 11.5M HF downloads | Apache-2.0 | Repo 2025-08; **kokoro-onnx 0.6.1 (2026-08-19)** — very active | `pip install kokoro-onnx` | **No** | **~0.1-0.3 s per sentence on CPU** (ONNX, int8); faster than real-time | 8-9 (en, zh, fr, ja, …) | Best low-latency "ack" voice; several credible male voices but no custom JARVIS voice; stable long-form, minimal artifacts ([2026 community consensus](https://www.reddit.com/r/LocalLLM/comments/1uh2xyh/what_is_the_best_opensource_tts_model_right_now/)). |
| [Chatterbox](https://github.com/resemble-ai/chatterbox) (Resemble AI) | ~26.3k | MIT | Active — `0.1.7` (2026-03-26) | `pip install chatterbox-tts` | **Zero-shot, ~5-10 s reference** | slow on CPU; RTF <1 on GPU (RTX 3060+) | 23 (multilingual, 2025) | The 2026 default for a **signature cloned voice**: emotion/exaggeration control helps "British-dry" delivery; MIT license is the cleanest of the cloners. |
| [XTTS-v2](https://github.com/coqui-ai/TTS) / community fork [idiap/coqui-ai-TTS](https://github.com/idiap/coqui-ai-TTS) (~2.3k) | ~46k (orig) | **CPML — non-commercial** | Company dead 2024; fork active (pushed 2026-06) | `pip install coqui-tts` (fork) | 3 s zero-shot, cross-lingual | ~1-3 s CPU; RTF ~0.3 GPU | 16 + cross-lingual clone | Aging but proven; the cross-lingual trick (clone JARVIS voice speaking any language) directly serves **J-13**; license blocks commercial use only — fine for a personal assistant. |
| [F5-TTS](https://github.com/SWivid/F5-TTS) | ~15.2k | MIT | Active (pushed 2026-07-23) | `pip install f5-tts` | Zero-shot ref | GPU-only for real-time (flow-matching steps) | en+zh core, community ext | Very natural cloning; heavier; good for pre-rendered briefings. |
| [Piper 1.x](https://github.com/OHF-Voice/piper1-gpl) (successor of archived [rhasspy/piper](https://github.com/rhasspy/piper), archived 2025) | ~5.5k | **GPL-3.0** | Very active — `piper-tts 1.8.0` (2026-09-04) | `pip install piper-tts` | No | **Fastest CPU TTS** (~50-150 ms per sentence) | 60+ | Voice too flat for JARVIS persona; perfect for sub-200 ms acknowledgments ("Yes, sir.") and J-13 breadth. |
| [MeloTTS](https://github.com/myshell-ai/MeloTTS) | ~7.6k | MIT | Stale (2024-12) | `pip install meloTTS`-style | No | ~real-time CPU | en/es/fr/zh/ja/ko | CPU multi-lang fallback; quality below Kokoro. |
| [StyleTTS2](https://github.com/yl4579/StyleTTS2) | ~6.3k | MIT | Stale (2024-08) | repo install | Style/ref | GPU | en (+LJSpeech-class) | High ceiling, painful Windows setup, stale. Skip in 2026. |
| [Orpheus](https://github.com/canopyai/Orpheus-TTS) | ~6.3k | Apache-2.0 | Cooled (2025-12) | repo + vLLM | via ref | ~200 ms with vLLM GPU | en + EU | Llama-3.2-3B based; emotion tags; superseded by Chatterbox/Qwen3-TTS for most. |
| **2026 wave:** [VibeVoice](https://github.com/microsoft/VibeVoice) (MS, ~53.8k★, MIT, active 2026-09 — long-form up to 90 min, 1-4 speakers), [IndexTTS-2](https://github.com/index-tts/index-tts) (~23.8k★, zero-shot + emotion, weights license custom), [Qwen3-TTS](https://github.com/QwenLM/Qwen3-TTS) (~13.3k★, Apache-2.0, ~30 s ref cloning, 10 langs), [fish-speech](https://github.com/fishaudio/fish-speech) (~32.6k★), [Dia](https://github.com/nari-labs/dia) (~19.4k★, dialogue-centric, cooled 2025-11), [NeuTTS Air](https://github.com/neuphonic/neutts) (~6.3k★, 0.5B on-device CPU cloning) | — | mixed | mostly active | — | yes | GPU-heavy for the big ones | The cloning frontier moved here; VibeVoice is the standout for long briefings (J-08), Qwen3-TTS/IndexTTS-2 for quality clones. |

**Two-voice strategy (recommended):** one **latency voice** (Kokoro ONNX male voice — "acknowledgments": "On it, sir." <250 ms, CPU, never competes with the LLM for GPU) and one **signature voice** (Chatterbox or XTTS-fork clone of a deep British male, rendered on GPU for full responses). Pre-clone once into a fixed reference WAV so the persona is stable across boots (J-03), and cache/render anything long (briefings, J-08) with VibeVoice or the cloud.

**JARVIS voice recipe (concrete):**
1. **Ack voice:** Kokoro `bm_george` / `bm_lewis` (its two British-male voices; deeper/drier than the American set) via `kokoro-onnx` int8 on CPU. Reserve for interjections and status lines so perceived latency stays <300 ms.
2. **Signature voice:** source 30-60 s of clean deep-British-male reference (licensed sample or your own recording — do not clone a film actor's voice for anything distributable; personal-use cloning legality is your call, distribution is not). Chatterbox with `exaggeration` low (~0.3-0.4) and a slow-ish pacing gives the calm "never gushing" delivery the persona spec (J-03 §1) demands; XTTS-fork if you need that same voice to also speak 16 languages (J-13) or want cross-lingual transfer.
3. **Consistency:** store the reference WAV + engine + parameters in the persona module (versioned, eval-able per J-03), not scattered in code.
4. **Long-form:** VibeVoice (90-min capability) or Eleven v3 for briefings; pre-render overnight, not live.
5. **Latency guardrail:** start TTS on the LLM's first sentence boundary (sentence-split streaming), not on completion — Kokoro streams via ONNX; Chatterbox needs full-sentence input.

---

## 7. TTS — cloud (J-03, J-13)

| Provider | Model | Latency (streaming) | Price | Steerability | Notes |
|---|---|---|---|---|---|
| [ElevenLabs](https://elevenlabs.io/docs/overview/models) | Flash v2.5 / Eleven v3 | **~75 ms** Flash; ~0.5-1 s v3 | Flash ≈ 0.5 credits/char (~$0.04-0.08/min equiv); v3 $0.10/1k chars; [Mar 2026 pricing reset](https://inworld.ai/resources/elevenlabs-v3-review) | Voice cloning + v3 audio tags (laughs, pacing) | The quality king; the "JARVIS" voice can be cloned once and reused; free tier 10k credits/mo. |
| [OpenAI](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts) | gpt-4o-mini-tts | ~200-400 ms | **~$0.015/min** ($0.60/1M text-in + $12/1M audio-out) | **Natural-language voice prompts** ("speak like a calm, dry British butler") — per-request persona control | The cheapest way to get a *steerable* persona voice; no cloning. |
| [Google](https://ai.google.dev/gemini-api/docs/pricing) | Gemini 2.5 Flash TTS / Chirp 3 HD | ~300-500 ms | ≈ $0.031-0.037/min ($0.50/1M text-in + $10/1M audio-out) | Style prompts, 30 voices, multilingual | Already an ULTRON dependency; one more adapter away. |
| Deepgram (Aurora) / Cartesia / Hume | — | ~100-250 ms | similar range | various | Cartesia Sonic is a latency leader (~90 ms); Hume for expressive EVI. Worth tracking, not required. |

**Pick:** OpenAI gpt-4o-mini-tts with a fixed persona instruction as the default cloud voice (cheap + steerable = ideal for J-03 iteration); ElevenLabs Flash/v3 if/when a cloned celebrity-grade JARVIS voice is wanted. Local Kokoro remains the free always-available fallback — the gateway must survive network loss.

**Cloud TTS detail:**
- **ElevenLabs** — `elevenlabs` pip SDK; Flash v2.5 at ~75 ms is the fastest cloud voice and 32 languages; Eleven v3 ($0.10/1k chars) adds audio tags (`[laughs]`, `[pause]`) that map nicely onto persona beats; cloning needs seconds of reference audio and a paid tier. Note the [March 2026 pricing reset](https://inworld.ai/resources/elevenlabs-v3-review) — re-check before committing. Serves J-03, J-13.
- **OpenAI** — `gpt-4o-mini-tts` accepts an *instructions* string per request: the persona module can emit "deep, calm, dry British butler; deliberate pacing; never gushing" as text and the delivery follows — the cheapest possible J-03 voice-iteration loop (edit a prompt, not a fine-tune). ~$0.015/min. Serves J-03, J-13 (~50 langs, quality varies).
- **Google** — Gemini 2.5 Flash TTS (30 voices, style prompts) rides the existing ULTRON Google dependency; ~$0.031-0.037/min; Chirp 3 HD available via Cloud TTS for batch. Serves J-03.

---

## 8. Speaker ID / diarization (J-02)

Goal: enroll the household (3-10 voices) from ~10 s samples each; on every wake, identify the speaker in <500 ms and load **per-user memory/persona**; diarize long session transcripts for J-04 memory ("You mentioned in March…").

| Toolkit | Stars | License | Maintenance (Sep 2026) | pip install | Role | Windows | Notes |
|---|---|---|---|---|---|---|---|
| [SpeechBrain](https://github.com/speechbrain/speechbrain) | ~11.8k | Apache-2.0 | Active — `1.1.1` (2026-08-27) | `pip install speechbrain` | **Enrollment + real-time ID**: ECAPA-TDNN embeddings (`spkrec-ecapa-voxceleb`), cosine vs enrolled centroids | Yes (PyTorch) | Simplest robust path to "who is talking" on a live stream; embeddings in ~10-50 ms CPU. |
| [pyannote.audio](https://github.com/pyannote/pyannote-audio) | ~10.5k | MIT | Very active — **4.0.7** (2026-06-30), ships [community-1](https://www.pyannote.ai/blog/community-1) diarization model | `pip install pyannote.audio` | **Diarization** (who spoke when) + embeddings; SAD/OSD | Yes | 4.0 + community-1 beats 3.1 on every metric; gated models (HF token + ToS accept). Commercial live diarization <300 ms exists via pyannote.ai, but for ULTRON open-source is enough. |
| [NeMo Speech](https://github.com/NVIDIA-NeMo/Speech) | ~18.4k | Apache-2.0 | Very active (pushed 2026-09-06) | heavy (conda/NGC) | Streaming diarization (**Sortformer**), ASR+diarization end-to-end | Yes but heavyweight | Adopt only if already in the NeMo ecosystem for Parakeet. |
| [Resemblyzer](https://github.com/resemble-ai/Resemblyzer) | ~3.3k | Apache-2.0 | Stale (2023-10) | `pip install resemblyzer` | Dead-simple GE2E embeddings | Yes | Fine lightweight fallback; noisier embeddings than ECAPA. |
| [3D-Speaker](https://github.com/modelscope/3D-Speaker) | ~3.1k | Apache-2.0 | Touched 2025-12 | repo | ERes2NetV2 embeddings, verification, diarization | Yes | Strong models, thinner docs. |

**Pick:** SpeechBrain ECAPA-TDNN for the live identification loop (enroll once per user; per-utterance embedding → cosine sim → threshold → identity to kernel), pyannote 4.0 offline for diarizing session audio into J-04 memories. **Fallback:** Resemblyzer if torch install weight is unacceptable. Word timestamps from WhisperX/faster-whisper align identities to transcript tokens.

**Enrollment + identification recipe (J-02, buildable in a day):**
1. **Enroll:** dashboard button records 10-20 s per person → 3-5 ECAPA embeddings (different segments) → store mean + per-segment vectors + threshold (cosine ~0.75-0.85 works for VoxCeleb ECAPA; calibrate per household) in the per-user memory record.
2. **Identify live:** on each VAD-closed utterance, extract one embedding (~10-50 ms CPU) → cosine vs enrolled centroids → `identity` or `unknown`. Feed `{"speaker": "Tony", "address": "sir"}` into the kernel turn context; unknowns get "Sir/Miss?" handling and a one-time enroll prompt.
3. **Robustness:** ECAPA degrades on far-field/laptop mics — enroll with the same mic and room, re-enroll on hardware change. Cross-talk: skip embedding when two VAD streams overlap.
4. **Diarize offline:** nightly, run pyannote community-1 + WhisperX over the day's audio → speaker-attributed transcripts → per-person memory consolidation (J-04). This keeps the expensive diarization off the live path entirely.

---

## 9. Windows audio plumbing & the echo problem (J-01)

ULTRON's current loop (`main.py`) already uses 16 kHz PCM16 in / 24 kHz out with sounddevice-style streaming; the additions below are what's missing.

| Library | Stars | License | Maintenance | pip install | What it does on Windows |
|---|---|---|---|---|---|
| [python-sounddevice](https://github.com/spatialaudio/python-sounddevice) | ~1.3k | MIT | Active — `0.5.6` (2026-08-17) | `pip install sounddevice` | CFFI/PortAudio binding; WASAPI/MME/WDM-KS/ASIO host APIs; callback streams; low latency. Recent PortAudio builds expose WASAPI **loopback** endpoints (speakers as input). |
| [PyAudioWPatch](https://github.com/s0d3s/PyAudioWPatch) | ~241 | MIT (NOASSERTION tag) | Active — `0.2.12.8` (2026-01-14) | `pip install pyaudiowpatch` | PyAudio fork with **guaranteed WASAPI loopback** (`get_loopback_device_info_generator()`) — the proven way to record "what the PC plays" ([MS loopback docs](https://learn.microsoft.com/en-us/windows/win32/coreaudio/loopback-recording)). |
| `soundcard` (PyPI) | — | BSD | semi-active | `pip install soundcard` | Pure-Python-ish loopback alternative; slower path, but simple. |

**Use cases:**
1. **"What did it just say?"** — capture JARVIS's own speech via WASAPI loopback → cheap local STT (faster-whisper) → transcript into memory (J-04). Because ULTRON *generated* that audio, you already have the exact text; loopback is only needed for *other* PC audio (YouTube, calls) — a "what am I hearing" sense.
2. **Mic selection** — enumerate WASAPI devices; prefer a dedicated mic; 16 kHz mono for all local models (resample from 48 kHz).

Loopback capture with PyAudioWPatch (minimal shape):

```python
import pyaudiowpatch as pyaudio
with pyaudio.PyAudio() as p:
    wasapi = p.get_host_api_info_by_type(pyaudio.paWASAPI)
    spk = p.get_device_info_by_index(wasapi["defaultOutputDevice"])
    if not spk["isLoopbackDevice"]:
        spk = next(l for l in p.get_loopback_device_info_generator()
                   if spk["name"] in l["name"])
    stream = p.open(format=pyaudio.paInt16, channels=spk["maxInputChannels"],
                    rate=int(spk["defaultSampleRate"]), frames_per_buffer=512,
                    input=True, input_device_index=spk["index"])
```

Note: WASAPI loopback runs at the *mix format* rate (usually 48 kHz stereo) — resample to 16 kHz mono before VAD/STT. Some apps exclude themselves from loopback; verify against the actual player.

**Echo / self-hearing (the real danger of open-mic + speakers):** when JARVIS talks through speakers, its own voice enters the mic → wake-word false triggers, VAD never closes, the LLM hears itself. Options, in order of practicality:
1. **Software duck/gate (what ULTRON does now):** while TTS is playing, keep mic buffer but discard/suppress wake+VAD; on interrupt (user speech detected *above* TTS level) stop within 50 ms. Simple, but blind to true barge-in at low volume.
2. **AEC with reference signal:** feed the TTS output stream to an echo canceller as the reference. Python options: `speexdsp-python` (`pip install speexdsp`), [thewh1teagle/aec](https://github.com/thewh1teagle/aec) (Speex-based engine with Python bindings), or WebRTC Audio Processing bindings; a 2026 survey of approaches: [AEC + barge-in primer](https://vocal.com/echo-cancellation/aec-barge-in/). This is what makes *true* full-duplex work over speakers.
3. **Virtual audio cable routing:** route JARVIS's TTS to a virtual output (VB-Cable) that the mic physically never hears, while loopback still records it — hacky but bulletproof.
4. **Headphones** — sidesteps everything, but breaks the "speaks across the room" JARVIS fantasy.

**Note for realtime adapters:** Gemini Live/OpenAI Realtime have **no server-side AEC** — the client must not feed echo into the session. So the AEC/ducking layer lives below the gateway and benefits every adapter equally.

---

## 10. Recommended architecture

ULTRON's roadmap calls for a provider-agnostic kernel where the Live audio loop becomes a **modality adapter**. Concretely: define `VoiceGateway` (kernel interface) exposing `start/stop`, `on_wake`, `on_user_audio/turn`, `speak(text|stream)`, `interrupt()`, `on_transcript(identity)`. Three adapters behind it:

### Option A — Local-only cascade (free, private, offline)
```
mic 16 kHz → openWakeWord("hey_jarvis" + "hey_ultron")  [~2% CPU, always on]
          → Silero VAD (32 ms chunks)
          → ECAPA-TDNN speaker ID (per utterance, J-02)
          → faster-whisper small-int8 via RealtimeSTT (partials → early LLM priming)
          → local LLM (Ollama/Qwen via existing core/llm_client.py)
          → Kokoro-ONNX ack (<250 ms) + Chatterbox signature voice (GPU)
speakers ← duck/gate + Speex AEC reference ← TTS stream
```
**Latency budget (RTX-class Windows PC):** wake 150-300 ms + VAD 32 ms + STT first-final 300-600 ms + LLM first token 150-400 ms + Kokoro first audio 100-250 ms ≈ **0.7-1.5 s** to first speech (vs ~0.5-0.8 s on Gemini Live today). Multilingual: Whisper-class STT is fine; Kokoro/Chatterbox cover fewer languages (J-13 weaker locally). Turn-taking is DIY (Silero + AEC) — good, not magical.
**Pros:** $0/mo, offline, private, no rate limits. **Cons:** voice quality < Gemini voices; DIY full-duplex; GPU contention needs care (Whisper and TTS and LLM sharing one card).

### Option B — Cloud-hybrid cascade (recommended default)
Same front-end (wake + VAD + speaker ID stay local — they must run before any network call), then: Deepgram Nova-3 streaming STT → any LLM through the model gateway → OpenAI gpt-4o-mini-tts (steerable persona) or ElevenLabs Flash, streamed out through the same 50 ms interrupt drain ULTRON already has.
**Latency:** wake+VAD local (200-400 ms) + Nova-3 ~200-300 ms + LLM 200-400 ms + TTS 75-400 ms ≈ **0.7-1.4 s**, with a *much* better voice and multilingual coverage than Option A.
**Cost at 30 min/day of talking:** STT ~$0.15-0.46/hr → ~$2-7/mo; TTS ~$0.015-0.08/min → ~$7-36/mo; LLM per gateway. Realistic: **$10-40/mo**.
**Pros:** JARVIS-grade quality now, per-component vendor swaps, graceful degradation to Option A on network loss. **Cons:** monthly cost, two cloud deps instead of one.

### Option C — Realtime S2S adapter (what ULTRON has today, generalized)
Gemini Live (native audio, server VAD, function calling) as the default; OpenAI gpt-realtime-2.1 and Nova 2 Sonic as swappable adapters implementing the same `VoiceGateway` events. Local openWakeWord + Silero still gate the session (in-session wake, echo control) so the S2S model isn't always-on-the-mic.
**Latency:** ~0.5-0.9 s turn latency with genuine semantic turn-taking and interruption — the best "feels alive" result, and ULTRON has already built the hard parts (queue drain, reconnect TaskGroup, phone relay).
**Cost at 30 min/day:** Gemini ≈ $27-54/mo; OpenAI ≈ $40-70/mo; Nova 2 Sonic ≈ $8-15/mo. Plus 15-min session resumption handling (already needed today).
**Pros:** best duplex feel, least pipeline code, vision-in-the-voice-loop possible. **Cons:** vendor lock per adapter, per-vendor tool-calling quirks, session limits, cost scales with talking.

### Synthesis
Ship **B as the default adapter set with A as offline fallback and C as the premium experience**, all behind `VoiceGateway`. The wake/VAD/AEC/speaker-ID front-end (§1, §2, §8, §9) is shared by all three — build it once. This is exactly the roadmap's "Live audio loop becomes a modality adapter": nothing already built is thrown away; the Gemini loop is wrapped, not replaced.

### Latency budget (Option A, measured-component estimates, RTX-class Windows PC)

| Stage | Component | p50 | Notes |
|---|---|---|---|
| Wake → gate open | openWakeWord | 100-300 ms | from end of phrase |
| Speech-end detection | Silero VAD | 32 ms/chunk | runs continuously |
| Speaker ID | ECAPA-TDNN | 10-50 ms | parallel with STT |
| First final transcript | faster-whisper small int8 (GPU) | 300-600 ms | partials appear ~150 ms earlier |
| LLM first token | local 7-14B (Ollama) | 150-400 ms | vLLM faster |
| First audio out | Kokoro-ONNX (CPU) | 100-250 ms | on first sentence |
| **Total → first speech** | | **~0.7-1.5 s** | vs ~0.5-0.8 s Gemini Live today |

Cloud options shift the middle rows: Deepgram ~250 ms, gpt-4o-mini-tts ~300 ms → similar total (~0.7-1.4 s) with better quality; S2S adapters (Option C) collapse rows 3-6 into one model (~0.5-0.9 s) — the structural reason the duplex feel wins there.

### Monthly cost at three usage levels (voice layers only, LLM excluded)

| Usage | Option A local | Option B hybrid | C: Gemini Live | C: OpenAI realtime | C: Nova 2 Sonic |
|---|---|---|---|---|---|
| 10 min/day (~5 h/mo) | ~$0 | ~$2-7 | ~$5-15 | ~$15-20 | ~$1.5-4 |
| 30 min/day (~15 h/mo) | ~$0 | ~$10-40 | ~$27-54 | ~$40-70 | ~$8-15 |
| 2 h/day (~60 h/mo) | ~$0 | ~$40-160 | ~$110-215 | ~$160-280 | ~$30-60 |

(A: electricity + hardware amortization only. Ranges span model tiers; verify live pricing before committing.)

---

## 11. J-13 multilingual coverage matrix (who speaks what, per layer)

Mid-sentence language switching (the film-level J-13) decomposes into: STT coverage → LLM language ability (gateway's problem, not this doc's) → TTS coverage. Voice *identity* should ideally persist across languages (XTTS-v2's cross-lingual cloning and ElevenLabs multilingual models are the only tools that keep the same cloned voice across languages).

| Layer | Engine | Languages | Same voice across langs? |
|---|---|---|---|
| STT local | faster-whisper / whisper.cpp | ~99 | n/a |
| STT local (fast) | Parakeet-tdt-0.6b-v3 / Canary-1B-v2 | 25 European (+ translation in Canary) | n/a |
| STT cloud | Deepgram Nova-3 multilingual / AssemblyAI | 30+/multi | n/a |
| STT S2S | Gemini Live | 70+ | n/a |
| TTS local (ack) | Kokoro v1.x | 8-9 (en, zh, fr, ja, ko, hi, it, pt-br…) | no (voice-per-language) |
| TTS local (signature) | Chatterbox multilingual | 23 | yes (cloned voice, per-lang rendering) |
| TTS local (signature alt) | XTTS-v2 fork | 16 + cross-lingual | **yes** (the cross-lingual trick) |
| TTS local (breadth) | Piper 1.x | 60+ | no |
| TTS cloud | ElevenLabs Flash/v2 | 32 | **yes** (cloned voice) |
| TTS cloud | OpenAI / Gemini TTS | ~50 / 24+ | voice-per-language mostly |
| S2S | Gemini Live native audio | 70+ | model voices, consistent-ish |

**J-13 verdict:** cloud S2S (Gemini) is currently the only single-component answer to "speaks any language, switches mid-sentence". Local-first J-13 = Whisper-class STT (fine) + Chatterbox/XTTS-fork for the signature voice (adequate) + accept per-language ack voices with Kokoro/Piper. Design the persona module so voice selection is a function of `(speaker, language, latency_budget)` — that abstraction makes the gap invisible to the kernel.

---

## RECOMMENDATION

### Top picks + fallbacks (all verified Sep 2026)

| Layer | Top pick | Fallback | Why |
|---|---|---|---|
| Wake word (J-01) | **openWakeWord** (`hey_jarvis` pretrained; train `hey_ultron`) | Porcupine (paid) / microWakeWord (ESP32) | Free, Apache-2.0, local, prebuilt JARVIS model; Picovoice free tier dies 2026-06-30 |
| VAD (J-01) | **Silero VAD v6** (ONNX) | webrtcvad | <1 ms/32 ms-chunk, MIT, streaming-native |
| STT local (J-01/J-13) | **faster-whisper** int8 via **RealtimeSTT**; **Parakeet-tdt-0.6b-v3** for bulk | whisper.cpp (CPU-only), sherpa-onnx streaming Zipformer (hard real-time), Moonshine (edge) | Word timestamps, ~99 langs, active ecosystem |
| STT cloud | **Deepgram Nova-3** streaming | AssemblyAI Universal-Streaming (cheapest) | <300 ms, keyterm boost |
| Duplex S2S (J-01) | **Gemini Live** (keep, behind gateway) + **OpenAI gpt-realtime** adapter | Nova 2 Sonic (cost), Kyutai Unmute (open self-host) | Proven in ULTRON; semantic turn-taking; function calling |
| Orchestration | ULTRON's own kernel gateway (+ pipecat reference patterns) | LiveKit Agents (if WebRTC/telephony needed), ~~Vocode~~ (dead) | Matches roadmap; avoid re-platforming |
| TTS local (J-03/J-13) | **Kokoro-ONNX** (ack) + **Chatterbox** (signature clone) | XTTS-fork (cross-lingual clone, non-comm license), Piper 1.x GPL (fast acks), VibeVoice (long briefings) | Latency + persona + cloning covered by two engines |
| TTS cloud (J-03) | **OpenAI gpt-4o-mini-tts** (steerable persona) | ElevenLabs Flash/v3 (cloned premium voice), Gemini TTS | ~$0.015/min, persona-as-prompt |
| Speaker ID (J-02) | **SpeechBrain ECAPA-TDNN** (live ID) + **pyannote 4.0** (offline diarization) | Resemblyzer (lightweight) | MIT/Apache-2.0, active, Windows-fine |
| Audio I/O | **sounddevice** (mic) + **PyAudioWPatch** (WASAPI loopback) | soundcard | Loopback enables "what did it just say" + AEC reference |

### Integration notes for ULTRON
1. **Current loop lives in `main.py`** (`LIVE_MODEL` at `main.py:68`; interrupt drain ~50 ms slices at `main.py:540-545`; `audio_in_queue`; reconnecting TaskGroup at `main.py:929-955`). Wrap — don't rewrite — this into `VoiceGateway` + `GeminiLiveAdapter` per `docs/ROADMAP.md`; add `LocalCascadeAdapter` (Option A) and `OpenAIRealtimeAdapter` (Option C) as siblings.
2. **In-session wake (the real J-01 gap):** today `wake_service.py` is a *process launcher* (speech_recognition polling, launches `main.py`). Move openWakeWord + Silero into the always-on front-end task inside the kernel; wake should open a session, not start a program. Kill the phrase-list hack (`"weak up ultron"` etc.) — a trained model does not need typo variants.
3. **Barge-in etiquette (J-03 item 4):** keep the 50 ms drain; add "resume or abandon" logic — currently interrupt kills the utterance; a flag should decide whether the assistant restarts the sentence in shorter form or drops it ("I'll spare you the details, sir").
4. **Speaker ID first value:** one ECAPA enrollment per person (10 s audio, done in the dashboard once), identity injected into the kernel as `{speaker: "Tony", role: "sir"}` — unlocks per-user memory (J-02) and correct address ("sir") without any model upgrade.
5. **Echo:** implement the duck/gate immediately (free), Speex AEC reference feed when speakers-only mode matters; note that realtime S2S adapters *require* the client to suppress echo — this layer must sit below the gateway.
6. **TTS two-voice setup:** Kokoro-ONNX for <250 ms acknowledgments (CPU — never blocks the GPU), Chatterbox one-time clone for the signature voice; both rendered through the same stream player with the existing 50 ms slice interrupt.
7. **Cost guardrail:** the gateway should meter audio-minutes per adapter/day; if talking exceeds ~45 min/day, S2S adapters get more expensive than the hybrid cascade — auto-switch or warn.
8. **Watchlist:** Gemini 3.1 Flash Live (preview → GA), Nova 2 Sonic price/latency, Kokoro v2 / Qwen3-TTS local-clone quality, livekit-wakeword, Kyutai Pocket TTS (CPU cloning), Silero v6 "ifless" models.

*Verification method: GitHub REST API (stars, pushed_at, archived, license) for every repo listed; PyPI JSON for pip-package versions; Hugging Face API for model licenses (Parakeet/Canary CC-BY-4.0, Kokoro apache-2.0) and download counts; vendor pages for cloud pricing (flagged where vendor-sourced). Companion docs: `01_jarvis_feature_catalog.md` (features), `../ROADMAP.md` (phases).*
