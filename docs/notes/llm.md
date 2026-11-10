# llm (app/llm.py, app/templates/*.md)

Status 2026-10-16: code complete. **Measurements not run yet** - `ollama list` was empty when
checked (nemotron-3.5-lightning was still downloading), so every number below marked MISSING has
not been measured. Nothing here is estimated.

## Design

- `complete()` - POST `/api/chat` with `think`, `keep_alive: "30m"`, `options.num_ctx`, and
  temperature 0.1 (JSON) / 0.3 (text). A `json_schema` value is passed as Ollama `format`
  (structured output). `stream=True` returns an async iterator of content deltas. Timeouts:
  connect 10 s, read 180 s. One retry on transport errors / 5xx (for a stream, only before the first
  token). On failure it raises `LLMError`.
- One `num_ctx` for every call (currently 32768): Ollama reloads the model when `num_ctx` changes
  between requests, so the notes, ask, minutes and fact-check calls must all share it.
- `update_notes` is incremental. It sends only segments with `id > prev["_upto"]` (in chunks of
  12k chars at most), plus a capped digest of prev (last 12 decisions/actions/topics, last 3 notes per
  person), the agenda and the tail of the typed notes. The model returns only a delta. The merge in code
  dedupes (normalised text + SequenceMatcher), canonicalises names to transcript names, and updates
  an existing action in place when the task matches but the owner or due date changed. Claims carry
  a segment id, which code maps to `speaker`/`at`, so timestamps are exact. With no new segments,
  no model call is made.
- `write_minutes` - each template is a prompt plus a Markdown skeleton. The required parts (header,
  agenda, decisions, `- [ ] **Owner**: task (due)`, key moments `[mm:ss] label`, fact-checks,
  facilitator notes) are rendered deterministically from the meeting dict, so they cannot be dropped
  or garbled. The model writes only the `{{body}}` prose. Transcript input is capped at 40k chars
  (the tail), with the running notes standing in for the earlier part.
- `ask` - context capped to the last 60k chars. The system prompt requires `[mm:ss]` citations (with the
  meeting title for past meetings) and the plain answer "The transcript doesn't say."

## Measurements - MISSING (TUNING PENDING)

| What | Value |
|---|---|
| prompt tokens/s (`prompt_eval_count / prompt_eval_duration`) | MISSING |
| generation tokens/s (`eval_count / eval_duration`) | MISSING |
| `ollama ps` memory at num_ctx 32768 (beside the ~6 GB ASR) | MISSING |
| update_notes latency, 60 s of new transcript (target < 25 s) | MISSING |
| update_notes latency, 5 min of new transcript | MISSING |
| thinking on vs off (latency + notes quality) | MISSING - default `THINK = False` until measured |
| num_ctx final choice | 32768 provisional; confirm against `ollama ps` memory |
| quality: JSON validity, claim precision, owners/due dates | MISSING |

Measured so far: the sample script parses into 38 segments. The merge and minutes assembly were checked
offline with a scratch stub (not committed). `templates()` lists 4 templates.
