# tablescribe internal contract

Binding interface between modules. Each module has exactly one owner; anything not listed here is private to its module. Python 3.12, async where noted, run with `uv run`.

## Layout

```
app/__init__.py        (core)
app/__main__.py        (core)     `uv run python -m app [--source mic|mic:<device>|file:<path>] [--host 0.0.0.0] [--port 8000] [--no-browser]`
app/config.py          (core)     paths + settings (DATA_DIR = <repo>/data, OLLAMA_URL, LLM_MODEL, BRAVE_API_KEY from env)
app/server.py          (core)     FastAPI app, WS hub, REST routes, static web/
app/session.py         (core)     Meeting state machine, roll call, persistence, event bus
app/audio.py           (audio)    mic/file sources + WAV recorder
app/asr.py             (asr)      VibeVoice streaming wrapper + `python -m app.asr --bench`
app/llm.py             (llm)      Ollama client, notes loop, chat, minutes, templates
app/templates/*.md     (llm)      minutes templates: standup, decision-log, client, brainstorm
app/factcheck.py       (factcheck) Brave Search + claim verdicts
app/store.py           (store)    SQLite FTS5 history index
web/index.html, web/app.js, web/style.css, web/assets/*   (ui)
tests/test_<module>.py (owner of <module>)
docs/notes/<module>.md (owner)    measured numbers + decisions; merged into NOTES.md at integration
```

Only the `asr` owner may run `uv add` (for ML deps). Already present: fastapi, uvicorn[standard], httpx, numpy, soundfile, pytest (dev). Others needing a dep: write it in your report; do not edit pyproject.toml.

## Shared types (defined in app/session.py; other modules duck-type them as dicts)

```python
Segment = {"id": int, "speaker": int, "name": str | None, "text": str,
           "start": float, "end": float}          # seconds since meeting start (audio clock)
```

## app/audio.py

```python
SAMPLE_RATE = 24000
class AudioSource(Protocol):
    async def blocks(self) -> AsyncIterator[np.ndarray]   # float32 mono 24 kHz, any block size (~100 ms typical)
    def close(self) -> None
def open_source(spec: str) -> AudioSource                # "mic" (default device), "mic:<avfoundation index or name>", "file:<path>" (decoded via ffmpeg, paced in real time)
def list_input_devices() -> list[dict]                   # [{"index": int, "name": str}] via ffmpeg -f avfoundation -list_devices
class Recorder:                                          # 16-bit PCM WAV at 24 kHz, header valid after every flush so a partial file is playable
    def __init__(self, path: Path)
    def write(self, pcm: np.ndarray) -> None
    def close(self) -> None
```
Mic capture uses `ffmpeg -f avfoundation -i ":<idx>" -ac 1 -ar 24000 -f f32le -` as a subprocess.

## app/asr.py

```python
class StreamingASR:
    sample_rate: int            # 24000, from preprocessor_config.json
    chunk_seconds: float        # ~2.93 (22 frames) ; lookahead ~0.53
    def __init__(self, model_id: str = "mlx-community/VibeVoice-ASR-Streaming-7B-4bit")
    def set_context(self, hotwords: list[str]) -> None    # participant names + jargon into the context prefix
    def feed(self, pcm: np.ndarray) -> list[dict]          # accumulate; returns finalized segments [{"speaker": int, "text": str, "start": float, "end": float}]
    def flush(self) -> list[dict]
    last_rtf: float             # rolling compute_time / audio_time
```
`feed` is synchronous and CPU/GPU-bound; session runs it in a single worker thread (`asyncio.to_thread`), never concurrently.

## app/llm.py

```python
async def complete(messages: list[dict], *, json_schema: dict | None = None, stream: bool = False) -> str | AsyncIterator[str]
async def update_notes(transcript: list[Segment], prev: dict, agenda: list[str], user_notes: str) -> dict
    # returns NOTES (below)
async def ask(question: str, context: str) -> AsyncIterator[str]          # streamed answer tokens; must cite [mm:ss] timestamps
async def write_minutes(meeting: dict, template: str) -> str              # Markdown; meeting = Meeting.to_dict() incl. transcript, notes, bookmarks, user_notes, agenda
def templates() -> list[dict]                                              # [{"id": "standup", "name": "Standup"}, ...]
NOTES = {"people": {"<name>": ["note", ...]}, "decisions": ["..."],
         "actions": [{"owner": "<name>", "task": "...", "due": "..."|null}],
         "topics": ["..."], "agenda_current": int | null,
         "claims": [{"text": "...", "speaker": "<name>", "at": float}]}    # checkable factual claims, new since prev
```

## app/factcheck.py

```python
async def brave_search(query: str, count: int = 5) -> list[dict]          # [{"title","url","snippet"}]; BRAVE_API_KEY from env
async def check_claim(claim: dict, complete: Callable) -> dict
    # {"id": str, "claim": str, "speaker": str, "at": float,
    #  "verdict": "supported"|"disputed"|"unclear", "summary": str, "sources": [{"title","url"}]}
```
Only the search query leaves the machine.

## app/store.py

```python
def init(db_path: Path) -> None
def index_meeting(meeting: dict) -> None          # upsert meeting row + transcript segments + minutes into FTS5
def search(q: str, limit: int = 20) -> list[dict] # [{"meeting_id","title","started","snippet","start": float|None}]
def list_meetings() -> list[dict]                 # newest first: id,title,started,ended,duration,participants
def context_for(q: str, limit: int = 12) -> str   # retrieved snippets across meetings for ask-across
```

## Session (app/session.py)

States: `idle -> rollcall -> live -> stopping -> ended`. Roll call: while in `rollcall`, a segment matching `(I'm|I am|my name is|this is) <Name>` maps that segment's speaker id to Name; `POST /api/meeting/rollcall/done` (or 60 s timeout) moves to `live`. Manual rename overrides any time. Notes loop: every 60 s while live (and once at stop), call `llm.update_notes`, then `factcheck.check_claim` for each new claim (max 3 in flight). On stop: flush ASR, close recorder, final notes, `llm.write_minutes`, write files, `store.index_meeting`.

Persistence under `data/meetings/<id>/` (id = `YYYYMMDD-HHMM-<slug>`): `meeting.json`, `transcript.jsonl`, `notes.json`, `factchecks.json`, `minutes.md`, `audio.wav`.

## WebSocket `/ws` (server -> client JSON; every message has `type`)

- `snapshot` `{meeting: Meeting.to_dict() | null, state}` on connect
- `state` `{state, meeting_id, elapsed, rtf, latency}` (also 1 Hz heartbeat while live)
- `segment` `{segment}`; `speakers` `{map: {"<id>": "<name>"}}`
- `notes` `{notes: NOTES}`; `factcheck` `{item}`
- `bookmark` `{id, at, label}`; `user_notes` `{text}`; `agenda` `{items, current, started_at: [float|null]}`
- `ask_delta` `{ask_id, text}`; `ask_done` `{ask_id}`
- `minutes` `{markdown, template}`; `error` `{message}`

## REST (CONTROL = only from 127.0.0.1/::1, else 403 `{"detail":"control is local-only"}`)

| Method path | Body | Who |
|---|---|---|
| POST /api/meeting/start | `{title, participants: [str], hotwords: [str], agenda: [str], template}` | CONTROL |
| POST /api/meeting/rollcall/done | - | CONTROL |
| POST /api/meeting/stop | - | CONTROL |
| POST /api/speakers | `{speaker: int, name: str}` | CONTROL |
| POST /api/bookmark | `{label?: str}` | CONTROL |
| POST /api/user-notes | `{text}` | CONTROL |
| POST /api/agenda | `{items?: [str], current?: int}` | CONTROL |
| POST /api/ask | `{question, scope: "meeting"|"all"}` -> `{ask_id}`; answer streams over WS | anyone |
| GET /api/state | -> snapshot | anyone |
| GET /api/meetings | -> list_meetings | anyone |
| GET /api/meetings/{id} | -> full meeting dict | anyone |
| GET /api/meetings/{id}/audio | WAV, HTTP Range supported | anyone |
| GET /api/meetings/{id}/minutes.md | text/markdown | anyone |
| POST /api/meetings/{id}/minutes | `{template}` -> regenerate | CONTROL |
| GET /api/search?q= | -> store.search | anyone |
| GET /api/templates | -> llm.templates | anyone |
| GET /api/devices | -> list_input_devices | anyone |
| GET / | web/index.html (+ `/static/*`) | anyone |

The page learns whether it is the controller from `GET /api/state` field `can_control: bool`.

## Additions as built (2026-10-16)

These are implemented and binding; they extend, never replace, the sections above.

- WS `ask_start` `{ask_id, question, scope}` is sent before that ask's `ask_delta` events; `error` may carry `ask_id`.
- `snapshot` is also broadcast to every client when a meeting starts, and always carries `can_control`.
- `minutes` also carries `meeting_id`.
- `GET /api/state` also returns `elapsed`, `rtf`, `latency`, `can_control`.
- REST responses: start -> `{meeting_id, state}`; stop -> `{state}` (minutes arrive later over WS); speakers -> `{map}`; bookmark -> `{id, at, label}`; agenda -> the agenda dict; user-notes -> `{ok}`; minutes regenerate -> `{markdown, template}`. A call in the wrong state returns 409 `{"detail": ...}`.
- `StreamingASR.reset() -> None` clears streaming state (model cache, pending audio, speaker state) and resets the audio clock to 0 without reloading weights. The session caches one instance per process and calls `reset()` then `set_context()` at the start of every meeting.
- Front end layout: `web/index.html` loads `web/app.js` as a module, which mounts `web/js/live.js`, `side.js`, `after.js` (each exports `mount(core)`) on top of `web/js/core.js` + `web/js/markdown.js`. Module styles live in `web/css/{live,side,after}.css`; `web/style.css` is the shared base and `web/js/MODULES.md` records DOM ownership.
- Launchers: `./meeting` (and `Tablescribe.command`) check uv/ffmpeg/ollama, start Ollama if needed, warn when `MC_LLM_MODEL` is not pulled, then exec `uv run python -m app`.
