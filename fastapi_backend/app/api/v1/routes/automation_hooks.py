"""An automation's webhook (docs/automations.md, Webhook trigger): the address anything that can send HTTP calls to set
an automation off. The token in the address is all a caller needs; the hooks themselves are made and revoked when
automations are saved and deleted (domain/automation_hooks.py)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from starlette.concurrency import run_in_threadpool
from starlette.formparsers import MultiPartException, MultiPartParser

from app.api.deps import Cfg, Db
from app.domain import automation_hooks
from app.schemas.automation_hooks import HookCalled

router = APIRouter(tags=["automations"])
CALL_BODY = {
    "requestBody": {
        "required": False,
        "content": {
            "application/json": {"schema": {}},
            "application/x-www-form-urlencoded": {"schema": {"type": "object", "additionalProperties": {"type": "string"}}},
            "multipart/form-data": {"schema": {"type": "object", "additionalProperties": {"type": "string"}}},
            "text/plain": {"schema": {"type": "string"}},
        },
    }
}


async def _body(request: Request) -> bytes:
    """The request's body, refused (413) past automation_hooks.MAX_BODY without reading the rest."""
    most = automation_hooks.MAX_BODY
    told = request.headers.get("content-length") or ""
    if told.isdigit() and int(told) > most:
        raise HTTPException(413, f"send at most {most // 1024} KB")
    got, n = [], 0
    async for piece in request.stream():
        n += len(piece)
        if n > most:
            raise HTTPException(413, f"send at most {most // 1024} KB")
        got.append(piece)
    return b"".join(got)


async def _multipart(request: Request, body: bytes) -> dict[str, Any] | None:
    """A multipart form's fields ({field: value, or a list of values}); files are refused."""
    if (request.headers.get("content-type") or "").split(";")[0].strip().lower() != "multipart/form-data":
        return None

    async def once() -> Any:
        yield body

    try:
        form = await MultiPartParser(request.headers, once(), max_files=0, max_fields=automation_hooks.MAX_FIELDS).parse()
    except MultiPartException as e:
        raise HTTPException(400, f"a form of text fields only: {e.message}") from None
    out: dict[str, Any] = {}
    for k in form:
        vals = [str(v) for v in form.getlist(k)]
        out[k] = vals[0] if len(vals) == 1 else vals
    return out


@router.post(
    "/automations/hooks/{token}",
    status_code=202,
    openapi_extra=CALL_BODY,
    responses={
        401: {"description": "not a webhook's token, or a signature that doesn't match"},
        403: {"description": "the automation is switched off"},
        413: {"description": "over 64 KB"},
        415: {"description": "neither JSON, a form nor text"},
        429: {"description": "over 60 calls a minute to this webhook"},
    },
)
async def call_automation_hook(token: str, request: Request, db: Db, cfg: Cfg) -> HookCalled:
    """Set an automation off: what is sent (JSON, a form or text, up to 64 KB) becomes a `webhook.received` activity
    for the automation whose trigger this address is. No session: the token in the address is the credential. Signed
    the Standard Webhooks way (webhook-id, webhook-timestamp, webhook-signature), the signature must match the webhook's
    secret; `webhook-id` or `Idempotency-Key` makes a retried call count once. Answers at once, before the automation
    runs."""
    body = await _body(request)
    form = await _multipart(request, body)
    try:
        aid = await run_in_threadpool(
            automation_hooks.receive,
            db,
            cfg,
            token,
            body,
            request.headers.get("content-type") or "",
            request.query_params.multi_items(),
            dict(request.headers),
            form,
        )
    except automation_hooks.Refused as e:
        headers = {"Retry-After": "60"} if e.status == 429 else None
        raise HTTPException(e.status, str(e), headers=headers) from None
    return HookCalled(id=aid)
