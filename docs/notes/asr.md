# ASR notes (app/asr.py)

Model: `mlx-community/VibeVoice-ASR-Streaming-7B-4bit` via `mlx-audio` 0.5.9 (`mlx_audio.stt.models.vibevoice_asr`, which implements the
streaming protocol natively: `init_streaming_state` / `encode_speech` / `streaming_generate_step`). The reference harness repo
(xocialize/mlxengine-audio-tools) was not cloneable (GitHub asked for auth); the model card points to it and mlx-audio carries the same protocol.

## Geometry (from preprocessor_config.json)

24 kHz, 3200 samples/frame. Chunk 22 frames = 70 400 samples = 2.933 s; window = chunk + 4 lookahead frames = 83 200 samples (3.467 s).
Each window is encoded independently (cold conv state); the language-model KV cache is carried across chunks. Generation stops at
`<|text_chunk_end|>` (bundled tokenizer), which is then appended to the context.

## Measured (M5 Pro 48 GB, 2026-10-15, bench streams 100 ms blocks unpaced)

| file | audio | compute | RTF | chunk p50 / p95 | peak MLX mem | speakers found / truth |
|---|---|---|---|---|---|---|
| AMI ES2002a min 2-5 (`data/samples/ami_es2002a_3min.wav`) | 180.0 s | 21.85 s | **0.121** | 345 / 572 ms | 6.10 GB | 3 / 4 (ids 0,1,2) |
| meeting5 synthetic, hotwords = 5 names + jargon | 186.4 s | 26.02 s | **0.140** | 403 / 489 ms | 6.13 GB | 4 / 5 (ids 0-3) |

Model load: ~1.5 s from a warm HF cache. Latency: a chunk's text is available ~3.47 s after its first sample (window) plus ~0.35-0.57 s
compute, so ~3.8-4.0 s for words at the start of a chunk. Finalized segments wait for a speaker change, a silent chunk, a sentence end once the segment
is >= 6 s, or 25 s, so a segment typically lands 4-10 s after its first word.

## Speaker ids

- The model writes `\n Speaker N:` inline; ids are small ints starting at 0 in order of first appearance and stay stable for the whole stream
  (meeting5: Anna=S0, Ben=S1, David=S2, Emma=S3 consistently over 3 min).
- Failure mode: similar voices merge. meeting5 Chloe (synthetic TTS) was labeled S0 together with Anna throughout; in AMI the 4th speaker
  (short turns at ~2:14) was folded into S2. Manual rename/merge in the UI is the fallback.
- `reset()` re-prefills the prompt: ids restart from 0 for each meeting. `set_context()` also re-prefills (call it right after reset).

## Text / timing quirks

- Times are on the cumulative audio clock. There are no word timestamps: a marker's time is interpolated by its character position inside
  the chunk text across the 2.93 s chunk span, so starts are accurate to about +/-1 s.
- Words can straddle chunks ("Designing a new " | "remote control"); text is concatenated across chunks inside an open segment, so this is invisible.
- Hotwords go into the prompt as `... with these keys: speaker, content and extra info: Anna, Ben, ...`. Names and "Kubernetes"/"OKRs" were
  spelled correctly in meeting5.
- Numbers come out spelled ("three hundred and thirty meters", "nineteen ninety-one").
- Encoder features are float32; they are cast to the embedding dtype (bf16) before entering the LM so the KV cache stays bf16 (the library's
  streaming step does not cast).

## Memory bound (multi-hour)

- About 15 LM tokens per second of audio (26 audio frames + 2 markers + ~10-30 text tokens + delimiter per chunk).
- The KV cache is an mlx `RotatingKVCache(max_size=8192)` with `keep` = prompt length, so system prompt + hotwords stay pinned and the oldest
  ~9 min of audio/text roll off (~0.5 GB bf16). The library's helper pins only 4 tokens; we override `keep`.
- RoPE positions keep growing; at absolute offset 120 000 (~2.2 h) the prompt is re-prefilled (open segment closed first), well before the
  131 072 trained max. Speaker ids may restart then.
- Pending audio buffer never exceeds one window + one feed block. `chunk_ms` history is a bounded deque.
- Speakers silent for longer than the rolling window (~9 min) may come back with a new id. Not yet measured on a long recording.
