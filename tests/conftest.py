from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.session import SessionManager  # noqa: E402

SR = 24000
BLOCK = 2400


class FakeASR:
    sample_rate = SR
    chunk_seconds = 2.93
    last_rtf = 0.25

    def __init__(self, script: list[dict], tail: list[dict] | None = None):
        self.script = list(script)
        self.tail = list(tail or [])
        self.samples = 0
        self.context: list[str] = []

    def set_context(self, hotwords: list[str]) -> None:
        self.context = list(hotwords)

    def feed(self, pcm: np.ndarray) -> list[dict]:
        self.samples += len(pcm)
        now = self.samples / SR
        out = [s for s in self.script if s["end"] <= now]
        self.script = [s for s in self.script if s["end"] > now]
        return out

    def flush(self) -> list[dict]:
        out = self.script + self.tail
        self.script, self.tail = [], []
        return out


class FakeSource:
    """Yields `phase1` blocks, waits for `gate`, yields `phase2` blocks, then idles like a live mic."""

    def __init__(self, phase1: int, phase2: int):
        self.phase1, self.phase2 = phase1, phase2
        self.gate = asyncio.Event()
        self.closed = False

    async def blocks(self):
        for _ in range(self.phase1):
            yield np.zeros(BLOCK, dtype=np.float32)
        await self.gate.wait()
        for _ in range(self.phase2):
            yield np.zeros(BLOCK, dtype=np.float32)
        await asyncio.Event().wait()

    def close(self) -> None:
        self.closed = True


class FakeRecorder:
    def __init__(self, path: Path):
        self.path = path
        self.samples = 0
        self.closed = False
        path.write_bytes(b"")

    def write(self, pcm: np.ndarray) -> None:
        self.samples += len(pcm)

    def close(self) -> None:
        self.closed = True


def make_llm(notes_results: list | None = None):
    calls = SimpleNamespace(update_notes=0, write_minutes=[], ask=[])

    async def update_notes(transcript, prev, agenda, user_notes):
        calls.update_notes += 1
        if notes_results:
            r = notes_results.pop(0)
            if isinstance(r, Exception):
                raise r
            return r
        return {"people": {}, "decisions": [], "actions": [], "topics": [], "agenda_current": None, "claims": []}

    async def write_minutes(meeting, template):
        calls.write_minutes.append((meeting, template))
        return f"# Minutes: {meeting['title']}\n\n{len(meeting['transcript'])} segments\n"

    async def complete(messages, *, json_schema=None, stream=False):
        return "{}"

    async def ask(question, context):
        calls.ask.append((question, context))
        for tok in ("Revenue ", "grew [00:03]."):
            yield tok

    def templates():
        return [{"id": "standup", "name": "Standup"}]

    return SimpleNamespace(update_notes=update_notes, write_minutes=write_minutes, complete=complete,
                           ask=ask, templates=templates, calls=calls)


def make_factcheck():
    seen: list[dict] = []

    async def check_claim(claim, complete):
        seen.append(claim)
        return {"id": f"fc{len(seen)}", "claim": claim["text"], "speaker": claim.get("speaker"),
                "at": claim.get("at", 0.0), "verdict": "supported", "summary": "ok", "sources": []}

    return SimpleNamespace(check_claim=check_claim, seen=seen)


def make_store():
    indexed: list[dict] = []
    return SimpleNamespace(init=lambda db: None, index_meeting=indexed.append, indexed=indexed,
                           search=lambda q, limit=20: [], list_meetings=lambda: [],
                           context_for=lambda q, limit=12: "past meeting snippet")


@pytest.fixture
def make_manager(tmp_path):
    def factory(*, asr: FakeASR | None = None, source: FakeSource | None = None, llm=None, **kw):
        asr = asr or FakeASR([])
        source = source or FakeSource(0, 0)
        mgr = SessionManager(
            meetings_dir=tmp_path / "meetings", index_db=tmp_path / "index.db",
            asr_factory=lambda: asr, llm=llm or make_llm(), factcheck=make_factcheck(), store=make_store(),
            source_factory=lambda spec: source, recorder_factory=FakeRecorder,
            notes_interval=kw.pop("notes_interval", 3600), rollcall_timeout=kw.pop("rollcall_timeout", 3600),
            heartbeat_interval=kw.pop("heartbeat_interval", 3600), **kw,
        )
        return mgr, asr, source
    return factory
