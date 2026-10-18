"""Storage sources (local folders and cloud storage, through rclone) and the watched folders that import from them."""

from __future__ import annotations

import logging
from collections import Counter

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, Response

from app.api.deps import Acl, AdminReader, AdminWriter, Cfg, CurrentUser, Db, web_origin
from app.api.v1.routes.external import _cookie
from app.domain import auth, google_auth, rclone_signin, sources, store
from app.domain.store import DB
from app.schemas.admin import Started
from app.schemas.common import Created, Ok
from app.schemas.sources import (
    Backend,
    BrowseEntry,
    GoogleClient,
    GoogleConnect,
    GoogleConnectStart,
    RcloneSignIn,
    RcloneSignInFinish,
    RcloneSignInStart,
    RcloneSignInState,
    RoutedMessage,
    RoutePreviewRequest,
    RouteView,
    SharedRoutes,
    Source,
    SourceCreate,
    SourceCreated,
    SourceHealth,
    SourceUpdate,
    SourceUpdated,
    Watch,
    WatchCreate,
    WatchPreview,
    WatchPreviewRequest,
    WatchUpdate,
)

log = logging.getLogger("lens")
router = APIRouter(tags=["sources"])


def _source(db: DB, sid: int) -> None:
    try:
        sources.get(db, sid)
    except KeyError:
        raise HTTPException(404, "not found") from None


def _watch(db: DB, wid: int) -> None:
    if not db.one("SELECT id FROM $r", r=store.R("watch_path", wid)):
        raise HTTPException(404, "not found")


@router.get("/sources/backends")
def list_backends(user: CurrentUser) -> dict[str, Backend]:
    """The kinds of storage a source can be, with their settings and secrets."""
    keys = {"label", "fields", "secrets", "oauth", "google"}
    return {k: Backend.model_validate({f: x for f, x in v.items() if f in keys}) for k, v in sources.BACKENDS.items()}


@router.get("/sources")
def list_sources(user: AdminReader, db: Db) -> list[Source]:
    return sources.list_sources(db)


@router.post("/sources")
def create_source(body: SourceCreate, user: AdminWriter, db: Db, cfg: Cfg) -> SourceCreated:
    """Add a source and test it; secrets are stored encrypted and never shown again."""
    try:
        sid = sources.create(db, cfg, body.name, body.type, body.params, body.secrets, user.email)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "source.create", f"storage_source:{sid}")
    return SourceCreated(id=sid, health=sources.test(db, cfg, sid))


@router.patch("/sources/{sid}")
def update_source(sid: int, body: SourceUpdate, user: AdminWriter, db: Db, cfg: Cfg) -> SourceUpdated:
    _source(db, sid)
    try:
        sources.update(db, cfg, sid, body.name, body.params, body.secrets)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "source.update", f"storage_source:{sid}")
    return SourceUpdated(health=sources.test(db, cfg, sid))


@router.put("/sources/{sid}/routes")
def set_shared_routes(sid: int, body: SharedRoutes, user: AdminWriter, db: Db) -> list[RouteView]:
    """Save an email account's shared routing rules, which every watch on it tries after its own (none: unchanged
    behaviour). They apply to messages that arrive after they're saved."""
    _source(db, sid)
    try:
        rules = sources.set_shared_routes(db, sid, [r.model_dump() for r in body.routes])
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "source.routes", f"storage_source:{sid}")
    return [RouteView(**r) for r in rules]


@router.post("/sources/rclone-signin")
def start_rclone_signin(body: RcloneSignInStart, user: AdminWriter, db: Db, cfg: Cfg) -> RcloneSignIn:
    """Connect Google Drive (with no Google app set up here), Dropbox or OneDrive by signing in through rclone on this
    server, or sign a connection in again (`source`). Open `url` in a browser and sign in; the service then sends the
    browser to http://127.0.0.1:53682/?…, which only loads on the machine Lens runs on. Hand that address to
    POST /sources/rclone-signin/{id}, which makes the connection. One at a time: this ends any other."""
    if body.source is None and body.type is None:
        raise HTTPException(400, "say which type of connection to make, or which connection to sign in again")
    try:
        fid, url, port = rclone_signin.start(db, cfg, body.type, body.params, body.name, body.source, by=user.email)
    except KeyError:
        raise HTTPException(404, "not found") from None
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    row = db.one("SELECT expires_at FROM $r", r=store.R("rclone_signin", fid))
    return RcloneSignIn(id=fid, url=url, port=port, expires_at=row["expires_at"])


@router.post("/sources/rclone-signin/{fid}")
def finish_rclone_signin(fid: str, body: RcloneSignInFinish, user: AdminWriter, db: Db, cfg: Cfg) -> RcloneSignInState:
    """Finish signing in through rclone with the address the browser ended on (`address`), or, without one, look
    whether rclone already got it. Answers the connection, made and tested, or none while it waits. 400: that address
    isn't this sign-in's, and it's still waiting for the right one; 410: it didn't work (cancelled, refused, ran out),
    and it's over; start again. Audited as `source.signin`."""
    try:
        sid = rclone_signin.finish(db, cfg, fid, body.address)
    except KeyError:
        raise HTTPException(404, "no such sign-in: it finished, ran out or was cancelled") from None
    except rclone_signin.WrongAddress as e:
        raise HTTPException(400, str(e)) from None
    except ValueError as e:
        raise HTTPException(410, str(e)) from None
    if sid is not None:
        auth.audit(db, user.as_audit(), "source.signin", f"storage_source:{sid}", ["rclone"])
    return RcloneSignInState(connected=sid)


@router.delete("/sources/rclone-signin/{fid}")
def cancel_rclone_signin(fid: str, user: AdminWriter, db: Db) -> Ok:
    """Stop a sign-in through rclone."""
    try:
        rclone_signin.cancel(db, fid)
    except KeyError:
        raise HTTPException(404, "no such sign-in") from None
    return Ok()


@router.delete("/sources/{sid}")
def delete_source(sid: int, user: AdminWriter, db: Db) -> Ok:
    """Remove a source and its watched folders."""
    sources.remove(db, sid)
    auth.audit(db, user.as_audit(), "source.delete", f"storage_source:{sid}")
    return Ok()


@router.post("/sources/{sid}/test")
def test_source(sid: int, user: AdminWriter, db: Db, cfg: Cfg) -> SourceHealth:
    _source(db, sid)
    return sources.test(db, cfg, sid)


@router.get("/sources/google-client")
def google_client(user: AdminReader, request: Request, db: Db, cfg: Cfg) -> GoogleClient:
    """The OAuth client a Google account would be connected through (never its secret), and the redirect URI it needs."""
    try:
        c = google_auth.client(db, cfg)
    except ValueError:
        c = {"origin": None, "client_id": None}
    return GoogleClient(origin=c["origin"], client_id=c["client_id"], redirect_uri=web_origin(request) + google_auth.CALLBACK)


@router.post("/sources/{sid}/google")
def connect_google(
    sid: int, user: AdminWriter, request: Request, response: Response, db: Db, cfg: Cfg, body: GoogleConnect | None = None
) -> GoogleConnectStart:
    """Connect a Google account to a Google source (Gmail, Google Calendar): open the returned address in this browser,
    consent there, and Google sends you back to the Sources page with the account connected. Its refresh token is kept
    sealed on the source. Without a client here, the Google sign-in provider's (Settings › Sign-in) or an earlier connection's is
    used. Only from the web app: the consent comes back to the browser that asked (an agent saves an authorized user
    JSON it already has as the source's `token` instead). Audited as `source.google_connect`."""
    if user.via != "access":
        raise HTTPException(
            403,
            "connect Google from the web app: the consent comes back to the browser that asked, so API tokens and apps "
            "can't (save an authorized user JSON as the source's token instead)",
        )
    body = body or GoogleConnect()
    try:
        src = sources.get(db, sid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    origin = web_origin(request)
    try:
        url, browser = google_auth.connect_start(db, cfg, src, origin, (body.client_id, body.client_secret), user.id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    _cookie(response, origin, browser)
    return GoogleConnectStart(url=url)


@router.get("/sources/{sid}/browse")
def browse_source(sid: int, user: AdminReader, db: Db, cfg: Cfg, path: str = "") -> list[BrowseEntry]:
    """The folders and files at one path of a source (for local sources, no path lists the allowed roots), with the
    assets each file already is."""
    _source(db, sid)
    try:
        entries = sources.browse(db, cfg, sid, path)
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    have = sources.imported(db, sid, [e["path"] for e in entries if not e["dir"]])
    return [BrowseEntry(**e, imported=have.get(e["path"], [])) for e in entries]


@router.get("/watches")
def list_watches(user: CurrentUser, acl: Acl, db: Db) -> list[Watch]:
    """Watched folders: all of them for admins, else those feeding namespaces you own."""
    roles = acl.roles
    return sources.list_watches(db, None if user.admin else {s for s in roles if auth.allows(roles, s, "owner")})


@router.post("/watches")
def create_watch(body: WatchCreate, user: AdminWriter, db: Db, cfg: Cfg) -> Created:
    """Watch a folder of a source; new files there are imported into the namespace (which is created if needed)."""
    opts = body.model_dump(exclude_unset=True, exclude={"source", "path", "namespace"})
    try:
        sources.get(db, body.source)
    except KeyError:
        raise HTTPException(400, "no such source") from None
    try:
        wid = sources.create_watch(db, cfg, body.source, body.path, store.ns_id(db, body.namespace), user.email, **opts)
    except (ValueError, SystemExit) as e:  # ns_id exits on a malformed namespace name
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "watch.create", f"watch_path:{wid}")
    return Created(id=wid)


@router.post("/watches/preview")
def preview_watch(body: WatchPreviewRequest, user: AdminWriter, db: Db, cfg: Cfg) -> WatchPreview:
    """How many files a watched folder would pick up (the backfill count), before creating it."""
    try:
        files = sources.list_files(db, cfg, sources.get(db, body.source), body.path, headers=False)
    except KeyError:
        raise HTTPException(400, "no such source") from None
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    kinds = Counter(sources.kind_of(cfg, body.model_dump(exclude_unset=True, include={"kinds", "include", "exclude"}), f) for f in files)
    return WatchPreview(
        files=sum(n for k, n in kinds.items() if k),
        audio=kinds["audio"],
        transcripts=kinds["transcript"],
        documents=kinds["document"],
        images=kinds["image"],
    )


@router.post("/watches/routes/preview")
def preview_routes(body: RoutePreviewRequest, user: AdminWriter, db: Db, cfg: Cfg) -> list[RoutedMessage]:
    """Where the latest messages of an email account would go under these routing rules, before saving them."""
    try:
        shared = None if body.shared_routes is None else [r.model_dump() for r in body.shared_routes]
        rows = sources.preview_routes(db, cfg, body.source, body.path, [r.model_dump() for r in body.routes], body.namespace, shared=shared)
    except KeyError:
        raise HTTPException(400, "no such source") from None
    except (ValueError, RuntimeError) as e:
        raise HTTPException(400, str(e)) from None
    return [RoutedMessage(**r) for r in rows]


@router.patch("/watches/{wid}")
def update_watch(wid: int, body: WatchUpdate, user: AdminWriter, db: Db) -> Ok:
    _watch(db, wid)
    try:
        sources.update_watch(db, wid, **body.model_dump(exclude_unset=True))
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    return Ok()


@router.delete("/watches/{wid}")
def delete_watch(wid: int, user: AdminWriter, db: Db) -> Ok:
    sources.remove_watch(db, wid)
    auth.audit(db, user.as_audit(), "watch.delete", f"watch_path:{wid}")
    return Ok()


def _scan(request: Request, wid: int) -> None:
    try:
        sources.poll_watch(request.app.state.db, request.app.state.settings.current(), wid, log=log.info)
    except Exception:  # noqa: BLE001 - runs after the response; log it
        log.exception("scanning watched folder %s failed", wid)


@router.post("/watches/{wid}/scan", status_code=202)
def scan_watch(wid: int, user: AdminWriter, request: Request, db: Db, tasks: BackgroundTasks) -> Started:
    """Look for new files now instead of waiting for the next poll."""
    _watch(db, wid)
    tasks.add_task(_scan, request, wid)
    return Started(status="scanning")
