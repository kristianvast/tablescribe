"""Ollama client, incremental notes loop, grounded Q&A and minutes (see docs/CONTRACT.md, app/llm.py)."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator, Mapping, Sequence
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

from app import config

log = logging.getLogger(__name__)

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
TEMPLATE_ORDER = ("standup", "decision-log", "client", "brainstorm")

# One num_ctx for every call: Ollama reloads the model when num_ctx changes between requests.
NUM_CTX = 32768
KEEP_ALIVE = "30m"
THINK = False
JSON_TEMPERATURE = 0.1
TEXT_TEMPERATURE = 0.3
CONNECT_TIMEOUT = 10.0
READ_TIMEOUT = 180.0
RETRIES = 1

NOTES_CHUNK_CHARS = 12_000
NOTES_PREV_ITEMS = 12
ASK_CONTEXT_CHARS = 60_000
MINUTES_TRANSCRIPT_CHARS = 40_000


class LLMError(RuntimeError):
    pass

def _payload(messages: list[dict], json_schema: dict | None, stream: bool) -> dict:
    body: dict[str, Any] = {
        "model": config.LLM_MODEL,
        "messages": messages,
        "stream": stream,
        "think": THINK,
        "keep_alive": KEEP_ALIVE,
        "options": {"num_ctx": NUM_CTX,
                    "temperature": JSON_TEMPERATURE if json_schema is not None else TEXT_TEMPERATURE},
    }
    if json_schema is not None:
        body["format"] = json_schema
    return body


def _timeout() -> httpx.Timeout:
    return httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)


def _retryable(e: Exception) -> bool:
    if isinstance(e, httpx.HTTPStatusError):
        return e.response.status_code >= 500
    return isinstance(e, httpx.TransportError)


def _error(e: Exception) -> LLMError:
    if isinstance(e, httpx.HTTPStatusError):
        detail = e.response.text[:300]
        return LLMError(f"Ollama {e.response.status_code} for model {config.LLM_MODEL!r}: {detail}")
    return LLMError(f"Ollama request to {config.OLLAMA_URL} failed: {type(e).__name__}: {e}")


async def _chat(body: dict) -> str:
    url = f"{config.OLLAMA_URL.rstrip('/')}/api/chat"
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=_timeout()) as client:
                resp = await client.post(url, json=body)
                resp.raise_for_status()
                data = resp.json()
            if data.get("error"):
                raise LLMError(f"Ollama error: {data['error']}")
            return (data.get("message") or {}).get("content") or ""
        except (httpx.HTTPError, ValueError) as e:
            last = e
            if attempt < RETRIES and _retryable(e):
                log.warning("Ollama call failed (%s), retrying once", e)
                await asyncio.sleep(1.0)
                continue
            break
    assert last is not None
    raise _error(last) from last


async def _chat_stream(body: dict) -> AsyncIterator[str]:
    url = f"{config.OLLAMA_URL.rstrip('/')}/api/chat"
    for attempt in range(RETRIES + 1):
        yielded = False
        try:
            async with httpx.AsyncClient(timeout=_timeout()) as client:
                async with client.stream("POST", url, json=body) as resp:
                    if resp.status_code >= 400:
                        await resp.aread()
                        resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        if data.get("error"):
                            raise LLMError(f"Ollama error: {data['error']}")
                        delta = (data.get("message") or {}).get("content") or ""
                        if delta:
                            yielded = True
                            yield delta
                        if data.get("done"):
                            return
            return
        except (httpx.HTTPError, ValueError) as e:
            # Retry only if nothing reached the caller yet; a half-streamed answer cannot be replayed.
            if attempt < RETRIES and not yielded and _retryable(e):
                log.warning("Ollama stream failed (%s), retrying once", e)
                await asyncio.sleep(1.0)
                continue
            raise _error(e) from e


async def complete(messages: list[dict], *, json_schema: dict | None = None,
                   stream: bool = False) -> str | AsyncIterator[str]:
    body = _payload(messages, json_schema, stream)
    if stream:
        return _chat_stream(body)
    return await _chat(body)


async def _complete_json(messages: list[dict], schema: dict) -> dict:
    raw = await complete(messages, json_schema=schema)
    assert isinstance(raw, str)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise LLMError(f"model returned invalid JSON: {raw[:200]!r}") from e
    if not isinstance(data, dict):
        raise LLMError(f"model returned non-object JSON: {raw[:200]!r}")
    return data


def fmt_ts(seconds: float) -> str:
    s = int(max(0.0, float(seconds or 0.0)))
    return f"{s // 60:02d}:{s % 60:02d}"


def _name(seg: Mapping[str, Any]) -> str:
    return seg.get("name") or f"Speaker {seg.get('speaker', '?')}"


def _norm(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", str(text).lower()).split())


def _similar(a: str, b: str, threshold: float = 0.82) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return na == nb
    return na == nb or SequenceMatcher(None, na, nb).ratio() >= threshold


def _add_unique(items: list[str], new: Sequence[Any]) -> None:
    for item in new:
        text = str(item).strip()
        if text and not any(_similar(text, old) for old in items):
            items.append(text)


def _empty_notes() -> dict:
    return {"people": {}, "decisions": [], "actions": [], "topics": [], "agenda_current": None, "claims": []}


NOTES_SCHEMA = {
    "type": "object",
    "properties": {
        "people": {"type": "array", "items": {
            "type": "object",
            "properties": {"name": {"type": "string"}, "notes": {"type": "array", "items": {"type": "string"}}},
            "required": ["name", "notes"]}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "actions": {"type": "array", "items": {
            "type": "object",
            "properties": {"owner": {"type": "string"}, "task": {"type": "string"},
                           "due": {"type": ["string", "null"]}},
            "required": ["owner", "task", "due"]}},
        "topics": {"type": "array", "items": {"type": "string"}},
        "agenda_current": {"type": ["integer", "null"]},
        "claims": {"type": "array", "items": {
            "type": "object",
            "properties": {"segment": {"type": "integer"}, "text": {"type": "string"}},
            "required": ["segment", "text"]}},
    },
    "required": ["people", "decisions", "actions", "topics", "agenda_current", "claims"],
}

NOTES_SYSTEM = """You keep live notes for an in-room meeting. You receive the notes so far and ONLY the NEW transcript lines since the last update. Return JSON with only what is NEW in those lines:

- people: for each speaker who said something substantive in the new lines, 1-3 short notes (their position, update, concern or commitment). Use the speaker names exactly as written in the transcript. Skip greetings and roll-call lines.
- decisions: decisions the group actually agreed in the new lines, one short sentence each. Not proposals, not opinions.
- actions: tasks someone committed to or was assigned. owner = the person who will do it (a transcript name), task = short imperative phrase, due = the deadline as said ("Tuesday", "next Monday") or null.
- topics: short labels (2-5 words) for subjects discussed in the new lines.
- agenda_current: the 0-based index of the agenda item being discussed at the END of the new lines, or null if no agenda or unclear.
- claims: EVERY checkable PUBLIC factual statement made in the new lines - list each one separately; a few lines can hold several. That means numbers about the world, dates, records, measurements, scientific or historical facts (e.g. "The Amazon is the longest river in Africa", "Water boils at 90 degrees Celsius at sea level"). Include claims that may be false, and claims someone else then disputes. Rewrite each as a self-contained sentence and give the #segment number where it was said. EXCLUDE opinions, plans, and internal company facts (our signups, our pilot accounts, our build status).

Do not repeat anything already in the notes so far. If a new line corrects an earlier action (new owner or due date), return the corrected action. Empty lists are fine."""


def _notes_digest(prev: Mapping[str, Any]) -> str:
    k = NOTES_PREV_ITEMS
    people = {n: list(v)[-3:] for n, v in (prev.get("people") or {}).items()}
    digest = {
        "people": people,
        "decisions": list(prev.get("decisions") or [])[-k:],
        "actions": [{"owner": a.get("owner"), "task": a.get("task"), "due": a.get("due")}
                    for a in list(prev.get("actions") or [])[-k:] if isinstance(a, Mapping)],
        "topics": list(prev.get("topics") or [])[-k:],
    }
    return json.dumps(digest, ensure_ascii=False)


def _chunks(segments: Sequence[Mapping[str, Any]], limit: int) -> list[list[Mapping[str, Any]]]:
    out: list[list[Mapping[str, Any]]] = []
    cur: list[Mapping[str, Any]] = []
    size = 0
    for seg in segments:
        n = len(seg.get("text", "")) + 32
        if cur and size + n > limit:
            out.append(cur)
            cur, size = [], 0
        cur.append(seg)
        size += n
    if cur:
        out.append(cur)
    return out


def _merge_notes(prev: dict, delta: Mapping[str, Any], segs: list[Mapping[str, Any]], agenda: list[str]) -> dict:
    notes = {
        "people": {n: list(v) for n, v in (prev.get("people") or {}).items()},
        "decisions": list(prev.get("decisions") or []),
        "actions": [dict(a) for a in prev.get("actions") or [] if isinstance(a, Mapping)],
        "topics": list(prev.get("topics") or []),
        "agenda_current": prev.get("agenda_current"),
        "claims": [],
    }
    by_id = {int(s["id"]): s for s in segs}
    names = {_name(s) for s in segs} | set(notes["people"])

    def canon(name: str) -> str:
        name = str(name).strip()
        if not name:
            return "Unassigned"
        first = name.split()[0].lower()
        for n in names:
            if n.lower() == name.lower() or n.split()[0].lower() == first:
                return n
        return name

    for p in delta.get("people") or []:
        if not isinstance(p, Mapping) or not str(p.get("name", "")).strip():
            continue
        bucket = notes["people"].setdefault(canon(p["name"]), [])
        _add_unique(bucket, p.get("notes") or [])
    _add_unique(notes["decisions"], delta.get("decisions") or [])

    for a in delta.get("actions") or []:
        if not isinstance(a, Mapping) or not str(a.get("task", "")).strip():
            continue
        due = a.get("due")
        due = str(due).strip() if due not in (None, "", "null", "none", "None") else None
        item = {"owner": canon(a.get("owner") or "Unassigned"), "task": str(a["task"]).strip(), "due": due}
        for old in notes["actions"]:
            if _similar(old.get("task", ""), item["task"], 0.75):
                old.update({k: v for k, v in item.items() if v})
                break
        else:
            notes["actions"].append(item)

    _add_unique(notes["topics"], delta.get("topics") or [])

    cur = delta.get("agenda_current")
    if isinstance(cur, int) and not isinstance(cur, bool) and 0 <= cur < len(agenda):
        notes["agenda_current"] = cur
    elif not agenda:
        notes["agenda_current"] = None

    seen_claims: list[str] = []
    for c in delta.get("claims") or []:
        if not isinstance(c, Mapping) or not str(c.get("text", "")).strip():
            continue
        text = str(c["text"]).strip()
        if any(_similar(text, s) for s in seen_claims):
            continue
        sid = c.get("segment")
        seg = by_id.get(sid, segs[-1]) if isinstance(sid, int) else segs[-1]
        seen_claims.append(text)
        notes["claims"].append({"text": text, "speaker": _name(seg), "at": float(seg.get("start", 0.0))})
    return notes


async def update_notes(transcript: list[dict], prev: dict, agenda: list[str], user_notes: str) -> dict:
    prev = dict(prev or {})
    upto = prev.get("_upto")
    upto = upto if isinstance(upto, int) else -1
    new = [s for s in transcript if int(s.get("id", -1)) > upto]
    base = _empty_notes() | {k: v for k, v in prev.items()}
    base["claims"] = []
    if not new:
        return base | {"_upto": upto}

    agenda_txt = "\n".join(f"{i}. {a}" for i, a in enumerate(agenda)) or "(none)"
    notes = base
    all_claims: list[dict] = []
    for chunk in _chunks(new, NOTES_CHUNK_CHARS):
        lines = "\n".join(f"#{s['id']} [{fmt_ts(s.get('start', 0))}] {_name(s)}: {s['text']}" for s in chunk)
        user = (f"Agenda (0-based):\n{agenda_txt}\n\n"
                f"Facilitator's typed notes:\n{(user_notes or '').strip()[-2000:] or '(none)'}\n\n"
                f"Notes so far:\n{_notes_digest(notes)}\n\n"
                f"NEW transcript lines:\n{lines}")
        delta = await _complete_json([{"role": "system", "content": NOTES_SYSTEM},
                                      {"role": "user", "content": user}], NOTES_SCHEMA)
        notes = _merge_notes(notes, delta, chunk, agenda)
        for c in notes["claims"]:
            if not any(_similar(c["text"], o["text"]) for o in all_claims):
                all_claims.append(c)
    notes["claims"] = all_claims
    notes["_upto"] = int(new[-1]["id"])
    return notes


ASK_SYSTEM = """You answer questions during and about meetings, using ONLY the context below (transcript lines look like "[mm:ss] Name: text").
- Be concise: 1-4 sentences or a short list.
- Cite every fact with its timestamp as [mm:ss]. For lines from past meetings, cite the meeting title plus [mm:ss], e.g. (Weekly sync [12:30]).
- If the context does not contain the answer, say plainly: "The transcript doesn't say." Do not guess or use outside knowledge."""


async def ask(question: str, context: str) -> AsyncIterator[str]:
    context = context or ""
    if len(context) > ASK_CONTEXT_CHARS:
        context = "...\n" + context[-ASK_CONTEXT_CHARS:]
    messages = [{"role": "system", "content": ASK_SYSTEM},
                {"role": "user", "content": f"Context:\n{context or '(empty)'}\n\nQuestion: {question}\n\n"
                 "Answer from the context and put the [mm:ss] of each line you used right after the fact, "
                 "in this format: \"<Name> agreed to <thing> [mm:ss].\""}]
    stream = await complete(messages, stream=True)
    assert not isinstance(stream, str)
    async for delta in stream:
        yield delta


_NAME_RE = re.compile(r"<!--\s*name:\s*(.+?)\s*-->")
_PROMPT_RE = re.compile(r"<!--\s*prompt\s*\n(.*?)-->\s*\n?", re.S)


def _load_template(template_id: str) -> tuple[str, str, str]:
    if template_id not in TEMPLATE_ORDER:
        template_id = "standup"
    raw = (TEMPLATES_DIR / f"{template_id}.md").read_text(encoding="utf-8")
    name_m = _NAME_RE.search(raw)
    prompt_m = _PROMPT_RE.search(raw)
    name = name_m.group(1) if name_m else template_id
    prompt = prompt_m.group(1).strip() if prompt_m else ""
    skeleton = _PROMPT_RE.sub("", _NAME_RE.sub("", raw, count=1), count=1).strip() + "\n"
    return name, prompt, skeleton


def templates() -> list[dict]:
    return [{"id": t, "name": _load_template(t)[0]} for t in TEMPLATE_ORDER
            if (TEMPLATES_DIR / f"{t}.md").is_file()]


def _attendees(meeting: Mapping[str, Any]) -> list[str]:
    names = [p for p in meeting.get("participants") or [] if p]
    for n in (meeting.get("speakers") or {}).values():
        if n and n not in names:
            names.append(n)
    for n in ((meeting.get("notes") or {}).get("people") or {}):
        if n and n not in names and not n.startswith("Speaker "):
            names.append(n)
    return names


def _duration(meeting: Mapping[str, Any]) -> str:
    tr = meeting.get("transcript") or []
    end = max((float(s.get("end", 0.0)) for s in tr), default=0.0)
    return f"{int(end // 60)} min" if end else "-"


def _sections(meeting: Mapping[str, Any]) -> dict[str, str]:
    notes = meeting.get("notes") or {}
    started = str(meeting.get("started") or "")
    agenda = meeting.get("agenda") or {}
    items = agenda.get("items") if isinstance(agenda, Mapping) else agenda
    attendees = _attendees(meeting)
    user_notes = (meeting.get("user_notes") or "").strip()

    bms = meeting.get("bookmarks") or []
    key_moments = "\n".join(f"- [{fmt_ts(b.get('at', 0))}] {b.get('label') or 'Bookmark'}" for b in bms)
    decisions = "\n".join(f"- {d}" for d in notes.get("decisions") or [])
    actions = "\n".join(
        f"- [ ] **{a.get('owner') or 'Unassigned'}**: {a.get('task', '')}" + (f" ({a['due']})" if a.get("due") else "")
        for a in notes.get("actions") or [] if isinstance(a, Mapping))
    icon = {"supported": "Supported", "disputed": "Disputed", "unclear": "Unclear"}
    fcs = []
    for f in meeting.get("factchecks") or []:
        src = ", ".join(f"[{s.get('title') or s.get('url')}]({s.get('url')})" for s in (f.get("sources") or [])[:2])
        fcs.append(f"- **{icon.get(f.get('verdict'), 'Unclear')}** - \"{f.get('claim', '')}\" "
                   f"({f.get('speaker') or '?'}, [{fmt_ts(f.get('at', 0))}]): {f.get('summary', '')}"
                   + (f" Sources: {src}" if src else ""))
    people = "\n".join(f"- **{n}**: " + "; ".join(v) for n, v in (notes.get("people") or {}).items() if v)
    return {
        "title": meeting.get("title") or "Meeting",
        "date": started[:16].replace("T", " ") or "-",
        "attendees": ", ".join(attendees) or "-",
        "duration": _duration(meeting),
        "agenda": "\n".join(f"{i + 1}. {a}" for i, a in enumerate(items or [])) or "_No agenda._",
        "user_notes": user_notes or "_None._",
        "key_moments": key_moments or "_No bookmarks._",
        "decisions": decisions or "_No decisions recorded._",
        "actions": actions or "_No action items._",
        "factchecks": "\n".join(fcs) or "_No claims were fact-checked._",
        "people": people or "_No per-person notes._",
    }


def _minutes_transcript(meeting: Mapping[str, Any]) -> str:
    tr = meeting.get("transcript") or []
    lines = [f"[{fmt_ts(s.get('start', 0))}] {_name(s)}: {s.get('text', '')}" for s in tr]
    full = "\n".join(lines)
    if len(full) <= MINUTES_TRANSCRIPT_CHARS:
        return full
    kept: list[str] = []
    total = 0
    for line in reversed(lines):
        if total + len(line) + 1 > MINUTES_TRANSCRIPT_CHARS:
            break
        kept.append(line)
        total += len(line) + 1
    return f"(earlier part omitted - see the running notes)\n" + "\n".join(reversed(kept))


MINUTES_SYSTEM = """You write meeting minutes. Write ONLY the Markdown body section requested by the instructions - no title, no attendee header, no action-item list, no fact-check list (those are added separately). Use "## " / "### " headings and short bullets. Ground every statement in the provided notes and transcript; add [mm:ss] timestamps for key points. Fold the facilitator's typed notes in where they belong."""


async def write_minutes(meeting: dict, template: str) -> str:
    _, prompt, skeleton = _load_template(template)
    sec = _sections(meeting)
    notes = {k: v for k, v in (meeting.get("notes") or {}).items() if not k.startswith("_") and k != "claims"}
    user = (f"Instructions for this template:\n{prompt}\n\n"
            f"Meeting: {sec['title']} ({sec['date']}, {sec['duration']})\nAttendees: {sec['attendees']}\n"
            f"Agenda:\n{sec['agenda']}\n\n"
            f"Facilitator's typed notes:\n{sec['user_notes']}\n\n"
            f"Bookmarked moments:\n{sec['key_moments']}\n\n"
            f"Running notes (JSON):\n{json.dumps(notes, ensure_ascii=False)}\n\n"
            f"Transcript:\n{_minutes_transcript(meeting) or '(empty)'}")
    if meeting.get("transcript") or notes.get("people") or (meeting.get("user_notes") or "").strip():
        body = await complete([{"role": "system", "content": MINUTES_SYSTEM},
                               {"role": "user", "content": user}])
        assert isinstance(body, str)
        body = _strip_fence(body).strip()
    else:
        body = "_Nothing was recorded._"
    sec["body"] = body
    out = skeleton
    for key, val in sec.items():
        out = out.replace("{{" + key + "}}", val)
    return out


def _strip_fence(text: str) -> str:
    m = re.fullmatch(r"\s*```(?:markdown|md)?\s*\n(.*?)\n```\s*", text, re.S)
    return m.group(1) if m else text
