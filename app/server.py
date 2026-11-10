"""FastAPI app: WebSocket hub, REST routes, static web/ (see docs/CONTRACT.md)."""

from __future__ import annotations

import asyncio
import importlib
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

import anyio
from fastapi import Depends, FastAPI, HTTPException, Request, WebSocket
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.requests import HTTPConnection

from app import config
from app.session import MeetingError, SessionManager

log = logging.getLogger(__name__)

LOCAL_HOSTS = frozenset({"127.0.0.1", "::1", "::ffff:127.0.0.1"})

PLACEHOLDER_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>tablescribe</title></head>
<body><h1>tablescribe</h1><p>The web UI (web/index.html) is not installed yet.
The API is live: <a href="/api/state">/api/state</a>.</p></body></html>
"""


def is_local(conn: HTTPConnection) -> bool:
    return conn.client is not None and conn.client.host in LOCAL_HOSTS


def require_control(request: Request) -> None:
    if not is_local(request):
        raise HTTPException(status_code=403, detail="control is local-only")


class _OptionalStaticFiles(StaticFiles):
    async def check_config(self) -> None:
        if self.directory is not None and Path(self.directory).is_dir():
            await super().check_config()


class StartBody(BaseModel):
    title: str = ""
    participants: list[str] = Field(default_factory=list)
    hotwords: list[str] = Field(default_factory=list)
    agenda: list[str] = Field(default_factory=list)
    template: str = "standup"


class SpeakerBody(BaseModel):
    speaker: int
    name: str


class BookmarkBody(BaseModel):
    label: str | None = None


class UserNotesBody(BaseModel):
    text: str


class AgendaBody(BaseModel):
    items: list[str] | None = None
    current: int | None = None


class AskBody(BaseModel):
    question: str
    scope: Literal["meeting", "all"] = "meeting"


class MinutesBody(BaseModel):
    template: str = "standup"


def create_app(manager: SessionManager | None = None) -> FastAPI:
    mgr = manager if manager is not None else SessionManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        try:
            await asyncio.to_thread(mgr.init_store)
        except Exception:
            log.exception("store init failed; history search unavailable")
        warm = asyncio.create_task(mgr.warm_up())
        try:
            yield
        finally:
            warm.cancel()
            await mgr.shutdown()

    app = FastAPI(title="tablescribe", lifespan=lifespan)
    app.state.manager = mgr
    control = [Depends(require_control)]

    @app.exception_handler(MeetingError)
    async def _meeting_error(request: Request, exc: MeetingError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    @app.websocket("/ws")
    async def ws(websocket: WebSocket) -> None:
        await websocket.accept()
        q = mgr.bus.subscribe()
        try:
            await websocket.send_json({"type": "snapshot", **mgr.snapshot(), "can_control": is_local(websocket)})

            async with anyio.create_task_group() as tg:

                async def forward() -> None:
                    try:
                        while True:
                            await websocket.send_json(await q.get())
                    except Exception as e:
                        log.debug("websocket send failed: %r", e)
                    tg.cancel_scope.cancel()

                tg.start_soon(forward)
                try:
                    while (await websocket.receive())["type"] != "websocket.disconnect":
                        pass
                except Exception as e:
                    log.debug("websocket receive failed: %r", e)
                tg.cancel_scope.cancel()
        except Exception as e:
            log.debug("websocket closed: %r", e)
        finally:
            mgr.bus.unsubscribe(q)

    @app.post("/api/meeting/start", dependencies=control)
    async def start(body: StartBody) -> dict:
        m = await mgr.start(title=body.title, participants=body.participants, hotwords=body.hotwords,
                            agenda=body.agenda, template=body.template)
        return {"meeting_id": m.id, "state": m.state}

    @app.post("/api/meeting/rollcall/done", dependencies=control)
    async def rollcall_done() -> dict:
        await mgr.rollcall_done()
        return {"state": mgr.state}

    @app.post("/api/meeting/stop", dependencies=control)
    async def stop() -> dict:
        if mgr.state not in ("rollcall", "live"):
            raise MeetingError("no meeting is running")
        mgr._spawn(mgr.stop())
        await asyncio.sleep(0)
        return {"state": mgr.state}

    @app.post("/api/speakers", dependencies=control)
    async def speakers(body: SpeakerBody) -> dict:
        return {"map": mgr.set_speaker(body.speaker, body.name)}

    @app.post("/api/bookmark", dependencies=control)
    async def bookmark(body: BookmarkBody | None = None) -> dict:
        return mgr.add_bookmark(body.label if body else None)

    @app.post("/api/user-notes", dependencies=control)
    async def user_notes(body: UserNotesBody) -> dict:
        mgr.set_user_notes(body.text)
        return {"ok": True}

    @app.post("/api/agenda", dependencies=control)
    async def agenda(body: AgendaBody) -> dict:
        return mgr.set_agenda(body.items, body.current)

    @app.post("/api/ask")
    async def ask(body: AskBody) -> dict:
        question = body.question.strip()
        if not question:
            raise HTTPException(status_code=400, detail="question is empty")
        return {"ask_id": mgr.ask(question, body.scope)}

    @app.get("/api/state")
    async def state(request: Request) -> dict:
        ev = mgr.state_event()
        return {**mgr.snapshot(), "elapsed": ev["elapsed"], "rtf": ev["rtf"], "latency": ev["latency"],
                "can_control": is_local(request)}

    @app.get("/api/meetings")
    async def meetings() -> list[dict]:
        try:
            return await asyncio.to_thread(mgr.store().list_meetings)
        except Exception:
            log.warning("store.list_meetings failed; listing from disk", exc_info=True)
            return mgr.list_meetings_from_disk()

    def _found(meeting_id: str) -> Path:
        mdir = mgr.meeting_dir(meeting_id)
        if not mdir.is_dir():
            raise HTTPException(status_code=404, detail="meeting not found")
        return mdir

    @app.get("/api/meetings/{meeting_id}")
    async def meeting(meeting_id: str) -> dict:
        data = mgr.load_meeting(meeting_id)
        if data is None:
            raise HTTPException(status_code=404, detail="meeting not found")
        return data

    @app.get("/api/meetings/{meeting_id}/audio")
    async def audio(meeting_id: str) -> FileResponse:
        path = _found(meeting_id) / "audio.wav"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no recording")
        return FileResponse(path, media_type="audio/wav")

    @app.get("/api/meetings/{meeting_id}/minutes.md")
    async def minutes_md(meeting_id: str) -> Response:
        path = _found(meeting_id) / "minutes.md"
        if not path.is_file():
            raise HTTPException(status_code=404, detail="no minutes yet")
        return Response(path.read_text(encoding="utf-8"), media_type="text/markdown; charset=utf-8")

    @app.post("/api/meetings/{meeting_id}/minutes", dependencies=control)
    async def regenerate(meeting_id: str, body: MinutesBody) -> dict:
        markdown = await mgr.regenerate_minutes(meeting_id, body.template)
        return {"markdown": markdown, "template": body.template}

    @app.get("/api/search")
    async def search(q: str = "", limit: int = 20) -> list[dict]:
        if not q.strip():
            return []
        return await asyncio.to_thread(mgr.store().search, q, limit)

    @app.get("/api/templates")
    async def templates() -> list[dict]:
        return mgr.llm().templates()

    @app.get("/api/devices")
    async def devices() -> list[dict]:
        return await asyncio.to_thread(importlib.import_module("app.audio").list_input_devices)

    @app.get("/", response_model=None)
    async def index() -> FileResponse | HTMLResponse:
        page = config.WEB_DIR / "index.html"
        if page.is_file():
            return FileResponse(page, media_type="text/html")
        return HTMLResponse(PLACEHOLDER_HTML)

    app.mount("/static", _OptionalStaticFiles(directory=config.WEB_DIR, check_dir=False), name="static")
    return app


app = create_app()
