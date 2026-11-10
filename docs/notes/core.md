# core (config, session, server, __main__)

## Decisions
- ASR runs on one dedicated daemon thread (`SerialWorker`): the model is loaded and fed on the same
  thread (MLX GPU streams are per-thread), calls never overlap, and a model load in progress does not
  block Ctrl-C at exit. Warm-up starts in the lifespan; the first `start()` waits on the same thread.
- Audio pump and ASR consumer are separate tasks joined by an unbounded queue: the recorder never
  waits on the model, and when ASR falls behind the consumer concatenates the backlog into one
  `feed` call to catch up. If ASR fails to load, the meeting still records audio and reports an `error`.
- Latency = emit wall time - (wall time of the first audio sample + segment.end).
- Roll call: case-insensitive intro (`I'm|I am|my name is|this is <Name>`, `<Name> here`). A name not
  on the participant list must be Capitalized and not a common word. The first match per speaker
  id wins during roll call; manual rename overrides at any time.
- The notes loop skips the LLM call when no new segments have arrived since the last successful run.
  Claims are deduped by normalised text; at most 3 fact-checks run concurrently; stop() waits up to
  90 s for them before writing minutes.
- `POST /api/meeting/stop` returns immediately (`{"state": "stopping"}`); minutes arrive over WS
  (`minutes`, then `state: ended`).
- WS handler uses an anyio task group (raw asyncio tasks broke Starlette TestClient teardown).

## Protocol additions (beyond docs/CONTRACT.md)
- WS `ask_start {ask_id, question, scope}` before any `ask_delta`; `error` may carry `ask_id`.
- WS `snapshot` is also broadcast when a new meeting starts; `snapshot` carries `can_control`.
- `minutes` event also carries `meeting_id`.
- `GET /api/state` adds `elapsed`, `rtf`, `latency`, `can_control`.

## Measured
- `uv run pytest tests/test_session.py tests/test_server.py -q`: 23 passed in 0.18 s.
- Server cold start to first 200 on /api/state: < 3 s (ASR module absent at the time).
