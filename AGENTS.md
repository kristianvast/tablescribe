# tablescribe

A local, open-source meeting assistant for an in-room group of five who speak English. It transcribes live with speaker labels, keeps running notes for each person, fact-checks claims, and writes minutes at the end. It runs on a single Mac (M5 Pro, 48 GB). The meeting is shown on a web page on a shared screen.

## Architecture

```
mic (center of table) -> streaming ASR (who said what) -> transcript store
transcript -> local LLM every ~60 s -> per-person notes, decisions, action items, fact-checks
fact-checks -> Brave Search API (queries only; audio and transcript never leave the machine)
web page (FastAPI + WebSocket) <- live transcript, notes, fact-check feed
stop -> minutes (Markdown) + recording saved to disk
```

## Decisions (settled - do not revisit without asking)

- **ASR + diarization:** `mlx-community/VibeVoice-ASR-Streaming-7B-4bit` (MLX, int4, ~6 GB). One model labels speakers and transcribes; there is no separate diarization stage. Do not use bf16: it measured the same accuracy, is about 6x slower, and likely cannot keep up live on an M5 Pro.
  - Streaming geometry is read from `preprocessor_config.json`: 22-frame chunks (~2.93 s) plus 4-frame lookahead (~0.53 s), at 24 kHz, giving about 3 s of latency.
  - Use the bundled tokenizer; each chunk ends on `<|text_chunk_end|>`.
  - Pass participant names and jargon as hotwords through the context prefix.
  - Reference harness: https://github.com/xocialize/mlxengine-audio-tools
- **Speaker -> name mapping:** a roll call at the start, where each person says "I'm <name>"; map the model's speaker IDs to those names.
- **LLM:** local via Ollama. The model has not been chosen yet; pick the newest strong tool-calling model that fits next to the ASR (leave about 10 GB of headroom).
- **Lookups:** Brave Search API. Read the key from env `BRAVE_API_KEY` (set it in the gitignored `.env`) and never commit it.
- **Stack:** Python 3.12 managed with `uv`, FastAPI, a plain HTML/JS front end, `ffmpeg` for audio I/O.
- No cloud ASR or LLM APIs and no NVIDIA/CUDA paths. Everything except search queries runs offline.

## Build order

1. Spike: run the 4-bit model on a multi-speaker sample file; measure RTF on this Mac and check the speaker labels.
2. Live mic -> 3 s chunks -> model -> transcript on the web page.
3. Roll call and name mapping.
4. LLM loop: per-person notes, decisions, action items, fact-checks with Brave.
5. Stop -> minutes Markdown + recording.

## Conventions

- Keep it small: one server process and one page. No frameworks beyond FastAPI.
- Large artifacts stay out of git: models go in the HF cache, recordings and outputs in `data/` (gitignored).
- Verify with real audio, not mocks. Record measured RTF and latency in `NOTES.md` when they change.
