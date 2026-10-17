"""A namespace's own chat completions, OpenAI's way (completions.py): any app that speaks OpenAI's API gets answers
grounded in one namespace, citing it, with the base URL /api/v1/namespaces/<namespace> and a Lens token as its key.

Errors come in OpenAI's shape ({"error": {"message", "type", "code"}}), so apps show them as they would OpenAI's.
"""

from __future__ import annotations

import contextvars
import json
import logging
import queue
import secrets
import threading
import time
from collections.abc import Iterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.api.deps import Db, Principal, optional_user, within
from app.api.v1.routes.oauth import web_app_base
from app.domain import activity, auth, budgets, completions, integrations, llm, store
from app.schemas.completions import ChatCompletion, ChatCompletionRequest, CompletionModels

log = logging.getLogger("lens")
router = APIRouter(prefix="/namespaces", tags=["chat completions"])
KEEPALIVE = 15  # seconds between SSE comments while the tools are looked up, so proxies keep the stream open
OPENAI_ERRORS: dict[int | str, dict[str, Any]] = {
    400: {"description": "OpenAI's error shape: {error: {message, type, code}}"},
    401: {"description": "no token, or one that doesn't open this namespace"},
    403: {"description": "the namespace's chat completions are off"},
    404: {"description": "no such namespace, or not one you can read"},
    429: {"description": "a budget is used up"},
    502: {"description": "the model server failed"},
}


class Refused(Exception):
    def __init__(self, status: int, message: str, code: str, kind: str = "invalid_request_error") -> None:
        super().__init__(message)
        self.status, self.message, self.code, self.kind = status, message, code, kind

    def response(self) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if self.status == 401 else None
        body = {"error": {"message": self.message, "type": self.kind, "param": None, "code": self.code}}
        return JSONResponse(body, status_code=self.status, headers=headers)


def _open(request: Request, db: Db, name: str) -> tuple[Principal, int]:
    """Who asks, and the namespace they ask about, when its chat completions are on and theirs to read."""
    user = optional_user(request, db)
    if not user:
        why = getattr(request.state, "refused", None)  # a key or app given another namespace's servers alone
        raise Refused(401, why or "give a Lens API key as the key (Authorization: Bearer …)", "invalid_api_key")
    try:
        sid = store.ns_id(db, name, create=False)
    except KeyError:
        raise Refused(404, f"there's no namespace {name} you can read", "not_found") from None
    if not auth.allows(user.roles, sid):
        raise Refused(404, f"there's no namespace {name} you can read", "not_found")
    if not integrations.profile(db, sid)["chat"]:
        raise Refused(
            403,
            f"{name}'s chat completions are off: an owner of {name} turns them on under Admin → Namespaces → {name} → Connect apps",
            "endpoint_off",
            "permission_error",
        )
    return within(user, sid), sid


@router.get("/{name}/models", responses=OPENAI_ERRORS)
def list_completion_models(name: str, request: Request, db: Db) -> CompletionModels:
    """OpenAI's model list for the namespace's chat completions: lens (the configured model) and the models people may
    pick in Lens's chat."""
    try:
        _open(request, db, name)
    except Refused as e:
        return e.response()  # type: ignore[return-value]
    cfg = request.app.state.settings.current()
    return CompletionModels(data=[{"id": m} for m in completions.models(cfg)])


def _chunk(cid: str, created: int, model: str, delta: dict[str, Any], finish: str | None = None, **extra: Any) -> str:
    body = {
        "id": cid,
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        **extra,
    }
    return f"data: {json.dumps(body, ensure_ascii=False)}\n\n"


@router.post("/{name}/chat/completions", response_model=ChatCompletion, responses=OPENAI_ERRORS)
def create_chat_completion(name: str, body: ChatCompletionRequest, request: Request, db: Db) -> Any:
    """OpenAI's chat completions, answered from this namespace alone: the last user message is the question, the
    messages before it the conversation and system messages the app's instructions. The answer cites what it read as
    [n], with a Sources list after it, `citations` (the n-th link for [n]) and `lens.sources`. With `stream`, it comes
    as OpenAI's chunks (server-sent events) ending in `data: [DONE]`. Off until an owner of the namespace turns it on;
    read-only tokens are enough. 429 when the namespace's budget or Lens's overall one is used up."""
    cfg = request.app.state.settings.current()
    try:
        user, sid = _open(request, db, name)
        try:
            question, history, app = completions.read(body.messages)
        except ValueError as e:
            raise Refused(400, str(e), "invalid_messages") from None
        if budgets.status(db, f"space:{sid}")["state"] == "over":
            raise Refused(
                429, f"{name}'s budget is used up; raise it in Settings › Budgets to go on", "insufficient_quota", "insufficient_quota"
            )
    except Refused as e:
        return e.response()
    model = completions.pick(cfg, body.model)
    tools = integrations.profile(db, sid)["tools"]
    web = web_app_base(request)
    args = (db, cfg, sid, name, user.as_audit(), question, history, app, model, tools, request.app.state.archive.base, body.temperature)
    cid, created = "chatcmpl-" + secrets.token_hex(12), int(time.time())
    with_sources = body.response_format is None

    def extras(done: dict[str, Any], steps: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        found = completions.sources(web, done["text"], done["refs"])
        lens = {
            "namespace": name,
            "sources": found,
            "steps": steps,
            "record": done.get("record"),
            **({"notice": done["notice"]} if done.get("notice") else {}),
        }
        return found, {"citations": [s["url"] for s in found], "lens": lens}

    if not body.stream:
        steps: list[dict[str, Any]] = []
        text, done = "", {}
        try:
            with activity.scope(db, f"space:{sid}", cfg=cfg):
                for kind, data in completions.answer(*args):
                    if kind == "step":
                        steps.append(data)
                    elif kind == "token":
                        text += data
                    else:
                        done = data
        except activity.OverBudget as e:
            return Refused(429, str(e), "insufficient_quota", "insufficient_quota").response()
        except llm.LLMError as e:
            return Refused(502, str(e), "upstream_error", "api_error").response()
        found, more = extras(done, steps)
        content = text + (completions.footer(found) if with_sources else "")
        return {
            "id": cid,
            "object": "chat.completion",
            "created": created,
            "model": done["model"],
            "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
            **more,
        }

    # Streamed: the answer is worked out in a thread of its own (the ledger's scope holds for all of it) and handed
    # over piece by piece; a quiet stretch (the tools) sends an SSE comment so nothing in between closes the stream.
    events: queue.Queue[tuple[str, Any]] = queue.Queue()

    def work() -> None:
        try:
            with activity.scope(db, f"space:{sid}", cfg=cfg):
                for ev in completions.answer(*args):
                    events.put(ev)
        except activity.OverBudget as e:
            events.put(("error", Refused(429, str(e), "insufficient_quota", "insufficient_quota")))
        except llm.LLMError as e:
            events.put(("error", Refused(502, str(e), "upstream_error", "api_error")))
        except Exception:  # noqa: BLE001 - the app hears it broke off rather than waiting
            log.exception("chat completions for %s failed", name)
            events.put(("error", Refused(500, "something went wrong while answering; try again", "server_error", "api_error")))
        events.put(("end", None))

    threading.Thread(target=contextvars.copy_context().run, args=(work,), daemon=True, name="lens-completion").start()
    shown = completions.MODEL if model is None else model

    def gen() -> Iterator[str]:
        yield _chunk(cid, created, shown, {"role": "assistant", "content": ""})
        steps: list[dict[str, Any]] = []
        while True:
            try:
                kind, data = events.get(timeout=KEEPALIVE)
            except queue.Empty:
                yield ": still working\n\n"
                continue
            if kind == "step":
                steps.append(data)
                yield f": looked up {data['tool']}\n\n"
            elif kind == "token":
                yield _chunk(cid, created, shown, {"content": data})
            elif kind == "done":
                found, more = extras(data, steps)
                tail = completions.footer(found) if with_sources else ""
                if tail:
                    yield _chunk(cid, created, shown, {"content": tail})
                usage = {"usage": None} if (body.stream_options or {}).get("include_usage") else {}
                yield _chunk(cid, created, shown, {}, "stop", **more, **usage)
            elif kind == "error":
                err: Refused = data
                yield f"data: {json.dumps({'error': {'message': err.message, 'type': err.kind, 'param': None, 'code': err.code}})}\n\n"
            else:
                yield "data: [DONE]\n\n"
                return

    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
