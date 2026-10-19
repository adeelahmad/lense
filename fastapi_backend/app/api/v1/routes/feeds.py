"""Published feeds (app/domain/syndication.py, docs/feeds.md): RSS and podcast feeds a namespace's editors make and
fill (and routines fill through a workflow's publish nodes), and the private addresses feed readers and podcast apps
read them at. The token in a feed's address is all a reader needs."""

from __future__ import annotations

import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, Response

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors, web_origin
from app.api.v1.routes import activities as activity_routes
from app.api.v1.routes import recordings as recording_routes
from app.domain import auth, events, syndication
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.feeds import Feed, FeedCreate, FeedItem, FeedPublish, FeedPublished, FeedSettings

router = APIRouter(prefix="/feeds", tags=["feeds"])
# a feed's address carries its token: kept out of other sites' logs, and out of search engines
PRIVATE = {"Referrer-Policy": "no-referrer", "X-Robots-Tag": "noindex, nofollow"}


def _feed(acl: Access, db: DB, fid: int, role: str = "viewer") -> dict[str, Any]:
    """The feed, if this person has `role` in its namespace (404 when they can't read it)."""
    try:
        feed = syndication.get(db, fid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    acl.need(feed["space"], role)
    return feed


def _out(db: DB, cfg: dict[str, Any], request: Request, feed: dict[str, Any]) -> Feed:
    return Feed.model_validate(syndication.view(db, cfg, feed, web_origin(request)))


# ---------- what feed readers read (no session: the token is the credential) ----------
@router.get(
    "/public/{token}",
    response_class=Response,
    responses={200: {"content": {"application/rss+xml": {}}}, 304: {"description": "unchanged since the reader's copy"}},
)
def read_feed(token: str, request: Request, db: Db, cfg: Cfg) -> Response:
    """A feed as RSS 2.0, with Apple Podcasts' tags for a podcast feed: what feed readers and podcast apps subscribe
    to. No session: the token in the address is the credential (404 for any other). Answers 304 to a reader whose
    copy (If-None-Match) is current."""
    try:
        feed = syndication.by_token(db, token)
    except KeyError:
        raise HTTPException(404, "not found") from None
    body = syndication.render_xml(db, cfg, feed, web_origin(request), token)
    tag = '"' + hashlib.sha256(body).hexdigest()[:32] + '"'
    if request.method == "GET":
        syndication.seen(db, feed)
    headers = {**PRIVATE, "ETag": tag, "Cache-Control": f"private, max-age={syndication.TTL_MINUTES * 60}"}
    if tag in [t.strip() for t in (request.headers.get("if-none-match") or "").split(",")]:
        return Response(status_code=304, headers=headers)
    return Response(body, media_type="application/rss+xml; charset=utf-8", headers=headers)


@router.get(
    "/public/{token}/items/{iid}/audio",
    response_class=Response,
    responses={200: {"content": {"audio/*": {}}}, 206: {"description": "a byte range"}},
)
def read_feed_audio(token: str, iid: int, request: Request, db: Db, cfg: Cfg) -> Response:
    """An item's audio (or video), with byte ranges, as podcast apps download it. The feed's token is the credential."""
    try:
        feed = syndication.by_token(db, token)
        rid, rec = syndication.item_recording(db, feed, iid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    got = recording_routes.serve_audio(db, cfg, rec, rid, request, visitor=True)  # a feed's listeners are visitors
    got.headers.update(PRIVATE)
    if request.method == "HEAD":  # the headers alone; nothing is read or decrypted
        return Response(status_code=got.status_code, headers=dict(got.headers))
    return got


# podcast apps check an enclosure with HEAD before they download it (the same answers, without a body)
router.add_api_route("/public/{token}", read_feed, methods=["HEAD"], include_in_schema=False)
router.add_api_route("/public/{token}/items/{iid}/audio", read_feed_audio, methods=["HEAD"], include_in_schema=False)


# ---------- managing feeds (signed in) ----------
@router.get("")
def list_feeds(request: Request, acl: Acl, user: CurrentUser, db: Db, cfg: Cfg, namespace: str | None = None) -> list[Feed]:
    """Feeds in the namespaces you can read (or one of them), each with its private address."""
    spaces = acl.readable()
    if namespace:
        spaces = {acl.namespace(namespace)}
    return [_out(db, cfg, request, f) for f in syndication.list_feeds(db, spaces)]


@router.post("", status_code=201)
def create_feed(body: FeedCreate, request: Request, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> Feed:
    """A new feed in a namespace you edit, with its private address. Audited as `feed.create`."""
    sid = acl.namespace(body.namespace, "editor")
    opts = body.model_dump(exclude={"namespace", "title", "kind"}, exclude_none=True)
    with domain_errors():
        fid = syndication.create(db, cfg, sid, body.title, body.kind, by=user.email, **opts)
    auth.audit(db, user.as_audit(), "feed.create", f"feed:{fid}", {"kind": body.kind})
    return _out(db, cfg, request, syndication.get(db, fid))


@router.get("/{fid}")
def get_feed(fid: int, request: Request, acl: Acl, user: CurrentUser, db: Db, cfg: Cfg) -> Feed:
    return _out(db, cfg, request, _feed(acl, db, fid))


@router.patch("/{fid}")
def update_feed(fid: int, body: FeedSettings, request: Request, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> Feed:
    """Change a feed's settings (editors of its namespace). Fields left out stay as they are."""
    _feed(acl, db, fid, "editor")
    with domain_errors():
        syndication.update(db, fid, **body.model_dump(exclude_unset=True))
    return _out(db, cfg, request, syndication.get(db, fid))


@router.delete("/{fid}")
def delete_feed(fid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Delete a feed and its items (editors): its address stops working at once. What the items were about stays.
    Audited as `feed.delete`."""
    _feed(acl, db, fid, "editor")
    syndication.remove(db, fid)
    auth.audit(db, user.as_audit(), "feed.delete", f"feed:{fid}")
    return Ok()


@router.post("/{fid}/token")
def rotate_feed_token(fid: int, request: Request, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> Feed:
    """A new private address for a feed (editors): the old one stops working at once, so readers subscribed to it
    need the new one. Audited as `feed.token`."""
    _feed(acl, db, fid, "editor")
    syndication.rotate(db, cfg, fid)
    auth.audit(db, user.as_audit(), "feed.token", f"feed:{fid}")
    return _out(db, cfg, request, syndication.get(db, fid))


@router.get("/{fid}/items")
def list_feed_items(
    fid: int, acl: Acl, user: CurrentUser, db: Db, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)
) -> list[FeedItem]:
    """A feed's items, newest first."""
    _feed(acl, db, fid)
    return [FeedItem.model_validate(i) for i in syndication.items(db, fid, limit, offset)]


def publish(acl: Access, user: Principal, db: DB, cfg: dict[str, Any], fid: int, body: FeedPublish) -> FeedPublished:
    """Add resources, activities and text to a feed, for this person: an editor of the feed's namespace, who can read
    every resource and activity named."""
    feed = _feed(acl, db, fid, "editor")
    entries: list[dict[str, Any]] = []
    refused = []
    for rid in dict.fromkeys(body.recordings):
        acl.recording(rid)  # 404 for one they can't see
        entries.append({"recording": rid})
    if body.activities:
        found = events.get_many(db, cfg, body.activities, activity_routes.viewer(db, user, acl))
        for aid in dict.fromkeys(body.activities):
            if aid in found:
                entries.append({"activity": found[aid]})
            else:
                refused.append({"what": f"activity {aid}", "why": "not found"})
    entries += [t.model_dump(exclude_none=True) for t in body.texts]
    if not entries and not refused:
        raise HTTPException(400, "name resources, activities or text to add")
    out = syndication.publish(db, cfg, feed["id"], entries, by=user.email)
    out["refused"] = refused + out["refused"]
    if out["added"]:
        auth.audit(db, user.as_audit(), "feed.publish", f"feed:{fid}", {"items": out["added"]})
    return FeedPublished.model_validate(out)


@router.post("/{fid}/items")
def publish_to_feed(fid: int, body: FeedPublish, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> FeedPublished:
    """Add resources (podcast episodes too), timeline activities and text items to a feed (editors of its namespace).
    What's already in it isn't added twice; what may not show there (it's in a namespace not everyone who reads the
    feed can read) is refused, with why. Audited as `feed.publish`."""
    return publish(acl, user, db, cfg, fid, body)


@router.delete("/{fid}/items/{iid}")
def remove_feed_item(fid: int, iid: int, acl: Acl, user: Writer, db: Db) -> Ok:
    """Take an item out of a feed (editors). A routine may add it again; what it was about stays."""
    _feed(acl, db, fid, "editor")
    try:
        syndication.remove_item(db, fid, iid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    return Ok()
