"""The person's browser, for the assistant (docs/browser-assistant.md): the Lens extension's side panel connects a
session and listens for actions, carries each one out in the browser and sends back what happened.

``POST /browser/sessions`` connects one; ``GET /browser/sessions/{id}/actions`` is its stream of ``action`` events
(server-sent, read with fetch: it needs the Authorization header); ``POST /browser/actions/{id}`` answers one. While the
stream is open, a question sent from the side panel (``browser`` on POST /chats/{cid}/messages) can use the browser, and
an agent can too (``POST /browser/sessions/{id}/run``). Only the session's own person reaches any of it.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Cfg, CurrentUser, Db, Principal, Writer
from app.domain import auth, browser
from app.domain.store import DB
from app.schemas.browser import BrowserAnswer, BrowserConnect, BrowserResult, BrowserRun, BrowserSession
from app.schemas.common import Ok

router = APIRouter(prefix="/browser", tags=["browser"])
OFF = "an admin turned off using a browser for the assistant (Settings → AI assistant)"


def _on(cfg: dict) -> None:
    if not browser.enabled(cfg):
        raise HTTPException(403, OFF)


def _session(db: DB, sid: str, user: Principal) -> dict:
    try:
        return browser.get(db, sid, user.id)
    except KeyError:
        raise HTTPException(404, "not found") from None


@router.post("/sessions", status_code=201)
def connect(user: Writer, db: Db, cfg: Cfg, body: BrowserConnect | None = None) -> BrowserSession:
    """Connect a browser's Lens side panel: then open its action stream. Audited as `browser.connect`."""
    _on(cfg)
    s = browser.connect(db, user.id, (body or BrowserConnect()).label)
    auth.audit(db, user.as_audit(), "browser.connect", f"browser_session:{s['id']}", {"label": s["label"]})
    return BrowserSession(**s)


@router.get("/sessions")
def list_sessions(user: CurrentUser, db: Db) -> list[BrowserSession]:
    """Your browsers whose side panel is connected now, the latest first."""
    return [BrowserSession(**s) for s in browser.mine(db, user.id)]


@router.delete("/sessions/{sid}")
def disconnect(sid: str, user: Writer, db: Db) -> Ok:
    """The side panel closed: the assistant can't use that browser any more, and actions waiting on it fail."""
    _session(db, sid, user)
    browser.end(db, sid)
    return Ok()


@router.get(
    "/sessions/{sid}/actions",
    response_class=StreamingResponse,
    responses={200: {"content": {"text/event-stream": {}}, "description": "`event: action` with {id, tool, args, chat}"}},
)
async def actions(sid: str, request: Request, user: Writer, cfg: Cfg, once: bool = False) -> StreamingResponse:
    """The actions the assistant (or an agent) asks of this browser, as server-sent `action` events {id, tool, args,
    chat}: carry each one out and answer it with POST /browser/actions/{id}. The session stays connected while this
    stream is open; a comment line every 15 seconds keeps it so through proxies. `once=true` sends the actions waiting
    now and closes, for a client that asks again and again instead (each time keeps it connected for a while)."""
    _on(cfg)
    db = request.app.state.db
    await run_in_threadpool(_session, db, sid, user)
    await run_in_threadpool(browser.seen, db, sid)

    async def gen() -> AsyncIterator[str]:
        yield ": open\n\n"
        beat = quiet = time.monotonic()
        while True:
            for a in await run_in_threadpool(browser.take, db, sid):
                quiet = time.monotonic()
                yield f"event: action\ndata: {json.dumps(a, default=str)}\n\n"
            if once:
                break
            now = time.monotonic()
            if now - beat >= browser.BEAT_SECONDS:
                beat = now
                await run_in_threadpool(browser.seen, db, sid)
            if now - quiet >= 15:
                quiet = now
                yield ": keep-alive\n\n"
            if await request.is_disconnected():
                break
            await asyncio.sleep(browser.POLL_SECONDS)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/actions/{aid}")
def answer_action(aid: str, body: BrowserAnswer, user: Writer, db: Db) -> Ok:
    """What the browser did with an action: its result, or why it didn't (the person said no, the site isn't allowed...).
    404 for an action that isn't one of your browser's, or that's been answered or given up on."""
    try:
        sid = browser.session_of(db, aid)
        _session(db, sid, user)
        browser.answer(db, sid, aid, body.ok, body.result, body.error)
    except KeyError:
        raise HTTPException(404, "not found") from None
    return Ok()


@router.post("/sessions/{sid}/run")
def run(sid: str, body: BrowserRun, user: Writer, db: Db, cfg: Cfg) -> BrowserResult:
    """Do one thing in your connected browser, as the assistant's browser_ tools do, and wait for it: for agents and
    scripts. The side panel shows it and asks you first, as it does for the assistant. Audited as `browser.run`."""
    _on(cfg)
    _session(db, sid, user)
    auth.audit(db, user.as_audit(), "browser.run", f"browser_session:{sid}", {"tool": body.tool})
    try:
        out = browser.run(db, sid, user.id, body.tool, body.args, wait=body.wait)
    except browser.BrowserError as e:
        return BrowserResult(ok=False, error=str(e))
    return BrowserResult(ok=True, result=out)
