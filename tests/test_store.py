import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import store  # noqa: E402


def _meeting(mid, title, started, ended, participants, transcript, minutes):
    return {
        "id": mid,
        "title": title,
        "started": started,
        "ended": ended,
        "participants": participants,
        "transcript": transcript,
        "minutes": minutes,
        "notes": {},
    }


BUDGET = _meeting(
    "20260901-0900-budget",
    "Budget review",
    "2026-09-01T09:00:00",
    "2026-09-01T09:30:00",
    ["Ana", "Ben"],
    [
        {"id": 1, "speaker": 0, "name": "Ana", "text": "We need to cut the travel budget for Q3.", "start": 75.5, "end": 80.0},
        {"id": 2, "speaker": 1, "name": "Ben", "text": "Agreed, the hardware line is fine.", "start": 90.0, "end": 95.0},
    ],
    "Decision: cut travel budget.",
)

LAUNCH = _meeting(
    "20261001-1400-launch",
    "Launch planning",
    "2026-10-01T14:00:00",
    None,
    ["Ana"],
    [
        {"id": 1, "speaker": 0, "name": None, "text": "The lighthouse banner ships next week.", "start": 12.0, "end": 14.0},
    ],
    None,
)


def _init(tmp_path):
    store.init(tmp_path / "data" / "history.db")


def test_search_finds_transcript_hit_with_start(tmp_path):
    _init(tmp_path)
    store.index_meeting(BUDGET)
    store.index_meeting(LAUNCH)

    hits = store.search("lighthouse")

    assert len(hits) == 1
    assert hits[0]["meeting_id"] == LAUNCH["id"]
    assert hits[0]["title"] == "Launch planning"
    assert hits[0]["start"] == 12.0
    assert "<mark>lighthouse</mark>" in hits[0]["snippet"]


def test_minutes_hit_has_null_start(tmp_path):
    _init(tmp_path)
    store.index_meeting(BUDGET)

    hits = store.search("decision")

    assert len(hits) == 1
    assert hits[0]["meeting_id"] == BUDGET["id"]
    assert hits[0]["start"] is None


def test_prefix_match_on_last_token(tmp_path):
    _init(tmp_path)
    store.index_meeting(LAUNCH)

    assert [h["meeting_id"] for h in store.search("light")] == [LAUNCH["id"]]


def test_punctuation_heavy_query_does_not_raise(tmp_path):
    _init(tmp_path)
    store.index_meeting(BUDGET)

    assert isinstance(store.search('what\'s "budget" (Q3)?'), list)
    assert [h["meeting_id"] for h in store.search('"budget" (Q3)?')] == [BUDGET["id"]]


def test_reindex_does_not_duplicate(tmp_path):
    _init(tmp_path)
    store.index_meeting(BUDGET)
    store.index_meeting(BUDGET)

    assert len(store.search("budget")) == 2
    assert len(store.list_meetings()) == 1


def test_list_meetings_newest_first(tmp_path):
    _init(tmp_path)
    store.index_meeting(BUDGET)
    store.index_meeting(LAUNCH)

    listed = store.list_meetings()

    assert [m["id"] for m in listed] == [LAUNCH["id"], BUDGET["id"]]
    assert listed[1]["duration"] == 1800.0
    assert listed[1]["participants"] == ["Ana", "Ben"]
    assert listed[0]["duration"] is None


def test_context_for_formats_timestamped_lines(tmp_path):
    _init(tmp_path)
    store.index_meeting(LAUNCH)

    assert store.context_for("lighthouse") == (
        "[Launch planning 2026-10-01 00:12] Speaker 0: The lighthouse banner ships next week."
    )
