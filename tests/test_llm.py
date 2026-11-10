import asyncio
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import llm  # noqa: E402

SCRIPT = Path(__file__).resolve().parents[1] / "data" / "samples" / "meeting5.script.txt"
AGENDA = ["Release plan for version two", "Budget", "Infrastructure choice"]


def script_segments() -> list[dict]:
    speakers: dict[str, int] = {}
    segs: list[dict] = []
    t = 0.0
    for line in SCRIPT.read_text(encoding="utf-8").splitlines():
        name, sep, text = line.partition(": ")
        if not sep:
            continue
        sentences = re.split(r"(?<=[.!?])\s+", text.strip())
        parts = [" ".join(sentences[i:i + 2]) for i in range(0, len(sentences), 2)]
        spk = speakers.setdefault(name, len(speakers))
        for part in parts:
            dur = max(1.5, len(part.split()) / 2.6)
            segs.append({"id": len(segs), "speaker": spk, "name": name, "text": part,
                         "start": round(t, 2), "end": round(t + dur, 2)})
            t += dur + 0.3
    return segs


def context_of(segs: list[dict]) -> str:
    lines = [f"[{llm.fmt_ts(s['start'])}] {s['name']}: {s['text']}" for s in segs]
    return "Meeting: Release planning\n" + "\n".join(lines)


async def collect(stream) -> str:
    return "".join([tok async for tok in stream])


def test_templates_lists_four():
    ids = [t["id"] for t in llm.templates()]
    assert ids == ["standup", "decision-log", "client", "brainstorm"]
    assert all(t["name"] for t in llm.templates())


def test_update_notes_on_sample_meeting():
    segs = script_segments()
    assert 35 <= len(segs) <= 50
    notes = asyncio.run(llm.update_notes(segs, {}, AGENDA, ""))

    assert {"people", "decisions", "actions", "topics", "agenda_current", "claims"} <= notes.keys()
    assert notes["_upto"] == segs[-1]["id"]
    assert len(notes["people"]) >= 3
    owned = [a for a in notes["actions"] if a["owner"] in {"Anna", "Ben", "Chloe", "David", "Emma"}]
    assert len(owned) >= 2, notes["actions"]
    assert len(notes["decisions"]) >= 1
    claims = notes["claims"]
    assert len(claims) >= 2, claims
    assert any(re.search(r"everest|great wall", c["text"], re.I) for c in claims), claims
    assert all(isinstance(c["at"], float) and c["speaker"] for c in claims)

    again = asyncio.run(llm.update_notes(segs, notes, AGENDA, ""))
    assert again["claims"] == [] and again["_upto"] == notes["_upto"]
    assert again["actions"] == notes["actions"]


def test_ask_is_grounded_and_cites():
    answer = asyncio.run(collect(llm.ask("Who drafts the budget?", context_of(script_segments()))))
    assert "Ben" in answer, answer
    assert re.search(r"\[\d\d:\d\d\]", answer), answer


def test_write_minutes_decision_log():
    segs = script_segments()
    notes = {"people": {"Ben": ["Will draft the budget by Tuesday"]},
             "decisions": ["Ship version two on Friday the 24th"],
             "actions": [{"owner": "Ben", "task": "Draft the budget", "due": "Tuesday"}],
             "topics": ["Release plan"], "agenda_current": 2, "claims": []}
    typed = "Remember to loop in finance about the zebra-42 budget line"
    meeting = {
        "id": "test", "title": "Release planning", "started": "2026-10-16T09:00:00+02:00",
        "participants": ["Anna", "Ben", "Chloe", "David", "Emma"],
        "agenda": {"items": AGENDA, "current": 2, "started_at": [0.0, None, None]},
        "speakers": {}, "transcript": segs, "notes": notes,
        "factchecks": [{"id": "x", "claim": "Mount Everest is about 6,000 metres high", "speaker": "Anna",
                        "at": 70.0, "verdict": "disputed", "summary": "Everest is 8,849 m.", "sources": []}],
        "bookmarks": [{"id": 1, "at": 95.0, "label": "Kubernetes freeze agreed"}],
        "user_notes": typed,
    }
    md = asyncio.run(llm.write_minutes(meeting, "decision-log"))
    assert md.startswith("# Release planning")
    assert "- [ ] **Ben**: Draft the budget (Tuesday)" in md
    assert typed in md
    assert "[01:35] Kubernetes freeze agreed" in md
    assert "Disputed" in md
