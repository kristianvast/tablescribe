from __future__ import annotations

import html
import json
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path
from typing import Iterator

_db_path: Path | None = None

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    started      TEXT NOT NULL,
    ended        TEXT,
    duration     REAL,
    participants TEXT NOT NULL
);
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
    body, meeting_id UNINDEXED, kind UNINDEXED, name UNINDEXED, start UNINDEXED,
    tokenize = 'porter unicode61'
);
"""

_TOKEN = re.compile(r"\w+")
# snippet() delimiters: control chars so the text can be HTML-escaped before <mark> is restored
_OPEN, _CLOSE = "\x01", "\x02"


def init(db_path: Path) -> None:
    global _db_path
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
    _db_path = db_path


@contextmanager
def _db() -> Iterator[sqlite3.Connection]:
    if _db_path is None:
        raise RuntimeError("store.init() must be called first")
    conn = sqlite3.connect(_db_path)
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def _seconds_between(started: str, ended: str | None) -> float | None:
    if not ended:
        return None
    return (datetime.fromisoformat(ended) - datetime.fromisoformat(started)).total_seconds()


def index_meeting(meeting: dict) -> None:
    mid = meeting["id"]
    rows: list[tuple] = []
    for seg in meeting.get("transcript") or []:
        text = (seg.get("text") or "").strip()
        if text:
            name = seg.get("name") or f"Speaker {seg.get('speaker')}"
            rows.append((text, mid, "transcript", name, float(seg["start"])))
    minutes = (meeting.get("minutes") or "").strip()
    if minutes:
        rows.append((minutes, mid, "minutes", "Minutes", None))

    with _db() as c:
        c.execute("DELETE FROM docs WHERE meeting_id = ?", (mid,))
        c.execute(
            "INSERT OR REPLACE INTO meetings (id, title, started, ended, duration, participants) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                mid,
                meeting["title"],
                meeting["started"],
                meeting.get("ended"),
                _seconds_between(meeting["started"], meeting.get("ended")),
                json.dumps(meeting.get("participants") or [], ensure_ascii=False),
            ),
        )
        c.executemany(
            "INSERT INTO docs (body, meeting_id, kind, name, start) VALUES (?, ?, ?, ?, ?)", rows
        )


def _match_expr(q: str) -> str | None:
    tokens = _TOKEN.findall(q)
    if not tokens:
        return None
    *head, last = tokens
    return " ".join([f'"{t}"' for t in head] + [f'"{last}"*'])


def _hits(q: str, limit: int) -> list[dict]:
    match = _match_expr(q)
    if match is None:
        return []
    with _db() as c:
        rows = c.execute(
            "SELECT docs.meeting_id, m.title, m.started, docs.name, docs.start, "
            "snippet(docs, 0, char(1), char(2), '...', 12) AS raw, bm25(docs) AS score "
            "FROM docs JOIN meetings m ON m.id = docs.meeting_id "
            "WHERE docs MATCH ? ORDER BY score, m.started DESC LIMIT ?",
            (match, limit * 3),
        ).fetchall()

    seen: set[tuple[str, str]] = set()
    hits: list[dict] = []
    for mid, title, started, name, start, raw, _score in rows:
        plain = raw.replace(_OPEN, "").replace(_CLOSE, "")
        if (mid, plain) in seen:
            continue
        seen.add((mid, plain))
        hits.append(
            {
                "meeting_id": mid,
                "title": title,
                "started": started,
                "name": name,
                "start": None if start is None else float(start),
                "plain": plain,
                "marked": html.escape(raw).replace(_OPEN, "<mark>").replace(_CLOSE, "</mark>"),
            }
        )
        if len(hits) == limit:
            break
    return hits


def search(q: str, limit: int = 20) -> list[dict]:
    return [
        {
            "meeting_id": h["meeting_id"],
            "title": h["title"],
            "started": h["started"],
            "snippet": h["marked"],
            "start": h["start"],
        }
        for h in _hits(q, limit)
    ]


def list_meetings() -> list[dict]:
    with _db() as c:
        rows = c.execute(
            "SELECT id, title, started, ended, duration, participants FROM meetings "
            "ORDER BY started DESC"
        ).fetchall()
    return [
        {
            "id": mid,
            "title": title,
            "started": started,
            "ended": ended,
            "duration": duration,
            "participants": json.loads(participants),
        }
        for mid, title, started, ended, duration, participants in rows
    ]


def _mmss(seconds: float) -> str:
    m, s = divmod(int(seconds), 60)
    return f"{m:02d}:{s:02d}"


def context_for(q: str, limit: int = 12) -> str:
    lines = []
    for h in _hits(q, limit):
        date = h["started"][:10]
        when = date if h["start"] is None else f"{date} {_mmss(h['start'])}"
        lines.append(f"[{h['title']} {when}] {h['name']}: {h['plain']}")
    return "\n".join(lines)
