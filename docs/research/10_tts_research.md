# 10 — TTS Deep-Dive: Piper1-GPL · Piper (legacy) · Kokoro · Cartesia Sonic

*Written 2026-09-13. Research stream: local/cloud TTS engines for ULTRON's voice output.
Method: repos shallow-cloned to `C:/Users/ceoha/repos/tts-research/` and the core
source files read line-by-line (not doc summaries). Complements the strategy-level
TTS section in `02_voice_stack.md` §6–7 (two-voice strategy) — this doc is the
**code-level** read + a concrete, prioritized ADOPT LIST for our codebase.*

---

## 0. Executive verdict

**ULTRON today has NO local TTS at all.** The only voice output is Gemini Live's
server-side TTS streamed into `audio_in_queue` (`app/audio.py:254` `_play_audio`,
plain `sounddevice.RawOutputStream`). `main.py:305 speak()` just pushes text into
the Live session — if the Live session is down, degraded, or the user runs the
local/Ollama path, ULTRON is mute. That is a single point of failure **and** a
latency/cost tax on every "Yes, sir."

The four artifacts studied here give us everything needed to fix that with ~2
days of adapter work, in this order:

| # | Adopt | From | Effort | Impact |
|---|---|---|---|---|
| A1 | `TtsEngine` seam + **piper-tts 1.8.0** backend | piper1-gpl | S | offline voice, zero new system deps, py-3.14 verified |
| A2 | **Python port of `TextSplitterStream`** | kokoro.js | S | LLM-token-stream → sentence-chunked speech (the latency unlock) |
| A3 | **kokoro-onnx** backend for the ack voice | hexgrad/kokoro | M | JARVIS-quality 82M model, 24 kHz, CPU real-time |
| A4 | Pronunciation override lexicon (`golds` / `[[phonemes]]`) | Kokoro + Piper | S | "ULTRON/JARVIS/names" said correctly |
| A5 | Voice download manager (HF URL pattern) | piper1-gpl | S | on-demand voices into `.ultron/voices/` |
| A6 | RTF/latency telemetry + volume/normalize knobs | piper C++/Python | S | observability + persona pacing |
| A7 | Phoneme→audio alignments → word timestamps | Piper + Kokoro `duration` output | M | dashboard "now speaking" highlight, HUD lip-sync (J-20) |
| A8 | Cartesia-style cloud adapter (reference only) | Sonic | parked | premium voice tier if ever wanted |

Both candidate engines resolve on **Python 3.14** today (verified below), and
`onnxruntime 1.30.0` is already importable in the runtime — the embedder seam
proved the ONNX path months ago.

---

## 1. piper1-gpl — the maintained Piper (`pip install piper-tts`)

Repo: `OHF-Voice/piper1-gpl` @ `404aefe` (2026-09-09, very active; Home
Assistant's authors). License **GPL-3.0-or-later** (runtime package). 145 files.

### Architecture (files actually read)

```
text ──► PiperVoice.phonemize()            voice.py:202
          ├─ PhonemeType.ESPEAK  → EspeakPhonemizer (espeakbridge C-ext, bundled espeak-ng-data)
          ├─ TEXT/PINYIN/HEBREW/JAPANESE/THAI → per-lang phonemizers (lazy-loaded, cached on voice)
          └─ [[raw phonemes]] blocks       _PHONEME_BLOCK_PATTERN voice.py:28 — manual pronunciation escape
     ──► phonemes_to_ids()                 phoneme_ids.py:182 (BOS/PAD … EOS framing)
     ──► onnxruntime session.run()         voice.py:547 (inputs: input, input_lengths, scales, sid)
     ──► AudioChunk generator              voice.py:433 — ONE CHUNK PER SENTENCE
```

Key findings, line-referenced:

- **Sentence streaming is the core API.** `synthesize()` (voice.py:333) is a
  generator yielding one `AudioChunk` per espeak clause — playback can start
  before the whole text is rendered. `__main__.py:184-193` streams raw s16le to
  stdout with `flush()` per chunk; `audio_playback.py` pipes to `ffplay`.
- **`AudioChunk` (voice.py:41)** carries float array + lazily-cached int16
  conversion (`audio_int16_array` voice.py:78) — float→int16 deferred until
  actually played/saved. Nice micro-pattern; we pay the conversion only once.
- **Zero system dependencies.** `espeak-ng-data` ships *inside the wheel*
  (setup.py:10-12); deps are just `onnxruntime` + `pathvalidate` (setup.py
  install_requires). Windows needs no espeak MSI — unlike raw Kokoro.
- **`SynthesisConfig` (config.py:132)**: `speaker_id`, `length_scale` (speed,
  <1 faster), `noise_scale`, `noise_w_scale` (prosody variance),
  `normalize_audio`, `volume`. Defaults 0.667/1.0/0.8 (config.py:7-9). This is
  the entire "voice feel" control surface — map onto ULTRON config keys.
- **`phoneme_silence` phrase splitting** (rhasspy/piper `piper.cpp:508-532`,
  config at `piper.cpp:144-149`): per-phoneme silence injection — split the
  sentence at pause phonemes (comma etc.) into separately-synthesized phrases
  with inserted silence. Cheap prosody fix for Piper's flatness; also reduces
  per-inference sequence length.
- **Alignments**: `PiperVoice.load(include_alignments=True)` (voice.py:170-189)
  patches the ONNX graph in memory to expose a durations tensor →
  `phoneme_id_samples` → per-phoneme `PhonemeAlignment` with `num_samples`
  (voice.py:382-431). Word timestamps without retraining.
- **HTTP server** (`http_server.py:216` `/synthesize` POST → WAV) — a complete
  reference for exposing TTS to the dashboard, including a per-utterance
  `synthesize_seconds` telemetry block (http_server.py:110-131).
- **Voice download**: `download_voices.py:11` — deterministic HF URL format
  `rhasspy/piper-voices/.../{lang}-{voice}-{quality}.onnx` + `voices.json`
  catalog; `_needs_download()` checks existence/empty only (no hash). Reusable
  for a ULTRON `voice_manager`.
- **RTF computed everywhere** (`SynthesisResult.real_time_factor`,
  piper.cpp:402-406) — adopt as an observability metric.

### Fit for ULTRON

Perfect **default local engine**: fastest CPU TTS (50-150 ms/sentence per
`02_voice_stack.md` §6), Windows wheels, 60+ languages (J-13 breadth), bundled
espeak. Weakness: flat prosody → use for long content; pair with Kokoro for the
persona voice. License GPL-3.0 is fine for a local personal app; if we ever
distribute non-GPL, keep piper behind an optional extra (`requirements-tts-piper.txt`).

---

## 2. rhasspy/piper — the legacy C++ original (archived 2025)

Repo @ `73c04d8`. Still valuable as a *design reference*; do not adopt code.

- **ONNX session tuning is benchmarked, not guessed** (`piper.cpp:262-306`):
  `DisableTelemetryEvents`, graph optimization **DISABLE_ALL** (they measured
  ORT_ENABLE_EXTENDED as "roughly doubles load time for no visible inference
  benefit"), `DisableCpuMemArena` + `DisableMemPattern`. Worth copying into our
  `InferenceSession` options when we build the TTS seam.
- **audioCallback streaming** (`piper.cpp:591-595`): buffer is cleared and the
  callback invoked per sentence — the C-side pattern our queue-based playback
  already implements.
- **SpeechStreamer** (`piper_train/infer_onnx_streaming.py:20-124`) — the most
  interesting artifact: the VITS model is exported as **two ONNX graphs**
  (encoder → latent `z`; decoder → audio) and the decoder runs on
  *overlapping mel-frame chunks*: `chunk_size=45` frames (~0.42 s at hop 256),
  `chunk_padding=10` frames of context from the neighbor chunks to hide
  stitching artifacts (`infer_onnx_streaming.py:76-108`). First-chunk latency =
  encoder + one chunk decode instead of the whole utterance. This exact
  overlap-pad chunking scheme is what modern streaming TTS (incl. kokoro-onnx's
  stream mode) builds on — keep the file as a reference implementation.
- **Training pipeline** (`piper_train/`: preprocess.py, VAD/trim/norm_audio,
  export_onnx.py) — viable path to a custom "ULTRON" voice later. **Parked**:
  user directive = no model training for now.
- MIT-licensed code — no contamination risk studying/porting from it.

---

## 3. hexgrad/kokoro — Kokoro-82M (Apache-2.0)

Repo @ `dfb907a`. 82M-param StyleTTS2-family model; **weights Apache-2.0**.
The GitHub repo is the *torch* inference library (`pip install kokoro`) — heavy
(torch dependency) → for ULTRON use **`kokoro-onnx`** (thewh1teagle, MIT,
onnxruntime-only) with the same model weights. Findings:

- **KPipeline** (README.md:20-24): `pipeline(text, voice='af_heart',
  split_pattern=r'\n+')` returns a generator of `(graphemes, phonemes, audio)`
  tuples — chunked synthesis by regex split, same streaming philosophy as
  Piper's sentence chunks.
- **Voice = style tensor indexed by phoneme count** (space `app.py:30-31`):
  `pack = pipeline.load_voice(voice)`; `ref_s = pack[len(ps)-1]` — the style
  vector is selected by utterance length (the pack is ~510 style rows). Model
  call: `KModel(ps, ref_s, speed)` → 24 kHz audio.
- **510-phoneme context cap** (export.py:51-52): chunks longer than 510
  phonemes must be split — relevant for our splitter design.
- **Pronunciation control** (space `app.py:18-19` + TOKEN_NOTE app.py:129-137):
  1. custom lexicon: `pipeline.g2p.lexicon.golds['kokoro'] = 'kˈOkəɹO'`
  2. inline markdown override: `[Kokoro](/kˈOkəɹO/)` in text
  3. stress adjust: `[word](+2)` / `[word](-1)`
  All three are trivially wrappable into a ULTRON "say it right" layer.
- **`generate_from_tokens`** (examples/phoneme_example.py:30-56): synthesize
  from raw phoneme strings, with per-token `start_ts/end_ts` timestamps —
  word-level timing for HUD highlighting.
- **ONNX export recipe** (examples/export.py:19-40): `KModelForONNX` wrapper,
  inputs `(input_ids, style[1×256], speed)`, outputs `(waveform, duration)`,
  opset 17, dynamic axes. The `duration` output = free alignment signal.
- **Windows espeak pain** (README.md:89-96): torch-Kokoro wants a system
  espeak-ng MSI. kokoro-onnx + `espeakng-loader` (pip wheel, bundled data)
  avoids that entirely — same trick piper1-gpl uses.
- **kokoro.js** (`kokoro.js/src/`): browser port. Two adoptable ideas:
  - `TextSplitterStream` (`kokoro.js/src/splitter.js:109-344`) — an
    async-iterator sentence splitter designed for **streaming LLM output**:
    quote/bracket nesting stack (`updateStack`:80-104), abbreviation set
    (:40), URL/email protection (:237), middle-initial heuristic (:251),
    lowercase-lookahead for periods (:259), numbered-list skip (:204),
    `$9.99` mid-token guard (:212), ellipsis merge (:266). Holds the boundary
    until a non-space char confirms the sentence ended (:218). **This is the
    best sentence-splitter we've seen in any of the 10 research repos** and
    ports to Python ~1:1.
  - `stream()` (`kokoro.js/src/kokoro.js:118`): splitter → per-sentence
    synthesis → playback queue; text can arrive while earlier sentences are
    still speaking.

### HF Space `hexgrad/Kokoro-TTS` (`app.py`, 202 lines)

The space is a minimal production pattern, read in full: two `KPipeline`s (en-us
`'a'` / en-gb `'b'`, `model=False` — G2P only, model shared), voice→pipeline
routed by `voice[0]`, `generate_all()` streams `(rate, audio)` tuples with a
graceful GPU→CPU fallback (`app.py:37-43`), `CHAR_LIMIT` guard. Nothing else
hidden there — the value was confirming the pack[len(ps)-1] + streaming-yield
pattern above.

### Fit for ULTRON

The **persona/ack voice** (`02_voice_stack.md` §167 already picked `bm_george`
/ `bm_lewis`): 24 kHz, better prosody than Piper, CPU real-time via
kokoro-onnx int8, male British voices, Apache weights. Resolves on py-3.14
(verified below). Longer-term: token timestamps → HUD, `duration` output →
subtitles.

---

## 4. Cartesia Sonic (cloud, closed-source) — reference only

Studied via cartesia.ai/sonic (product page; docs behind signup). Not
clonable — no code to read.

- **Architecture**: state-space models (SSM) — their efficiency bet vs
  transformer TTS; "sub-90 ms" model latency claim; Sonic-3.6 current gen.
- **Voice cloning** from **10 s** of audio; instant, high similarity at scale.
- **Expressiveness**: model "interprets emotional subtext and calibrates
  delivery automatically"; inline non-verbal tags — `[laughter]` typed directly
  into the transcript text. Cheap to imitate in our prompt→TTS layer: let the
  LLM emit tags and the TTS adapter interpret/strip them.
- **Custom pronunciation dictionaries with inline phoneme notation** — same
  feature as Kokoro `golds` / Piper `[[…]]` blocks; three independent engines
  converged on it, which confirms A4 as a must-have.
- **44 languages**, emotion/tone carries across languages (J-13 relevant).
- **On-prem/VPC deployment** offered ("From Cloud to Local").

**Verdict**: nothing to import; it's a closed cloud API. What we *take* is the
product design: emotion tags in-band, pronunciation dictionaries, per-voice
latency SLOs, and the validation that SSM-style streaming low-latency TTS is
the direction. If ULTRON ever grows a premium cloud voice tier, it goes behind
the same `TtsEngine` seam as a `CartesiaTtsEngine` adapter (parked, A8).

---

## 5. ULTRON integration analysis (the gap this closes)

Current voice output path, read from code:

1. Gemini Live session streams TTS audio → `audio_in_queue` →
   `app/audio.py:254 _play_audio()` (sounddevice s16le, 50 ms barge-in slicing
   via `asyncio.wait_for` timeout + queue drain in `main.py:~290 interrupt`).
2. `main.py:305 speak(text)` sends text INTO the Live session (cloud speaks it).
3. The kernel voice stack (`kernel/voice/engines.py`) defines Protocol seams
   (`WakeWordEngine`, `VadEngine`, `SpeakerIdEngine`) with lazy loaders +
   `EngineUnavailable` — **the exact pattern to extend with `TtsEngine`**.
4. Flag-gated voice stack (`app/voice_stack.py`): `echo_gate_enabled`,
   `speaker_id_enabled` — precedent for a default-off `tts_backend` flag.
5. `docs/research/01_jarvis_feature_catalog.md` J-13 already names
   "XTTS/Kokoro" as the TTS direction; `02_voice_stack.md` §6 already picked
   the two-voice strategy. **No code exists yet** — this stream delivers the
   adapter design.

Gaps the adopt list closes: (a) mute when Live session is down or on the
Ollama/local path; (b) 300-800 ms cloud round-trip for "Yes, sir"-class
acks; (c) no offline mode; (d) no voice on the orchestrator/AgentLoop outputs.

---

## 6. ADOPT LIST (prioritized, concrete)

### A1 — `TtsEngine` seam + Piper backend (P1, effort S)
- New `kernel/voice/tts.py`: `TtsEngine` Protocol —
  `synthesize(text, config) -> Iterator[AudioChunk]` (mirror
  `piper1_gpl.AudioChunk`: rate/width/channels/float array), lazy
  `load_piper()` / `load_kokoro()` following `engines.py` style
  (`EngineUnavailable` naming the missing package).
- Config keys (default off — Kill List: no behavior change without flag):
  `tts_backend: none|piper|kokoro`, `tts_voice` (e.g.
  `en_US-lessac-medium` / `bm_george`), `tts_length_scale`, `tts_volume`,
  `tts_speaker_id`.
- Wire output into the **existing** `audio_in_queue` → `_play_audio()` path.
  Piper voices are 22.05 kHz, Kokoro 24 kHz vs Live's 24 kHz receive rate —
  `_play_audio` already opens its stream per call, so parameterize the rate
  from the chunk (or resample once with numpy linear interp; decide in impl).
- Deps: `requirements-tts.txt` optional extra: `piper-tts>=1.8,<2`
  (pulls onnxruntime ✓ + espeakng data ✓), verified resolvable:
  `py -3.14 -m pip install --dry-run piper-tts kokoro-onnx` →
  `Would install … espeakng-loader-0.2.4 … kokoro-onnx-0.4.7 … piper-tts-1.8.0`
  (2026-09-13). `onnxruntime 1.30.0` imports on py-3.14.7 (verified).

### A2 — Sentence-stream splitter (P1, effort S)
- Port `kokoro.js/src/splitter.js` → `app/text_splitter.py` (or
  `kernel/voice/`): async iterator `push(chunk)` / `async for sentence`.
  Keep ALL guards (abbreviations, URL/email, initials, nesting stack,
  lowercase lookahead, `$9.99`, ellipsis). ~330 LOC JS → ~300 LOC Python.
- Use case: LLM/gateway text deltas → splitter → per-sentence A1/A3
  synthesis → `audio_in_queue`. Speech starts at the first sentence
  boundary (the `02_voice_stack.md` §167 latency guardrail), not at
  completion. Serves J-01 barge-in (shorter spoken units = cleaner
  interrupts) and J-13.

### A3 — Kokoro ack voice via kokoro-onnx (P1→P2, effort M)
- `KokoroOnnxEngine`: `kokoro-onnx` + `kokoro-v1.0.onnx` (int8) + voice
  files from HF (`onnx-community/Kokoro-82M-v1.0-ONNX`), 24 kHz. Default
  voices `bm_george`/`bm_lewis` per `02_voice_stack.md` §167.
- Routing rule (start simple): acks/status/interjections ≤ N sentences →
  Kokoro; long-form answers → Piper (or Kokoro if RTF acceptable); Live
  session remains flagship when connected. Extend later with the
  complexity-routing policy already in Phase A.
- Verify actual `stream()` support in the installed kokoro-onnx version
  (PyPI serves 0.4.7 on py-3.14; upstream announced 0.6.x — pin what works).

### A4 — Pronunciation lexicon (P2, effort S)
- One config file (e.g. `.ultron/pronounce.json`): term → phonemes.
  Piper backend wraps terms in `[[…]]` blocks (piper1-gpl voice.py:263-281);
  Kokoro backend seeds `golds` (space app.py:18-19). Preload with
  "ULTRON", "JARVIS", user names, common tech terms.

### A5 — Voice manager (P2, effort S)
- Port `download_voices.py:11` URL pattern + `voices.json` catalog into a
  `kernel/voice/voice_manager.py`: list/download voices into
  `.ultron/voices/`, sha check (improve on their size-only check,
  `download_voices.py:123-132`).

### A6 — Synthesis telemetry + prosody knobs (P3, effort S)
- Record RTF + first-chunk latency per utterance (piper.cpp:394-406
  pattern) into the existing observability/audit stack.
- Expose `length_scale`/`noise_scale`/`volume`/`normalize` in config;
  optional `phoneme_silence` pause map for Piper (piper.cpp:508-532).

### A7 — Alignments → word timestamps (P3, effort M)
- Piper: `include_alignments=True` (voice.py:170-200, 382-431); Kokoro:
  `duration` ONNX output (export.py:26) / token start_ts. Feed the
  dashboard a "speaking word X" stream → HUD highlight/lip-sync (J-20).

### A8 — Cartesia cloud adapter (parked)
- Only if a premium tier is ever wanted: `CartesiaTtsEngine` behind the
  same seam; adopt product ideas (in-band `[laughter]` tags, pronunciation
  dict, latency SLOs) regardless.

### Anti-adopt / Kill-List notes
- **One** TTS seam, **N** adapters — never a second playback path
  (Kill List #2); `_play_audio` remains the only consumer.
- No system espeak install ever: piper bundles espeak-ng-data in-wheel;
  kokoro side uses `espeakng-loader` wheel. Kill any doc instructing an MSI.
- No model training (user directive) — piper_train is reference-only.
- License: kokoro-onnx MIT + Apache weights = default engine for anything
  potentially shared; piper-tts GPL-3.0 = local-use only, optional extra.
- Don't adopt the piper C++ playback (ffplay subprocess) — we have
  sounddevice + barge-in already.

---

## 7. Verification evidence (2026-09-13)

- Clones: `git log -1` → piper1-gpl `404aefe` (2026-09-09), piper `73c04d8`
  (2025-08-26), kokoro `dfb907a` (2025-08-06), Kokoro-TTS space `555d90e`
  (2025-04-09) — all at `C:/Users/ceoha/repos/tts-research/`.
- Files read in full this session: piper1-gpl `voice.py` (564), `config.py`
  (151), `__main__.py` (258), `download_voices.py` (138), `phoneme_ids.py`
  (204), `phonemize_espeak.py` (97), `setup.py`, `audio_playback.py`,
  `http_server.py` (API surface); rhasspy/piper `piper.cpp` (636),
  `infer_onnx_streaming.py` (295); kokoro `README.md`, `examples/export.py`
  (149), `examples/phoneme_example.py` (61), `kokoro.js/src/splitter.js`
  (345), `kokoro.js/src/kokoro.js` (stream API), space `app.py` (202);
  ULTRON `app/audio.py`, `app/voice_stack.py`, `kernel/voice/engines.py`
  (grep-level), `main.py` speak path, feature catalog J-IDs.
- py-3.14 compat: `py -3.14 -m pip install --dry-run piper-tts kokoro-onnx`
  → "Would install … piper-tts-1.8.0 … kokoro-onnx-0.4.7 espeakng-loader-0.2.4
  phonemizer-fork-3.3.1 …" (no build errors). `py -3.14 -c "import
  onnxruntime"` → onnxruntime 1.30.0. Python 3.14.7.
- Cartesia: product page read via web fetch (SSM, sub-90 ms, 10 s cloning,
  inline tags, 44 langs, on-prem). Docs behind signup — API details not read;
  does not block anything (parked).

## Residual risks
- kokoro-onnx PyPI lag (0.4.7 vs upstream 0.6.x claims) — pin at impl time,
  test `stream()` presence.
- Sample-rate mixing (22.05/24 kHz) in the shared playback path — the one
  real design decision in A1; parameterize per-stream (preferred) or
  resample.
- GPL: keep piper as optional extra; if ULTRON is ever distributed beyond
  personal use, default to the kokoro-onnx (MIT) backend.
- espeak clause-splitting quality for Hinglish text — needs a live listen
  test; Kokoro `h` (Hindi) lang_code exists for J-13 but untested here.
