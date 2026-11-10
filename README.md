# Tablescribe

A local meeting assistant for an in-room group, running on one Mac. It transcribes the room live with speaker names, keeps notes per person, tracks decisions and action items, fact-checks claims, and writes minutes when you stop. Everything runs on the Mac except Brave Search queries used for fact-checks.

## Start

```
./meeting
```

You can also double-click `Tablescribe.command` in Finder. Either way, the page opens at http://127.0.0.1:8000. Other people on the same Wi-Fi can open the LAN address printed in the terminal to read and copy, but only this Mac can start or stop the meeting or change anything.

Options:

```
./meeting --source mic:<device name or index>   # pick a microphone (default: the macOS default input)
./meeting --source file:data/samples/meeting5.wav  # replay a recording in real time (demo / testing)
./meeting --port 8001 --no-browser
```

The first launch may trigger a macOS microphone permission prompt for your terminal; allow it.

## Running a meeting

1. **Setup**: enter a title, the participants, any jargon (hotwords help the transcription), an agenda, and a minutes template.
2. **Roll call**: go around the table and have each person say "I'm <name>". Names attach to voices automatically, and you can click a speaker to rename at any time.
3. **Live**: the transcript, agenda timer, actions, decisions, per-person notes, and fact-checks update during the meeting. Notes refresh about every 60 s.
   - `B` bookmarks a moment, `N` focuses your notes, `/` asks a question about the meeting, `J` jumps back to live.
   - Click any transcript line to replay that audio.
   - Every card has a copy button that copies rich text and Markdown.
4. **Stop**: the app writes minutes in the chosen template, including your typed notes and bookmarks, and saves the recording. You can switch templates, copy, or download.
5. **History**: search past meetings, reopen them, or ask questions across all of them.

Files are saved to `data/meetings/<id>/`: `minutes.md`, `audio.wav`, `transcript.jsonl`, `notes.json`, `factchecks.json`, and `meeting.json`.

## Requirements

- Apple Silicon Mac. It is built for an M5 Pro with 48 GB.
- `brew install uv ffmpeg ollama`
- Ollama model: `ollama pull nemotron-3.5-lightning`. To use another model, set `MC_LLM_MODEL=<model>`.
- Speech model: `mlx-community/VibeVoice-ASR-Streaming-7B-4bit` downloads into the Hugging Face cache on first run.
- Fact-checks need a `BRAVE_API_KEY` in `.env`. The file is gitignored.

## Development

```
uv run pytest
uv run basedpyright
uv run python -m app.asr --bench data/samples/ami_es2002a_3min.wav
```

Measured performance is in `NOTES.md`, and the module contract is in `docs/CONTRACT.md`.

## License

MIT. See `LICENSE`.
