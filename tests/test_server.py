from __future__ import annotations

import json

import numpy as np
import pytest
import soundfile as sf
from fastapi.testclient import TestClient

from app.server import create_app

LOCAL = ("127.0.0.1", 5000)
LAN = ("192.168.1.50", 5000)
MEETING_ID = "20261015-1000-past"


@pytest.fixture
def manager(make_manager):
    mgr, _, _ = make_manager()
    mdir = mgr.meetings_dir / MEETING_ID
    mdir.mkdir(parents=True)
    sf.write(mdir / "audio.wav", np.zeros(24000, dtype=np.float32), 24000, subtype="PCM_16")
    (mdir / "minutes.md").write_text("# Past minutes\n")
    (mdir / "meeting.json").write_text(json.dumps({"id": MEETING_ID, "title": "Past", "started": "2026-10-15T10:00:00",
                                                   "ended": "2026-10-15T10:30:00", "state": "ended",
                                                   "participants": [], "transcript": [], "minutes": "# Past minutes\n"}))
    return mgr


@pytest.mark.parametrize("client_addr,local", [(LOCAL, True), (LAN, False)])
def test_control_guard_and_public_routes(manager, client_addr, local):
    with TestClient(create_app(manager), client=client_addr) as c:
        r = c.post("/api/meeting/stop")
        if local:
            assert r.status_code == 409
        else:
            assert r.status_code == 403 and r.json() == {"detail": "control is local-only"}
        for path, body in [("/api/meeting/start", {"title": "x"}), ("/api/meeting/rollcall/done", None),
                           ("/api/speakers", {"speaker": 0, "name": "A"}), ("/api/bookmark", {}),
                           ("/api/user-notes", {"text": "t"}), ("/api/agenda", {"items": ["a"]}),
                           (f"/api/meetings/{MEETING_ID}/minutes", {"template": "standup"})]:
            if not local:
                assert c.post(path, json=body).status_code == 403, path
        assert c.get("/").status_code == 200
        state = c.get("/api/state").json()
        assert state["can_control"] is local and state["state"] == "idle" and state["meeting"] is None
        assert c.get("/api/templates").json() == [{"id": "standup", "name": "Standup"}]
        assert c.get(f"/api/meetings/{MEETING_ID}").json()["title"] == "Past"
        assert c.get("/api/meetings/nope").status_code == 404
        md = c.get(f"/api/meetings/{MEETING_ID}/minutes.md")
        assert md.status_code == 200 and md.headers["content-type"].startswith("text/markdown")
        with c.websocket_connect("/ws") as ws:
            snap = ws.receive_json()
        assert snap["type"] == "snapshot" and snap["state"] == "idle" and snap["can_control"] is local


def test_audio_range_request(manager):
    with TestClient(create_app(manager), client=LAN) as c:
        full = c.get(f"/api/meetings/{MEETING_ID}/audio")
        assert full.status_code == 200 and full.headers["content-type"] == "audio/wav"
        r = c.get(f"/api/meetings/{MEETING_ID}/audio", headers={"Range": "bytes=0-99"})
        assert r.status_code == 206
        assert len(r.content) == 100 and r.content[:4] == b"RIFF"
        assert r.headers["content-range"] == f"bytes 0-99/{len(full.content)}"


def test_ask_streams_over_ws(manager):
    with TestClient(create_app(manager), client=LAN) as c:
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["type"] == "snapshot"
            ask_id = c.post("/api/ask", json={"question": "What grew?", "scope": "all"}).json()["ask_id"]
            events = []
            while True:
                ev = ws.receive_json()
                if ev.get("ask_id") != ask_id:
                    continue
                events.append(ev)
                if ev["type"] == "ask_done":
                    break
    assert events[0] == {"type": "ask_start", "ask_id": ask_id, "question": "What grew?", "scope": "all"}
    assert "".join(e["text"] for e in events if e["type"] == "ask_delta") == "Revenue grew [00:03]."
    assert "past meeting snippet" in manager.llm().calls.ask[0][1]


def test_local_start_stop_over_http(manager):
    with TestClient(create_app(manager), client=LOCAL) as c:
        r = c.post("/api/meeting/start", json={"title": "Live", "participants": ["Anna"]})
        assert r.status_code == 200 and r.json()["state"] == "rollcall"
        assert c.post("/api/meeting/start", json={"title": "again"}).status_code == 409
        assert c.post("/api/bookmark", json={"label": "key point"}).json()["label"] == "key point"
        with c.websocket_connect("/ws") as ws:
            assert ws.receive_json()["meeting"]["title"] == "Live"
            assert c.post("/api/meeting/stop").status_code == 200
            while (ev := ws.receive_json())["type"] != "state" or ev["state"] != "ended":
                pass
        assert c.get("/api/state").json()["state"] == "ended"
