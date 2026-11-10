"""Meeting state machine, roll call, persistence and event bus (see docs/CONTRACT.md)."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping, Sequence
import importlib
import inspect
import json
import logging
import os
import queue
import re
import threading
import time
import uuid
from concurrent.futures import Future
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, TypedDict

import numpy as np

from app import config

log = logging.getLogger(__name__)

STATES = ("idle", "rollcall", "live", "stopping", "ended")
ACTIVE_STATES = frozenset({"rollcall", "live"})
DEFAULT_SAMPLE_RATE = 24000
CONTEXT_CHARS = 48_000
QUEUE_LIMIT = 500
STOP_ASR_TIMEOUT = 180.0
STOP_FACTCHECK_TIMEOUT = 90.0
FACTCHECK_CONCURRENCY = 3
_MEETING_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


class Segment(TypedDict):
    id: int
    speaker: int
    name: str | None
    text: str
    start: float
    end: float


class MeetingError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


def empty_notes() -> dict:
    return {"people": {}, "decisions": [], "actions": [], "topics": [], "agenda_current": None, "claims": []}


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(title: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    return slug or "meeting"


def write_json_atomic(path: Path, obj: Any) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def fmt_ts(seconds: float) -> str:
    s = int(max(0.0, seconds))
    return f"{s // 60:02d}:{s % 60:02d}"


def speaker_label(seg: Mapping[str, Any]) -> str:
    return seg.get("name") or f"Speaker {seg.get('speaker', '?')}"


def format_transcript(segments: Sequence[Mapping[str, Any]], max_chars: int = CONTEXT_CHARS) -> str:
    kept: list[str] = []
    total = 0
    for seg in reversed(segments):
        line = f"[{fmt_ts(seg['start'])}] {speaker_label(seg)}: {seg['text']}"
        if total + len(line) + 1 > max_chars:
            break
        kept.append(line)
        total += len(line) + 1
    return "\n".join(reversed(kept))


# Roll call. The intro phrase is case-insensitive; a name outside the participant list must be
# Capitalized and not a common word, so "I'm going to..." or "this is the plan" never map a speaker.
_INTRO_RE = re.compile(
    r"(?i:\b(?:i'?m|i\s+am|my\s+name\s+is|my\s+name's|this\s+is)\b)[\s,]+([A-Za-z][\w'-]*(?:\s+[A-Z][\w'-]*)?)"
)
_HERE_RE = re.compile(r"(?:^|[.!?,;]\s*|(?i:\b(?:hi|hello|hey)\b)[\s,]*)([A-Z][\w'-]*)\s+here\b")
_NOT_NAMES = frozenset(
    "a an the not so just here there sure going gonna glad happy sorry back done ready fine good great okay ok "
    "really also still new that this it in on at with from for to excited thinking looking trying working afraid "
    "joining speaking talking everything nothing something everyone someone nobody we they you he she i "
    "hi hello hey yes yeah no well thanks thank".split()
)


def _resolve_name(candidate: str, participants: list[str]) -> str | None:
    cand = candidate.strip(" '-")
    if not cand or cand.lower().endswith("'s"):
        return None
    words = cand.split()
    first = words[0].lower()
    for p in participants:
        pl = p.lower()
        if pl == cand.lower() or pl.split()[0] == first:
            return p
    if not words[0][0].isupper() or first in _NOT_NAMES:
        return None
    return " ".join(words)


def match_rollcall(text: str, participants: list[str] | tuple[str, ...] = ()) -> str | None:
    text = text.replace("\u2019", "'").strip()
    for rx in (_INTRO_RE, _HERE_RE):
        for m in rx.finditer(text):
            name = _resolve_name(m.group(1), list(participants))
            if name:
                return name
    return None


class EventBus:
    """Fan-out to per-subscriber queues; a slow subscriber loses its oldest events past `limit`.

    publish() must be called from the event-loop thread.
    """

    def __init__(self, limit: int = QUEUE_LIMIT):
        self.limit = limit
        self._subs: set[asyncio.Queue] = set()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, event: dict) -> None:
        for q in list(self._subs):
            while q.qsize() >= self.limit:
                q.get_nowait()
            q.put_nowait(event)


class SerialWorker:
    """One daemon thread running calls in submission order.

    The ASR model is loaded and fed on this same thread (MLX GPU streams are per-thread), and a
    daemon thread does not block interpreter exit while a model load is in progress.
    """

    def __init__(self, name: str = "asr-worker"):
        self._q: queue.SimpleQueue = queue.SimpleQueue()
        self._closed = False
        self._thread = threading.Thread(target=self._run, name=name, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            fut, fn, args = item
            if not fut.set_running_or_notify_cancel():
                continue
            try:
                fut.set_result(fn(*args))
            except BaseException as e:  # noqa: BLE001 - delivered to the awaiting coroutine
                fut.set_exception(e)

    async def run(self, fn: Callable, *args: Any) -> Any:
        if self._closed:
            raise RuntimeError("ASR worker is shut down")
        fut: Future = Future()
        self._q.put((fut, fn, args))
        return await asyncio.wrap_future(fut)

    def shutdown(self) -> None:
        self._closed = True
        self._q.put(None)


class Meeting:
    def __init__(self, *, id: str, title: str, participants: list[str], hotwords: list[str],
                 agenda: list[str], template: str, dir: Path):
        self.id = id
        self.title = title
        self.participants = participants
        self.hotwords = hotwords
        self.template = template
        self.dir = dir
        self.started = now_iso()
        self.ended: str | None = None
        self.state = "idle"
        self.agenda_items = list(agenda)
        self.agenda_current: int | None = 0 if agenda else None
        self.agenda_started_at: list[float | None] = [None] * len(agenda)
        if agenda:
            self.agenda_started_at[0] = 0.0
        self.speakers: dict[int, str] = {}
        self.transcript: list[Segment] = []
        self.notes: dict = empty_notes()
        self.factchecks: list[dict] = []
        self.bookmarks: list[dict] = []
        self.user_notes = ""
        self.minutes: str | None = None
        self.wall_t0 = time.time()
        self.audio_t0: float | None = None
        self.end_t: float | None = None

    def elapsed(self) -> float:
        end = self.end_t or time.time()
        return round(max(0.0, end - (self.audio_t0 or self.wall_t0)), 2)

    def agenda_dict(self) -> dict:
        return {"items": list(self.agenda_items), "current": self.agenda_current,
                "started_at": list(self.agenda_started_at)}

    def relabel(self) -> None:
        for seg in self.transcript:
            seg["name"] = self.speakers.get(seg["speaker"])

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "started": self.started,
            "ended": self.ended,
            "state": self.state,
            "participants": list(self.participants),
            "hotwords": list(self.hotwords),
            "agenda": self.agenda_dict(),
            "template": self.template,
            "speakers": {str(k): v for k, v in self.speakers.items()},
            "transcript": [dict(s) for s in self.transcript],
            "notes": self.notes,
            "factchecks": list(self.factchecks),
            "bookmarks": list(self.bookmarks),
            "user_notes": self.user_notes,
            "minutes": self.minutes,
        }


def _default_asr_factory() -> Any:
    return importlib.import_module("app.asr").StreamingASR()


def _default_source_factory(spec: str) -> Any:
    return importlib.import_module("app.audio").open_source(spec)


def _default_recorder_factory(path: Path) -> Any:
    return importlib.import_module("app.audio").Recorder(path)


class SessionManager:
    def __init__(self, *, source_spec: str = "mic", meetings_dir: Path | None = None,
                 index_db: Path | None = None, asr_factory: Callable[[], Any] | None = None,
                 llm: Any = None, factcheck: Any = None, store: Any = None,
                 source_factory: Callable[[str], Any] | None = None,
                 recorder_factory: Callable[[Path], Any] | None = None,
                 notes_interval: float | None = None, rollcall_timeout: float | None = None,
                 heartbeat_interval: float = 1.0):
        self.source_spec = source_spec
        self.meetings_dir = Path(meetings_dir or config.MEETINGS_DIR)
        self.index_db = Path(index_db or config.INDEX_DB)
        self.notes_interval = config.NOTES_INTERVAL if notes_interval is None else notes_interval
        self.rollcall_timeout = config.ROLLCALL_TIMEOUT if rollcall_timeout is None else rollcall_timeout
        self.heartbeat_interval = heartbeat_interval
        self.bus = EventBus()
        self.meeting: Meeting | None = None
        self._asr_factory = asr_factory or _default_asr_factory
        self._source_factory = source_factory or _default_source_factory
        self._recorder_factory = recorder_factory or _default_recorder_factory
        self._llm_mod, self._factcheck_mod, self._store_mod = llm, factcheck, store
        self._asr: Any = None
        self._worker = SerialWorker()
        self._tasks: dict[str, asyncio.Task] = {}
        self._bg: set[asyncio.Task] = set()
        self._fc_tasks: set[asyncio.Task] = set()
        self._fc_sem: asyncio.Semaphore | None = None
        self._notes_lock: asyncio.Lock | None = None
        self._seen_claims: set[str] = set()
        self._notes_done_n = 0
        self._latency: float | None = None
        self._source: Any = None
        self._recorder: Any = None
        self._asr_q: asyncio.Queue | None = None
        self._transcript_fh: Any = None

    def llm(self) -> Any:
        if self._llm_mod is None:
            self._llm_mod = importlib.import_module("app.llm")
        return self._llm_mod

    def factcheck(self) -> Any:
        if self._factcheck_mod is None:
            self._factcheck_mod = importlib.import_module("app.factcheck")
        return self._factcheck_mod

    def store(self) -> Any:
        if self._store_mod is None:
            self._store_mod = importlib.import_module("app.store")
        return self._store_mod

    def init_store(self) -> None:
        self.index_db.parent.mkdir(parents=True, exist_ok=True)
        self.store().init(self.index_db)

    @property
    def state(self) -> str:
        return self.meeting.state if self.meeting else "idle"

    def publish(self, type_: str, **payload: Any) -> None:
        self.bus.publish({"type": type_, **payload})

    def _error(self, message: str) -> None:
        log.warning(message)
        self.publish("error", message=message)

    def snapshot(self) -> dict:
        return {"meeting": self.meeting.to_dict() if self.meeting else None, "state": self.state}

    def state_event(self) -> dict:
        m = self.meeting
        return {"type": "state", "state": self.state, "meeting_id": m.id if m else None,
                "elapsed": m.elapsed() if m else 0.0,
                "rtf": getattr(self._asr, "last_rtf", None), "latency": self._latency}

    def _set_state(self, state: str) -> None:
        assert self.meeting is not None and state in STATES
        self.meeting.state = state
        self._persist()
        self.bus.publish(self.state_event())

    def _persist(self) -> None:
        m = self.meeting
        if m is not None and m.dir.is_dir():
            write_json_atomic(m.dir / "meeting.json", m.to_dict())

    def _spawn(self, coro: Any) -> asyncio.Task:
        task = asyncio.create_task(coro)
        self._bg.add(task)
        task.add_done_callback(self._bg.discard)
        return task

    def _load_asr(self) -> Any:
        if self._asr is None:
            self._asr = self._asr_factory()
        return self._asr

    async def warm_up(self) -> bool:
        try:
            await self._worker.run(self._load_asr)
        except Exception as e:
            log.warning("ASR warm-up failed", exc_info=True)
            self.publish("error", message=f"ASR warm-up failed: {e}")
            return False
        log.info("ASR model ready")
        return True

    def _new_id(self, title: str) -> str:
        base = f"{datetime.now():%Y%m%d-%H%M}-{slugify(title)}"
        mid, n = base, 2
        while (self.meetings_dir / mid).exists():
            mid, n = f"{base}-{n}", n + 1
        return mid

    async def start(self, title: str = "", participants: list[str] | None = None,
                    hotwords: list[str] | None = None, agenda: list[str] | None = None,
                    template: str = "standup", source: str | None = None) -> Meeting:
        if self.state in ("rollcall", "live", "stopping"):
            raise MeetingError("a meeting is already running")
        participants = [p.strip() for p in participants or [] if p and p.strip()]
        hotwords = [h.strip() for h in hotwords or [] if h and h.strip()]
        agenda = [a.strip() for a in agenda or [] if a and a.strip()]
        title = title.strip() or f"Meeting {datetime.now():%Y-%m-%d %H:%M}"
        mid = self._new_id(title)
        src = self._source_factory(source or self.source_spec)
        mdir = self.meetings_dir / mid
        try:
            mdir.mkdir(parents=True)
            recorder = self._recorder_factory(mdir / "audio.wav")
        except Exception:
            src.close()
            raise
        m = Meeting(id=mid, title=title, participants=participants, hotwords=hotwords,
                    agenda=agenda, template=template or "standup", dir=mdir)
        self.meeting = m
        self._source, self._recorder = src, recorder
        self._asr_q = asyncio.Queue()
        self._fc_sem = asyncio.Semaphore(FACTCHECK_CONCURRENCY)
        self._notes_lock = asyncio.Lock()
        self._seen_claims, self._fc_tasks = set(), set()
        self._notes_done_n, self._latency = 0, None
        self._transcript_fh = open(mdir / "transcript.jsonl", "a", encoding="utf-8")
        self._set_state("rollcall")
        self.publish("snapshot", **self.snapshot())
        self._tasks = {
            "asr": asyncio.create_task(self._asr_loop(m)),
            "pump": asyncio.create_task(self._pump(m)),
            "heartbeat": asyncio.create_task(self._heartbeat()),
            "rollcall": asyncio.create_task(self._rollcall_timer(m)),
        }
        log.info("meeting %s started (source=%s)", mid, source or self.source_spec)
        return m

    async def rollcall_done(self) -> None:
        if self.state != "rollcall":
            raise MeetingError("not in roll call")
        await self._cancel("rollcall")
        self._go_live()

    def _go_live(self) -> None:
        self._set_state("live")
        self._tasks["notes"] = asyncio.create_task(self._notes_loop())

    async def _rollcall_timer(self, m: Meeting) -> None:
        await asyncio.sleep(self.rollcall_timeout)
        if self.meeting is m and m.state == "rollcall":
            self._tasks.pop("rollcall", None)
            self._go_live()

    async def _cancel(self, name: str) -> None:
        task = self._tasks.pop(name, None)
        if task is None or task is asyncio.current_task():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception:
            log.exception("task %s failed", name)

    def _close_io(self) -> None:
        for name, obj in (("source", self._source), ("recorder", self._recorder), ("transcript", self._transcript_fh)):
            if obj is None:
                continue
            try:
                obj.close()
            except Exception as e:
                self._error(f"closing {name} failed: {e}")
        self._source = self._recorder = self._transcript_fh = None

    async def stop(self) -> Meeting:
        m = self.meeting
        if m is None or m.state not in ACTIVE_STATES:
            raise MeetingError("no meeting is running")
        self._set_state("stopping")
        for name in ("rollcall", "notes", "heartbeat", "pump"):
            await self._cancel(name)
        if self._source is not None:
            try:
                self._source.close()
            except Exception as e:
                self._error(f"closing audio source failed: {e}")
            self._source = None
        assert self._asr_q is not None
        self._asr_q.put_nowait(None)
        asr_task = self._tasks.pop("asr", None)
        if asr_task is not None:
            try:
                await asyncio.wait_for(asr_task, STOP_ASR_TIMEOUT)
            except TimeoutError:
                self._error("ASR did not finish the backlog in time; transcript may be cut short")
            except Exception as e:
                self._error(f"ASR failed while stopping: {e}")
        m.end_t, m.ended = time.time(), now_iso()
        self._close_io()
        await self._run_notes()
        if self._fc_tasks:
            _, pending = await asyncio.wait(set(self._fc_tasks), timeout=STOP_FACTCHECK_TIMEOUT)
            for t in pending:
                t.cancel()
        write_text_atomic(m.dir / "transcript.jsonl", "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in m.transcript))
        try:
            minutes = await self.llm().write_minutes(m.to_dict(), m.template)
            m.minutes = minutes
            write_text_atomic(m.dir / "minutes.md", minutes)
            self.publish("minutes", markdown=m.minutes, template=m.template, meeting_id=m.id)
        except Exception as e:
            log.exception("write_minutes failed")
            self._error(f"Writing minutes failed: {e}")
        self._set_state("ended")
        try:
            await asyncio.to_thread(self.store().index_meeting, m.to_dict())
        except Exception as e:
            log.exception("index_meeting failed")
            self._error(f"Indexing meeting failed: {e}")
        log.info("meeting %s ended", m.id)
        return m

    async def shutdown(self) -> None:
        for name in list(self._tasks):
            await self._cancel(name)
        for t in list(self._bg) + list(self._fc_tasks):
            t.cancel()
        self._close_io()
        m = self.meeting
        if m is not None and m.state not in ("ended", "idle"):
            m.end_t, m.ended = time.time(), now_iso()
            m.state = "ended"
            self._persist()
        self._worker.shutdown()

    async def _pump(self, m: Meeting) -> None:
        assert self._asr_q is not None
        sr = getattr(self._asr, "sample_rate", None) or DEFAULT_SAMPLE_RATE
        try:
            async for block in self._source.blocks():
                pcm = np.asarray(block, dtype=np.float32).reshape(-1)
                if m.audio_t0 is None:
                    m.audio_t0 = time.time() - len(pcm) / sr
                self._recorder.write(pcm)
                self._asr_q.put_nowait(pcm)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("audio source failed")
            self._error(f"Audio source failed: {e}")
        log.info("audio source ended")

    async def _asr_loop(self, m: Meeting) -> None:
        q = self._asr_q
        assert q is not None
        try:
            asr = await self._worker.run(self._load_asr)
            if hasattr(asr, "reset"):
                await self._worker.run(asr.reset)
            await self._worker.run(asr.set_context, m.participants + m.hotwords)
        except Exception as e:
            log.exception("ASR unavailable")
            self._error(f"ASR unavailable, recording audio only: {e}")
            while await q.get() is not None:
                pass
            return
        failing = False
        done = False
        while not done:
            first = await q.get()
            if first is None:
                break
            blocks = [first]
            while not q.empty():
                nxt = q.get_nowait()
                if nxt is None:
                    done = True
                    break
                blocks.append(nxt)
            pcm = blocks[0] if len(blocks) == 1 else np.concatenate(blocks)
            try:
                segs = await self._worker.run(asr.feed, pcm)
                failing = False
            except Exception as e:
                if not failing:
                    log.exception("ASR feed failed")
                    self._error(f"ASR failed on a chunk: {e}")
                failing, segs = True, []
            for raw in segs or []:
                self._emit_segment(m, raw)
        try:
            for raw in await self._worker.run(asr.flush) or []:
                self._emit_segment(m, raw)
        except Exception as e:
            self._error(f"ASR flush failed: {e}")

    def _emit_segment(self, m: Meeting, raw: dict) -> None:
        text = str(raw.get("text", "")).strip()
        if not text:
            return
        speaker = int(raw.get("speaker", 0))
        start = float(raw.get("start", 0.0))
        seg: Segment = {"id": len(m.transcript), "speaker": speaker, "name": m.speakers.get(speaker),
                        "text": text, "start": start, "end": float(raw.get("end", start))}
        if m.audio_t0 is not None:
            self._latency = round(time.time() - (m.audio_t0 + seg["end"]), 3)
        m.transcript.append(seg)
        named = False
        if m.state == "rollcall" and speaker not in m.speakers:
            name = match_rollcall(text, m.participants)
            if name:
                m.speakers[speaker] = name
                m.relabel()
                named = True
        if self._transcript_fh is not None:
            self._transcript_fh.write(json.dumps(seg, ensure_ascii=False) + "\n")
            self._transcript_fh.flush()
        self.publish("segment", segment=dict(seg))
        if named:
            self.publish("speakers", map={str(k): v for k, v in m.speakers.items()})
            self._persist()

    async def _heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self.heartbeat_interval)
            self.bus.publish(self.state_event())

    async def _notes_loop(self) -> None:
        while True:
            await asyncio.sleep(self.notes_interval)
            if self.state != "live":
                return
            await self._run_notes()

    async def _run_notes(self) -> None:
        m = self.meeting
        assert m is not None and self._notes_lock is not None
        async with self._notes_lock:
            n = len(m.transcript)
            if n == 0 or n == self._notes_done_n:
                return
            try:
                notes = await self.llm().update_notes([dict(s) for s in m.transcript], m.notes,
                                                      list(m.agenda_items), m.user_notes)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("update_notes failed")
                self._error(f"Notes update failed: {e}")
                return
            self._notes_done_n = n
            self._apply_notes(m, notes if isinstance(notes, dict) else {})

    def _apply_notes(self, m: Meeting, notes: dict) -> None:
        merged = empty_notes() | notes
        m.notes = merged
        cur = merged.get("agenda_current")
        if (isinstance(cur, int) and m.agenda_current is not None
                and m.agenda_current < cur < len(m.agenda_items)):
            m.agenda_current = cur
            m.agenda_started_at[cur] = m.elapsed()
            self.publish("agenda", **m.agenda_dict())
        write_json_atomic(m.dir / "notes.json", merged)
        self._persist()
        self.publish("notes", notes=merged)
        for claim in merged.get("claims") or []:
            key = " ".join(str(claim.get("text", "")).lower().split()) if isinstance(claim, dict) else ""
            if not key or key in self._seen_claims:
                continue
            self._seen_claims.add(key)
            task = asyncio.create_task(self._check_claim(m, claim))
            self._fc_tasks.add(task)
            task.add_done_callback(self._fc_tasks.discard)

    async def _check_claim(self, m: Meeting, claim: dict) -> None:
        assert self._fc_sem is not None
        async with self._fc_sem:
            try:
                item = await self.factcheck().check_claim(claim, self.llm().complete)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.exception("check_claim failed")
                self._error(f"Fact-check failed for {claim.get('text', '')!r}: {e}")
                return
        m.factchecks.append(item)
        write_json_atomic(m.dir / "factchecks.json", m.factchecks)
        if self.meeting is m:
            self._persist()
        self.publish("factcheck", item=item)

    def _require_meeting(self) -> Meeting:
        if self.meeting is None:
            raise MeetingError("no meeting")
        return self.meeting

    def set_speaker(self, speaker: int, name: str) -> dict:
        m = self._require_meeting()
        name = name.strip()
        if name:
            m.speakers[int(speaker)] = name
        else:
            m.speakers.pop(int(speaker), None)
        m.relabel()
        if m.state == "ended":
            write_text_atomic(m.dir / "transcript.jsonl",
                              "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in m.transcript))
        self._persist()
        mapping = {str(k): v for k, v in m.speakers.items()}
        self.publish("speakers", map=mapping)
        return mapping

    def add_bookmark(self, label: str | None = None) -> dict:
        m = self._require_meeting()
        if m.state not in ACTIVE_STATES:
            raise MeetingError("no meeting is running")
        bm = {"id": len(m.bookmarks) + 1, "at": m.elapsed(), "label": (label or "").strip()}
        m.bookmarks.append(bm)
        self._persist()
        self.publish("bookmark", **bm)
        return bm

    def set_user_notes(self, text: str) -> None:
        m = self._require_meeting()
        m.user_notes = text
        self._persist()
        self.publish("user_notes", text=text)

    def set_agenda(self, items: list[str] | None = None, current: int | None = None) -> dict:
        m = self._require_meeting()
        if items is not None:
            items = [i.strip() for i in items if i and i.strip()]
            old = m.agenda_started_at
            m.agenda_items = items
            m.agenda_started_at = [old[i] if i < len(old) else None for i in range(len(items))]
            if not items:
                m.agenda_current = None
            elif m.agenda_current is None or m.agenda_current >= len(items):
                m.agenda_current = 0 if m.agenda_current is None else len(items) - 1
        if current is not None:
            if not 0 <= current < len(m.agenda_items):
                raise MeetingError("agenda index out of range", status=400)
            m.agenda_current = current
        if m.agenda_current is not None and m.agenda_started_at[m.agenda_current] is None:
            m.agenda_started_at[m.agenda_current] = m.elapsed()
        self._persist()
        agenda = m.agenda_dict()
        self.publish("agenda", **agenda)
        return agenda

    def meeting_dir(self, meeting_id: str) -> Path:
        if not _MEETING_ID_RE.match(meeting_id) or ".." in meeting_id:
            raise MeetingError("invalid meeting id", status=404)
        return self.meetings_dir / meeting_id

    def load_meeting(self, meeting_id: str) -> dict | None:
        if self.meeting is not None and self.meeting.id == meeting_id:
            return self.meeting.to_dict()
        path = self.meeting_dir(meeting_id) / "meeting.json"
        if not path.is_file():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("transcript"):
            tpath = path.parent / "transcript.jsonl"
            if tpath.is_file():
                data["transcript"] = [json.loads(line) for line in tpath.read_text(encoding="utf-8").splitlines() if line.strip()]
        return data

    def list_meetings_from_disk(self) -> list[dict]:
        out = []
        for path in sorted(self.meetings_dir.glob("*/meeting.json"), reverse=True):
            try:
                d = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            out.append({k: d.get(k) for k in ("id", "title", "started", "ended", "participants")} | {"duration": None})
        return out

    async def regenerate_minutes(self, meeting_id: str, template: str) -> str:
        if self.meeting is not None and self.meeting.id == meeting_id and self.meeting.state not in ("ended", "idle"):
            raise MeetingError("meeting is still running")
        data = self.load_meeting(meeting_id)
        if data is None:
            raise MeetingError("meeting not found", status=404)
        mdir = self.meeting_dir(meeting_id)
        markdown = await self.llm().write_minutes(data, template)
        write_text_atomic(mdir / "minutes.md", markdown)
        data["minutes"], data["template"] = markdown, template
        if self.meeting is not None and self.meeting.id == meeting_id:
            self.meeting.minutes, self.meeting.template = markdown, template
            self._persist()
        else:
            write_json_atomic(mdir / "meeting.json", data)
        self.publish("minutes", markdown=markdown, template=template, meeting_id=meeting_id)
        try:
            await asyncio.to_thread(self.store().index_meeting, data)
        except Exception as e:
            self._error(f"Indexing meeting failed: {e}")
        return markdown

    async def build_context(self, question: str, scope: str) -> str:
        m = self.meeting
        if scope == "all":
            past = await asyncio.to_thread(self.store().context_for, question)
            parts = [f"Past meetings:\n{past}"] if past else []
            if m is not None and m.state in ACTIVE_STATES:
                parts.append(f"Current meeting ({m.title}, live):\n{format_transcript(m.transcript, CONTEXT_CHARS // 2)}")
            return "\n\n".join(parts)
        if m is None:
            return ""
        return f"Meeting: {m.title}\n{format_transcript(m.transcript)}"

    def ask(self, question: str, scope: str = "meeting") -> str:
        ask_id = uuid.uuid4().hex[:12]
        self.publish("ask_start", ask_id=ask_id, question=question, scope=scope)
        self._spawn(self._ask(ask_id, question, scope))
        return ask_id

    async def _ask(self, ask_id: str, question: str, scope: str) -> None:
        try:
            context = await self.build_context(question, scope)
            stream = self.llm().ask(question, context)
            if inspect.isawaitable(stream):
                stream = await stream
            async for token in stream:
                if token:
                    self.publish("ask_delta", ask_id=ask_id, text=token)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            log.exception("ask failed")
            self.publish("error", message=f"Ask failed: {e}", ask_id=ask_id)
        finally:
            self.publish("ask_done", ask_id=ask_id)
