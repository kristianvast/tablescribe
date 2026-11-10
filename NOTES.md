# NOTES

Measured numbers and decisions. Details per module are in `docs/notes/`.

## ASR: VibeVoice-ASR-Streaming-7B-4bit (M5 Pro, 48 GB, 2026-10-15)

| file | audio | RTF | chunk p50 / p95 | peak mem | speakers found / truth |
|---|---|---|---|---|---|
| AMI ES2002a min 2-5 (real speech) | 180 s | **0.119-0.121** | 332-345 / 564-572 ms | 6.1 GB | 3 / 4 |
| meeting5 (5 synthetic `say` voices + hotwords) | 186 s | **0.140** | 403 / 489 ms | 6.1 GB | 4 / 5 |

- Command: `uv run python -m app.asr --bench data/samples/ami_es2002a_3min.wav`
- Latency: text for the start of a chunk is ready about 3.8-4.0 s after it is spoken (3.47 s window + ~0.4 s compute). A finalized transcript line lands 4-10 s after its first word.
- Similar voices can share one speaker id (meeting5: Anna and Chloe). Roll call keeps the first name per id; rename from the page.
- Memory stays bounded by a rotating KV cache of about 9 minutes; the prompt is re-anchored at about 2.2 h. This is not yet measured on a long meeting.

## LLM

- Model: `nemotron-3.5-lightning` (Ollama, 30B MoE with about 3B active per token, 25 GB). Override with `MC_LLM_MODEL`.
- Chosen over `qwen3.8:27b`: dense 27B is several times slower per token beside the ASR, and the network here is slow.
- Speed and quality: see `docs/notes/llm.md` (pending measurement).

## Audio input

- avfoundation index 0 on this Mac can be an iPhone Continuity mic that yields no audio. `mic` follows the macOS default input instead.
