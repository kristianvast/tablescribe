from __future__ import annotations

import asyncio
import json

import pytest

from app.session import EventBus, format_transcript, match_rollcall
from conftest import FakeASR, FakeSource, make_llm


async def next_event(q: asyncio.Queue, log: list, pred, timeout: float = 5.0) -> dict:
    async def scan():
        while True:
            ev = await q.get()
            log.append(ev)
            if pred(ev):
                return ev
    return await asyncio.wait_for(scan(), timeout)


def state_sequence(events: list[dict]) -> list[str]:
    seq: list[str] = []
    for ev in events:
        if ev["type"] == "state" and (not seq or seq[-1] != ev["state"]):
            seq.append(ev["state"])
    return seq


@pytest.mark.parametrize("text,participants,expected", [
    ("Hi, I'm Anna.", [], "Anna"),
    ("I am Ben and I run sales", [], "Ben"),
    ("my name is Clara", [], "Clara"),
    ("Anna here.", [], "Anna"),
    ("Hello, Dev here", [], "Dev"),
    ("This is Erik Lund speaking", [], "Erik Lund"),
    ("i'm anna", ["Anna", "Ben"], "Anna"),
    ("I\u2019m Ben", ["Anna", "Ben"], "Ben"),
    ("I'm going to share my screen", [], None),
    ("this is the plan for today", [], None),
    ("I'm Not sure", [], None),
    ("Everything here is fine", [], None),
])
def test_match_rollcall(text, participants, expected):
    assert match_rollcall(text, participants) == expected


def test_format_transcript_keeps_tail():
    segs = [{"id": i, "speaker": i % 2, "name": "Anna" if i % 2 == 0 else None, "text": f"line {i}",
             "start": 60.0 * i + 5, "end": 60.0 * i + 6} for i in range(3)]
    full = format_transcript(segs)
    assert full.splitlines() == ["[00:05] Anna: line 0", "[01:05] Speaker 1: line 1", "[02:05] Anna: line 2"]
    assert format_transcript(segs, max_chars=50).splitlines() == ["[01:05] Speaker 1: line 1", "[02:05] Anna: line 2"]


def test_event_bus_drops_oldest():
    async def run():
        bus = EventBus(limit=3)
        q = bus.subscribe()
        for i in range(5):
            bus.publish({"type": "x", "i": i})
        return [q.get_nowait()["i"] for _ in range(q.qsize())]
    assert asyncio.run(run()) == [2, 3, 4]


SCRIPT = [
    {"speaker": 0, "text": "Hi, I'm Anna.", "start": 0.0, "end": 0.5},
    {"speaker": 1, "text": "Ben here.", "start": 0.6, "end": 0.9},
    {"speaker": 0, "text": "Revenue grew 40 percent last year.", "start": 1.2, "end": 1.6},
]
TAIL = [{"speaker": 1, "text": "Let's ship it.", "start": 1.7, "end": 1.9}]
CLAIM = {"text": "Revenue grew 40% last year", "speaker": "Anna", "at": 1.2}


def test_full_meeting_flow(make_manager):
    notes = {"people": {"Anna": ["reported revenue"]}, "decisions": ["ship it"], "actions": [],
             "topics": ["revenue"], "agenda_current": 1, "claims": [CLAIM, dict(CLAIM)]}
    llm = make_llm([notes])
    source = FakeSource(phase1=10, phase2=8)
    mgr, asr, _ = make_manager(asr=FakeASR(SCRIPT, TAIL), source=source, llm=llm)

    async def run():
        q = mgr.bus.subscribe()
        events: list[dict] = []
        m = await mgr.start(title="Q3 Review", participants=["Anna", "Ben"], hotwords=["ARR"],
                            agenda=["Intro", "Numbers", "Next steps"])
        assert mgr.state == "rollcall"
        await next_event(q, events, lambda e: e["type"] == "speakers" and e["map"] == {"0": "Anna", "1": "Ben"})
        await mgr.rollcall_done()
        source.gate.set()
        await next_event(q, events, lambda e: e["type"] == "segment" and e["segment"]["id"] == 2)
        await mgr.stop()
        while not q.empty():
            events.append(q.get_nowait())
        return m, events

    m, events = asyncio.run(run())
    assert asr.context == ["Anna", "Ben", "ARR"]
    assert m.speakers == {0: "Anna", 1: "Ben"}
    assert [s["name"] for s in m.transcript] == ["Anna", "Ben", "Anna", "Ben"]
    assert [s["id"] for s in m.transcript] == [0, 1, 2, 3]

    assert state_sequence(events) == ["rollcall", "live", "stopping", "ended"]
    types = [e["type"] for e in events]
    assert types.index("speakers") < types.index("notes") < types.index("factcheck") < types.index("minutes")
    seg_events = [e["segment"] for e in events if e["type"] == "segment"]
    assert seg_events[0]["name"] == "Anna" and seg_events[-1]["text"] == "Let's ship it."
    assert [e for e in events if e["type"] == "agenda"][-1]["current"] == 1

    assert len(mgr.factcheck().seen) == 1
    assert len(m.factchecks) == 1 and m.factchecks[0]["verdict"] == "supported"

    d = m.dir
    lines = [json.loads(line) for line in (d / "transcript.jsonl").read_text().splitlines()]
    assert [s["text"] for s in lines] == [s["text"] for s in SCRIPT + TAIL]
    assert lines[1]["name"] == "Ben"
    assert (d / "minutes.md").read_text().startswith("# Minutes: Q3 Review")
    saved = json.loads((d / "meeting.json").read_text())
    assert saved["state"] == "ended" and saved["ended"] and saved["minutes"]
    assert saved["speakers"] == {"0": "Anna", "1": "Ben"}
    assert saved["agenda"]["current"] == 1 and saved["agenda"]["started_at"][1] is not None
    assert json.loads((d / "notes.json").read_text())["decisions"] == ["ship it"]
    assert len(json.loads((d / "factchecks.json").read_text())) == 1
    assert mgr.store().indexed and mgr.store().indexed[0]["id"] == m.id
    assert llm.calls.write_minutes[0][1] == "standup"
    assert m.id.endswith("-q3-review")


def test_notes_failure_broadcasts_error_and_meeting_continues(make_manager):
    ok = {"people": {}, "decisions": ["d1"], "actions": [], "topics": [], "agenda_current": None, "claims": []}
    llm = make_llm([RuntimeError("ollama down"), ok])
    source = FakeSource(phase1=10, phase2=0)
    mgr, _, _ = make_manager(asr=FakeASR(SCRIPT), source=source, llm=llm, notes_interval=0.01)

    async def run():
        q = mgr.bus.subscribe()
        events: list[dict] = []
        await mgr.start(title="x")
        await next_event(q, events, lambda e: e["type"] == "segment")
        await mgr.rollcall_done()
        err = await next_event(q, events, lambda e: e["type"] == "error")
        assert mgr.state == "live"
        notes = await next_event(q, events, lambda e: e["type"] == "notes")
        await mgr.stop()
        return err, notes

    err, notes = asyncio.run(run())
    assert "ollama down" in err["message"]
    assert notes["notes"]["decisions"] == ["d1"]
    assert mgr.state == "ended"


def test_restart_after_end_and_manual_rename(make_manager):
    mgr, _, _ = make_manager(asr=FakeASR([{"speaker": 3, "text": "hello", "start": 0, "end": 0.1}]),
                             source=FakeSource(phase1=2, phase2=0))

    async def run():
        q = mgr.bus.subscribe()
        events: list[dict] = []
        first = await mgr.start(title="one")
        await next_event(q, events, lambda e: e["type"] == "segment")
        mgr.set_speaker(3, "Dana")
        assert first.transcript[0]["name"] == "Dana"
        await mgr.stop()
        second = await mgr.start(title="one")
        assert second.id != first.id and mgr.state == "rollcall"
        await mgr.stop()
        return first, second

    first, second = asyncio.run(run())
    assert json.loads((first.dir / "meeting.json").read_text())["speakers"] == {"3": "Dana"}
    assert second.state == "ended"


def test_asr_unavailable_still_records(make_manager):
    source = FakeSource(phase1=5, phase2=0)

    def broken():
        raise ImportError("no app.asr yet")

    mgr, _, _ = make_manager(source=source)
    mgr._asr_factory = broken

    async def run():
        q = mgr.bus.subscribe()
        events: list[dict] = []
        assert await mgr.warm_up() is False
        m = await mgr.start(title="rec only")
        await next_event(q, events, lambda e: e["type"] == "error" and "ASR unavailable" in e["message"])
        await mgr.stop()
        return m

    m = asyncio.run(run())
    assert m.state == "ended" and (m.dir / "minutes.md").exists()
