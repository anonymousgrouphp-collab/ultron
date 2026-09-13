# 11 — STT Deep-Dive: RealtimeSTT · AssemblyAI SDK · Deepgram SDK

*Written 2026-09-13 after the STT research sweep. Cloned to
`C:/Users/ceoha/repos/stt-research/` — RealtimeSTT @ 07df360 (2026-08-30),
assemblyai-python-sdk @ 8fd216b (2026-09-11), deepgram-python-sdk @ fbd6a2a
(2026-09-03). Every repo read line-by-line on the load-bearing paths (file/line
counts in §7). Sources verified Sep 2026. Complements `02_voice_stack.md`
(faster-whisper/RealtimeSTT was already the chosen local path — this doc makes
it concrete) and report 10 (TTS seam).*

## 0. Executive verdict

**ULTRON has NO local STT and no STT seam.** The only speech-to-text today is
Gemini Live's server-side `input_transcription` (`app/audio.py:127`); when Live
is down (no key, network drop, quota) the assistant loses its ears entirely —
the Ollama/local path is text-only. This is the exact mirror image of the TTS
gap report 10 closed (local speak fallback now exists via `kernel/voice/tts.py`).

- **RealtimeSTT (KoljaB)** is the adoption goldmine: a mature 20k-line
  real-time capture pipeline (VAD cascade → pre-roll → speculative
  finalization → flicker-free partials). Two of its modules are **zero-dependency
  ports** (text stabilizer, acoustic boundary detector); its engine seam
  (`BaseTranscriptionEngine` + `StreamingTranscriptionSession`) is the exact
  shape to mirror for ULTRON's `SttEngine`; faster-whisper itself installs clean
  on py-3.14 (verified §7). Do **not** take the package wholesale — it drags
  torch/torchaudio/webrtcvad/PyAudio; ULTRON already owns the mic loop
  (`app/audio.py::_listen_audio`) and the VAD contracts (`kernel/voice/engines.py`).
- **AssemblyAI SDK (v1.5.2)**: streaming **v3** is a turn-based voice-agent
  protocol (`Turn` events with `end_of_turn_confidence` + per-word
  `word_is_final`, mid-session `UpdateConfiguration`, `keyterms_prompt`,
  `agent_context`, `SpeechStarted`, `Heartbeat` with `realtime_factor`,
  browser-safe temporary tokens). Plus the new **Dictation API** (upload →
  transcript + optional server-side LLM rewrite). Read as protocol design
  reference for a cloud-STT adapter — not as a primary dependency today.
- **Deepgram SDK (v7.8.1)**: `listen` v1 (nova-3, utterance-end events) and
  **v2 turn-based** (`TurnInfo`: `StartOfTurn` / `EagerEndOfTurn` /
  `TurnResumed` / `EndOfTurn` with `end_of_turn_confidence` and a `trigger`
  enum). The **voice_agent / agent** namespaces (server-side LLM brain) are
  anti-adopt (Kill List #2 — second brain).

Both cloud providers converge on the same turn-based protocol shape — that
convergence is the design signal for ULTRON's future cloud-STT adapter.

## 1. RealtimeSTT — the real-time capture pipeline (20,240 lines)

### Architecture (files actually read)

- `audio_recorder.py` (943L) — the `AudioToTextRecorder` public facade:
  ~110 constructor knobs, defaults (post-speech silence 0.6 s, pre-roll 1.0 s,
  deactivity confirmation 0.16 s), lifecycle methods (`listen/wait_audio/text/
  feed_audio/wakeup/abort/shutdown`), context-manager shutdown. Thin facade —
  all logic lives in `core/`.
- `core/recording.py` (587L) — the recording state machine. Consumes
  `audio_queue` (with a drain-marker ordered after manual audio, `allowed_latency_limit`
  overflow discard); wake-word → VAD → `start()` with pre-roll frames injected
  under the frames lock; **wake-word audio removal** (`wakeword_samples_to_remove`
  pops/trims leading frames so "ultron what time…" never transcribes "ultron");
  speech-end gate = `min_length_of_recording` + silence-candidate confirmation
  (`deactivity_silence_confirmation_duration` 0.16 s prevents breath/trailing-syllable
  false stops) + `post_speech_silence_duration` stop threshold.
- **Early transcription on silence** (`recording.py:424-452`): after
  `early_transcription_on_silence` ms of trailing silence it submits the final
  transcription BEFORE the stop threshold fires; if speech resumes, the request
  is discarded. Speculative finalization — cuts ~300-500 ms of perceived
  latency by overlapping decode time with the silence window.
- `core/voice_activity.py` (409L) — **two-stage VAD cascade**: cheap WebRTC VAD
  (10 ms frames) runs inline; on first speech frame it spawns the expensive
  Silero check on a thread (non-blocking mic path) with a **generation counter**
  (`_silero_vad_generation`) so stale checks from a previous recording never
  mutate current state; `is_voice_active()` = WebRTC recent (≤1 s) AND Silero
  active. scipy `resample_poly` handles non-16k input devices.
- `core/silero_vad.py` (591L) — Silero backend matrix: `auto` prefers **raw
  onnxruntime** (`silero_vad_op18_ifless.onnx`) → plain `silero_vad.onnx` →
  PyTorch CPU. The raw-ONNX path means VAD needs **no torch at all** (CUDA is
  deliberately not automatic — launch overhead loses for 32 ms single-stream chunks).
- `core/preroll.py` (434L) — conservative pre-roll trim
  (`select_preroll_frames`): uses VAD metadata captured while audio flowed
  (never a second VAD pass), adaptive noise floor (lowest-20% RMS × 2.5 +
  margin), guard-ms before speech onset, min-included-ms, merges VAD false
  gaps; five explicit fallback reasons — anything uncertain → keep full
  pre-roll. Solves "first words cut off" without feeding junk audio to whisper.
- `core/transcription.py` (435L) — final-transcription worker in a **separate
  PROCESS** (`torch.multiprocessing`, spawn on Windows) behind a pipe; engine
  created from the registry; WAV warmup before ready; engine-close-on-shutdown
  thread so native inference cancels promptly; `SharedFinalModelExecutor` lets
  Preview reuse the loaded Final model via request-id-isolated responses.
- `core/tail_transcription.py` (593L) — rolling 3-s PCM tail + the
  **Live↔Final tail merge**: tokenize live hypothesis + final-tail, find the
  live suffix as an anchor in the final tail (rightmost exact 3-4-word anchor;
  constrained fuzzy = one edit class only — inserted/deleted token, 1-char
  spelling diff on ≥4-char words, or a Final word *completing* a truncated
  Live word — arbitrary substitutions rejected); the merged text keeps the
  live prefix and adopts only the final suffix. Deliberately conservative:
  untrusted tails fall back to the Live snapshot.
- `core/preview_transcription.py` (322L) — speculative Preview lane: at VAD
  silence, queue (max 2, drop-not-block) a tail-only transcription; worker
  never touches Final; result = merged preview with status
  (exact/fuzzy/alignment_failed/tail_only) for an **early LLM response**.
- `core/realtime.py` (1884L) — realtime worker. Per pass: snapshot frames
  under lock → int16→float32 → transcribe (streaming session lane if the
  engine supports it, else whole-buffer re-transcribe on the small realtime
  model) → publish through the stabilizer. Trigger modes: fixed pause timer,
  or **syllable-boundary scheduling** (energy-valley detection + follow-up
  passes at +0.05 s/+0.2 s, timer fallback). **Punctuation split** (opt-in
  `realtime_punctuation_split_marks="sentence"`): when stable text shows a
  confirmed sentence boundary, re-transcribe with word timestamps on the main
  model, then atomically split the frame buffer at the split sample under the
  realtime lock — long dictation becomes a stream of finalized utterances with
  mid-stream `recording_id` advance. Every failure path is non-fatal.
- `core/realtime_text_stabilizer.py` (1136L) — **zero-dependency** flicker
  killer. Each realtime pass is an `observation` (normalized via NFKC +
  casefold + punctuation-stripped projection mapped back to raw offsets);
  per-`(offset, char)` evidence accumulates (confirmations + time-span +
  audio-progress gating); chars stabilize at ≥2 confirmations/≥0.6 s span,
  spaces need ≥4 + stable right context, punctuation needs ≥4; outlier
  observations (similarity < 0.35 vs last 5) are quarantined and branch-adopted
  only if the branch repeats; emits `stable_text`/`stable_delta`/`unstable_text`/
  `display_text` events. Only stdlib (difflib, unicodedata, dataclasses).
- `core/realtime_boundary_detector.py` (559L) — **zero-dependency** (numpy)
  acoustic valley detector: 10 ms frames, rolling noise-floor (dB, dual-rate
  EMA), RMS + zero-crossing + autocorrelation voicing score → vowel-like
  frames; confirmed local energy valleys after ≥70 ms of voiced audio with 30 ms
  lookahead → boundary events with score/reason/latency. This is what makes
  partials update at natural pauses instead of a fixed timer.
- `core/realtime_merge.py` (821L) — `StickyRealtimeTranscriptionMerger`: slow
  (authoritative) stream + optional ultrafast stream that may only append a
  bounded (≤5-word) suffix anchored on slow text; accepted suffixes are sticky
  until the slow hypothesis changes; fuzzy candidates need repeat confirmation.
  Deterministic, no recorder coupling.
- `core/wakeword.py` (223L) — Porcupine + openwakeword backends; OWW predict
  loop takes max score ≥ sensitivity across models.
- `core/initialization.py` (958L) — spawn config, worker/thread bring-up,
  model loading via the engine registry.
- `transcription_engines/` — `base.py` (172L) defines `BaseTranscriptionEngine`
  (sync `transcribe()`, `supports_streaming`, `warmup()`) +
  `StreamingTranscriptionSession` (`reset/accept_audio/decode/get_result/finish/
  close`) + `TranscriptionResult/Info/Config` + typed errors; 12 adapters incl.
  `faster_whisper_engine.py` (114L — WhisperModel or BatchedInferencePipeline,
  word-timestamp metadata), whisper.cpp, kroko-ONNX, sherpa-ONNX, parakeet,
  qwen3-asr, nemotron, omnilingual, funasr, HF transformers. Same seam shape as
  ULTRON's `TtsEngine` (report 10 A1).

### Fit for ULTRON

ULTRON already owns: mic capture (sounddevice 16 kHz int16 512-blocks),
EchoGate duck/barge-in, wake + VAD + speaker-ID contracts with lazy loaders.
What it lacks — RealtimeSTT has production-grade answers for all four:

1. A local transcription engine + seam (ears for the offline path).
2. Flicker-free partials (its stabilizer is a drop-in port).
3. Latency discipline (early finalize, tail/preview, pre-roll trim).
4. The recording state machine's stop-timing hygiene (confirmation windows,
   min-length, wake-word removal).

## 2. AssemblyAI SDK v1.5.2 — streaming v3 + dictation (6,782 lines)

- `streaming/v3/client.py` (432L) — `RealTimeTranscriber`: dedicated read +
  write threads over a queue; transient handshake failures retried
  (`max_connection_retries`), HTTP-level rejections (auth/quota) fail fast to
  `on_error`; graceful `disconnect(terminate=True)` sends `TerminateSession`
  and waits for the server `TerminationEvent` (reports total audio duration);
  single-slot close-error handoff so ALL error dispatch happens on the read
  thread (no cross-thread dedup races — documented inline); `create_temporary_token`
  (`/v3/token`, expiry + max session duration) for browser/dashboard clients.
- `streaming/v3/models.py` (453L) — the protocol: `Turn` event carries
  `turn_order`, `turn_is_formatted`, `end_of_turn`, `end_of_turn_confidence`,
  per-word `Word(text, start, end, confidence, word_is_final, speaker)`;
  `SpeechStarted`; `Heartbeat(total_audio_received_ms, realtime_factor,
  max_speech_probability)`; `SpeakerRevision` (offline reclustering fix-ups
  matched by `turn_order`); session params updatable MID-STREAM via
  `UpdateConfiguration` (`min_turn_silence`, `max_turn_silence`,
  `vad_threshold`, `format_turns`, `keyterms_prompt`, `filter_profanity`,
  `prompt`, `agent_context`, `interruption_delay`); opus/ogg_opus/aac are
  self-describing encodings (sample_rate omittable); streaming modes
  min_latency/balanced/max_accuracy; PII redaction policies; voice_focus
  noise-suppression model. `LLMGateway` = server-side LLM pass on turns
  (anti-adopt for ULTRON).
- `dictation/v1/models.py` (204L) — `DictationConfig` (`extra="forbid"` —
  typo-proof config; `stt_prompt` situational context vs `llm_instruction`
  post-pass; keyterms normalized + length-capped) → `DictationResponse`
  (`final_text` property prefers the LLM rewrite, falls back to raw;
  `request_time_ms`/`sync_time_ms` split timing). Upload-based dictation with
  typed error codes + `Retry-After` handling.
- `prerecorded/v2/transcript.py` (448L) — the async polling transcript
  lifecycle (queued → processing → completed/error), feature assembly
  (diarization, chapters, entities, sentiments, auto-highlight, IAB topics).
  Reference for any future "transcribe this file/recording" tool.

### Fit for ULTRON

Not a dependency today (Gemini Live remains the flagship ears; cloud STT would
duplicate cost + keys). The value is **protocol shape**: if ULTRON ever grows a
cloud-STT adapter behind the S1 seam, model it on Turn events
(`end_of_turn_confidence` + `word_is_final` + mid-session config updates +
heartbeat telemetry with `realtime_factor`). The temporary-token pattern is
also the right way to let the dashboard (J-20 HUD) open its own mic lane
without embedding the master API key.

## 3. Deepgram SDK v7.8.1 — listen v1/v2, voice_agent (14,977 lines SDK)

- `listen/v1` — `client.listen.v1.connect(model="nova-3")` context-managed WS:
  `send_media(chunk)` + `start_listening` thread, typed events
  (`ListenV1Results`/`Metadata`/`SpeechStarted`/`UtteranceEnd`), sync + async
  clients, Fern-generated types (extra="allow" compat models).
- `listen/v2/types/listen_v2turn_info.py` (102L) — the v2 turn protocol:
  `Update` / `StartOfTurn` / **`EagerEndOfTurn`** ("opportunity to begin
  preparing an agent reply") / **`TurnResumed`** (retract an eager end when
  speech continues) / `EndOfTurn` with `end_of_turn_confidence` and a `trigger`
  enum (`model` | `manual` ForceEndTurn | `timeout` `eot_timeout_ms`), open-enum
  tolerant. `examples/16-transcription-force-end-turn.py` shows manual turn
  forcing; `17-transcription-live-reconnect.py` the reconnect flow.
- `agent/` + `voice_agent/` — server-side Listen+Think+Speak configurations.
  **Anti-adopt**: that's a second brain in someone else's cloud (Kill List #2);
  ULTRON's kernel loop is the brain.
- SDK mechanics worth copying: context-managed WS connections, keepalive
  threads, `wiremock/` fixture-based contract tests in CI.

### Fit for ULTRON

Same as AssemblyAI: protocol reference + the **EagerEndOfTurn/TurnResumed
pair** is the sharpest available formalization of "start thinking, but be ready
to keep listening" — worth encoding in the S1 seam's callback contract even for
the local path (preview vs final, S4).

## 4. ULTRON integration analysis (the gap this closes)

Current state of the ears (all verified in-tree):

- `app/audio.py::_listen_audio` — sounddevice 16 kHz int16 512-block capture,
  EchoGate via `gate_mic_frame()`, frames pushed to the Live session
  (`out_queue → session.send_realtime_input`). STT is 100% Gemini-side
  (`sc.input_transcription` at `app/audio.py:127`).
- `kernel/voice/` — `WakeWordEngine`/`VadEngine`/`SpeakerIdEngine` Protocols +
  lazy loaders (`load_silero_vad` ONNX 512-chunks already ported!), `EchoGate`,
  `TtsEngine` seam (report 10 A1-A6 merged). Config-flag-gated with
  `EngineUnavailable` degradation — the established extension pattern.
- `app/voice_stack.py` — arm/disarm hooks; `speak_local` TTS worker.
- Partial-text handling in `_receive_audio` (`app/audio.py:122-125`) is naive:
  append if `txt != out_buf[-1]` — Live partials flicker and there is no
  consensus stabilization.
- `requirements-voice.txt` exists as the optional-extra pattern for heavy deps.

So the STT story mirrors the TTS story exactly: **one seam, N adapters, lazy
loads, config-gated, zero behavior change until enabled.**

## 5. ADOPT LIST (prioritized, concrete)

### S1 — `SttEngine` seam + faster-whisper backend: offline voice loop (P1, effort M)

`kernel/voice/stt.py` mirroring `kernel/voice/tts.py`: `SttEngine` Protocol —
`transcribe(pcm16_int16: np.ndarray, sample_rate: int) -> SttResult` and, for
the streaming engines, a `StreamingSttSession` (mirror RealtimeSTT's
`accept_audio/decode/get_result/finish/close` contract from
`transcription_engines/base.py:67-103`). First adapter: faster-whisper
(`faster_whisper_engine.py` is a 114-line reference: WhisperModel int8, batch
pipeline, `vad_filter=True`, word-timestamp metadata). Wire a
`local_stt_enabled` config path: when the Gemini Live session fails to start or
`stt_prefer_local=true`, `_listen_audio` frames route through VAD (existing
`load_silero_vad`) → S1 engine → `_maybe_route_agent`/gateway → existing
`speak_local` TTS. **Result: the Ollama/offline path gains full voice
in/out** — today it has neither ears nor use for them. Deps: `faster-whisper`
(+ctranslate2) — py-3.14 verified (§7); goes in `requirements-voice.txt`, never
the default install (P1-D optional-accelerator doctrine).

### S2 — Port `RealtimeTextStabilizer` (P1, effort S, zero-dep)

`kernel/voice/stt_stabilizer.py` — near-verbatim port of
`realtime_text_stabilizer.py` (1136L, stdlib-only). Consumes partial
observations (from Live's `output_transcription`/`input_transcription` OR the
S1 local engine), emits `stable_text`/`display_text`. Fix for the naive
`app/audio.py:122-125` dedup — flicker-free HUD captions and, critically,
**stable text for `_maybe_route_agent`** so routing decisions stop firing on
gibberish partials. Config thresholds already sensible (2 chars/0.6 s; spaces
4 confirmations). Unit-testable with synthetic observation sequences.

### S3 — Port `RealtimeSpeechBoundaryDetector` (P1, effort S, numpy-only)

`kernel/voice/boundary.py` — port of `realtime_boundary_detector.py` (559L).
Vowel/energy-valley detection for: (a) partial-publish cadence (update HUD at
natural pauses, not a fixed timer), (b) a cheap pre-barge-in signal —
`EchoGate` currently re-opens on loud speech; boundary events distinguish
speech onsets from thumps. Runs per-512-block on the mic thread — the
reference implementation is O(frame) with bounded history (~900 ms).

### S4 — Speculative finalization + Preview lane (P2, effort M)

Adopt the `early_transcription_on_silence` pattern (`recording.py:424-452`)
and the Preview design (`preview_transcription.py` + `tail_transcription.py`
merge): on the local path, when trailing silence hits N ms, submit the final
decode while still listening; at confirmed silence, keep only a 3-s tail,
transcribe it on the small realtime model, and **anchor-merge** it into the
live text (exact 3-4-word anchors, constrained one-edit fuzzy only —
`merge_live_and_tail_transcription` is the reference). The
`EagerEndOfTurn`/`TurnResumed` semantics map 1:1 onto "preview published →
final reconciled or live-text fallback". This is the difference between a
local loop that feels instant and one that feels like dictation software.

### S5 — Pre-roll trim with adaptive noise floor (P2, effort S)

Port `select_preroll_frames` (`preroll.py`, 434L) into the S1 capture path:
keep VAD metadata per buffered frame (EchoGate already buffers frames during
TTS — same shape), trim clear dead air, keep `guard_ms` + `min_included_ms`,
fall back to full pre-roll on anything uncertain. Fixes clipped first words
without re-running VAD.

### S6 — VAD cascade + stop-timing hygiene (P2, effort S)

Two upgrades inside the existing `VadEngine` contract (no second
implementation): (1) cheap inline WebRTC VAD (`webrtcvad-wheels` — cp314 wheel
verified) wakes the threaded Silero confirmation with a generation counter
(`voice_activity.py:45-71`), (2) silence-confirmation window
(`deactivity_silence_confirmation_duration` 0.16 s) + `min_length_of_recording`
before accepting end-of-speech. Both are local-path behaviors; they also give
EchoGate's barge-in a lower false-positive floor.

### S7 — Cloud-STT adapter contract (P3, parked until needed)

If/when a cloud STT fallback is wanted (Live quota/network outages with cloud
budget available), define ONE `SttStreamProvider` shape from the convergence:
Turn events with `end_of_turn_confidence` + per-word `word_is_final`
(AssemblyAI `Turn` ≈ Deepgram `TurnInfo`), `StartOfTurn`/`SpeechStarted`,
mid-session config update, `ForceEndpoint`/`ForceEndTurn` manual control,
heartbeat with `realtime_factor`, temporary-token auth for HUD clients,
`keyterms_prompt` for ULTRON/command vocabulary biasing. Neither SDK is
adopted as a package now; the adapter would hand-roll ~300 lines against the
WS API of whichever provider is chosen (both protocols are small). Kill-List
note: provider model strings live only in that adapter's config block.

### S8 — Wake-word audio removal (P3, effort S)

Port the `wakeword_samples_to_remove` mechanism (`recording.py:354-372`) into
the openwakeword cutover path (the parked P4 residual): after detection, strip
`wake_word_buffer_duration` seconds of audio so the transcribed utterance never
contains "ultron". Directly improves transcript hygiene and memory episode
capture quality (`record_episode` currently stores whatever Live heard).

### S9 — Word-timestamp metadata contract (P3, effort S)

Adopt `final_transcription_word_timestamps` → `metadata["words"]`
(`faster_whisper_engine.py:97-106`, `realtime.py:567-610`) as the S1 result
shape, and the AssemblyAI `Word(text, start, end, confidence, word_is_final)`
as the streaming shape. Consumers later: J-20 HUD caption timing, per-word
confidence gates for the memory write path (skip storing <0.5-confidence
rubble).

### S10 — Dictation-mode punctuation split (P3, effort M, behind S1)

Port the punctuation-split lane (`realtime.py:1241-1346`): stable sentence-end
punctuation → main-model re-decode with word timestamps → atomic frame-buffer
split at the split sample → new `recording_id`. Gives ULTRON a continuous
dictation lane (long multi-sentence thoughts become a stream of finalized
utterances the AgentLoop can start acting on) without re-recording machinery.

### Anti-adopt / Kill-List notes

- **RealtimeSTT as a package** — do NOT add. It hard-drags torch+torchaudio +
  webrtcvad + PyAudio and owns its own mic loop; ULTRON already owns capture
  (`app/audio.py`) and VAD contracts (`kernel/voice/engines.py`). Port the
  modules (S2/S3 zero-dep) and the patterns (S4/S5/S6/S8). Note: PyPI
  `realtimestt 1.0.4` resolved on py-3.14 (repo `setup.py:327` says
  `>=3.11,<3.13` — released metadata disagrees with the repo; do not rely on
  the package either way).
- **Deepgram `agent`/`voice_agent`** — server-side Think/Listen/Speak agent =
  second brain (Kill List #2). ULTRON's kernel loop stays the brain; cloud
  services are adapters.
- **AssemblyAI `LLMGateway`** (server-side LLM pass on turns) and Dictation's
  `llm_instruction` — same second-brain violation. The transcript lands in
  ULTRON; ULTRON's gateway does the thinking.
- **No second VAD/wake implementation** — cascade (S6) upgrades the existing
  `VadEngine` loaders; openwakeword stays behind the existing
  `WakeWordEngine` contract at cutover.
- **No new always-on audio processes** — RealtimeSTT's spawn-process isolation
  is a pattern to copy IF native crashes show up, not something to adopt
  preemptively; the threaded lane is fine for whisper int8 on CPU.

## 6. Priority order and phasing

1. **S2 + S3** (zero-dep ports, immediately testable, improve the LIVE path
   today — no new deps, no config flips).
2. **S1** (the seam + faster-whisper; the offline-voice-loop milestone),
   with **S5/S6** landing inside its capture path.
3. **S4** (latency discipline) — after S1 works end-to-end.
4. **S8/S9** with the openwakeword cutover; **S7/S10** parked until a user
   session demands cloud fallback / dictation.

All of it maps to ROADMAP Phase 4 voice work (J-01/J-02) and completes the
research/02 §2 decision ("faster-whisper/RealtimeSTT local") with the
TtsEngine-style seam discipline the codebase already standardized.

## 7. Verification evidence (2026-09-13)

- Clones (shallow, analysis-only): RealtimeSTT @ `07df360` (2026-08-30),
  assemblyai-python-sdk @ `8fd216b` (2026-09-11), deepgram-python-sdk @
  `fbd6a2a` (2026-09-03) — under `C:/Users/ceoha/repos/stt-research/`.
- Line counts read (RealtimeSTT): realtime.py 1884, realtime_text_stabilizer.py
  1136, audio_recorder.py 943, initialization.py 958 (architecture greps +
  spawn/start-method lines), tail_transcription.py 593, silero_vad.py 591
  (backend matrix), recording.py 587, realtime_boundary_detector.py 559,
  realtime_merge.py 821 (first 300 lines + contract), preroll.py 434,
  voice_activity.py 409, transcription.py 435, preview_transcription.py 322,
  recorder_config.py 245, wakeword.py 223, transcription_engines/base.py 172,
  faster_whisper_engine.py 114, engines/__init__.py 29, __init__.py 94.
  AssemblyAI: streaming/v3/client.py 432, models.py 453, dictation/v1/models.py
  204, README + wc survey of all 27 package files (6,782 total). Deepgram:
  listen/v2 turn-info 102, examples/13 live WS, wc survey of listen/agent
  namespaces (14,977 total), example index.
- **py-3.14 wheel verification** (system `py -3.14`, per user order):
  `pip install --dry-run --ignore-installed RealtimeSTT assemblyai
  deepgram-sdk faster-whisper` → exit 0, "Would install" includes
  `realtimestt-1.0.4 faster-whisper-1.2.1 ctranslate2-4.8.2 torch-2.14.0
  torchaudio-2.11.0 onnxruntime-1.30.0 webrtcvad-wheels-2.0.14 assemblyai-1.5.2
  deepgram-sdk-7.8.1 scipy-1.17.1 soundfile-0.13.1 PyAudio-0.2.14`. Focused
  re-run confirmed RealtimeSTT and faster-whisper individually resolvable
  (cp314 wheels present for ctranslate2/av/tokenizers).
- Residual risk on the `realtimestt` PyPI package's python_requires
  (repo `setup.py:327` `<3.13` vs PyPI resolution) recorded above — moot for
  the adopt list, which ports modules instead of the package.

## Residual risks

- **Model download**: faster-whisper pulls HF weights on first run (~75 MB
  tiny → ~500 MB small-int8); must be gated behind the config flag and a
  documented `download_root` (`.ultron/models`) like the voice_manager paths.
- **CPU latency**: whisper int8 tiny/base on a desktop CPU ≈ 0.3-1.0 s for a
  5-s utterance — S4's speculative finalize exists precisely to hide this;
  if the box has CUDA, `device="cuda"` config knob covers it (RealtimeSTT's
  gpu_device_index pattern).
- **Hinglish**: faster-whisper handles code-switched Hindi-English weakly
  (language="hi" degrades English tokens); Live stays flagship for
  Hinglish speech — the local path is the outage/offline fallback, and the
  report 10 pronunciation-lexicon idea applies to STT prompts too
  (`initial_prompt` biasing, S1 config).
- **EchoGate interaction**: the local loop must route mic frames through the
  same `gate_mic_frame()` seam — a local-STT path that bypasses EchoGate would
  reintroduce the self-hearing bug the gate exists to prevent.
- **Stabilizer tuning**: the 0.6-0.8 s evidence spans trade flicker for lag;
  for command routing (S2's routing consumer) thresholds may need a
  latency-biased profile — keep them in config, not constants.
