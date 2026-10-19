"""Circles between Lens servers over ActivityPub (app/domain/federation.py, docs/federation.md): WebFinger, each
findable person's actor and (always empty) outbox, and their signed inbox. Everything answers 404 while federation is
off, and for anyone who isn't findable."""

from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response
from starlette.concurrency import run_in_threadpool

from app.api.deps import Cfg, Db
from app.domain import federation

router = APIRouter(include_in_schema=False, tags=["federation"])
AP = f"{federation.AP}; charset=utf-8"


def _json(doc, media=AP, status=200):
    return Response(json.dumps(doc), status_code=status, media_type=media, headers={"Vary": "Accept"})


@router.get("/.well-known/webfinger")
def webfinger(db: Db, cfg: Cfg, resource: str = Query(..., max_length=300)) -> Response:
    try:
        return _json(federation.webfinger(db, cfg, resource), f"{federation.JRD}; charset=utf-8")
    except KeyError:
        raise HTTPException(404, "nobody by that handle here") from None


@router.get("/ap/users/{handle}")
def actor(handle: str, db: Db, cfg: Cfg) -> Response:
    try:
        return _json(federation.actor(db, cfg, handle))
    except KeyError:
        raise HTTPException(404, "nobody by that handle here") from None


@router.get("/ap/users/{handle}/outbox")
def outbox(handle: str, db: Db, cfg: Cfg) -> Response:
    try:
        return _json(federation.outbox(db, cfg, handle))
    except KeyError:
        raise HTTPException(404, "nobody by that handle here") from None


@router.post("/ap/users/{handle}/inbox")
async def inbox(handle: str, request: Request, db: Db, cfg: Cfg) -> Response:
    body = b""
    async for chunk in request.stream():
        body += chunk
        if len(body) > federation.MAX_BODY:
            raise HTTPException(413, "that's too large")
    path = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    try:
        got = await run_in_threadpool(federation.receive, db, cfg, handle, "POST", path, dict(request.headers), body)
    except KeyError:
        raise HTTPException(404, "nobody by that handle here") from None
    except federation.Unsigned as e:
        raise HTTPException(401, str(e)) from None
    except PermissionError as e:
        raise HTTPException(403, str(e)) from None
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return _json({"ok": got}, "application/json", 202)
