"""Notes as pages (docs/notes.md): free notes in a tree, and the page of every resource, entity and topic.

Pages belong to a namespace: anyone with a role there reads them, editors write them. A thing's own page is made the
first time someone writes on it; until then it reads as a draft.
"""

from __future__ import annotations

import contextlib
import tempfile
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import Response, StreamingResponse
from starlette.concurrency import run_in_threadpool

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.api.v1.routes.files import _receive
from app.api.v1.routes.uploads import CHUNK
from app.domain import auth, keyring, notebook, store
from app.domain import files as resource_files
from app.domain.store import DB
from app.schemas.common import Ok
from app.schemas.notebook import (
    NoteDeleted,
    NoteFile,
    NoteHistory,
    NoteHit,
    NoteHomeSuggestion,
    NoteLinkSuggestion,
    NoteLinkTarget,
    NotePage,
    NotePageCreate,
    NotePageDraft,
    NotePageItem,
    NotePageMove,
    NotePageUpdate,
    NoteSearch,
    NoteToday,
    NoteTree,
    NoteVersion,
    NoteVersionItem,
    Place,
)

router = APIRouter(prefix="/notes", tags=["notes"])


def _page(acl: Access, db: DB, pid: int, role: str = "viewer") -> dict[str, Any]:
    try:
        p = notebook.get(db, pid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    acl.need(p["space"], role)
    return p


def _backlinks(acl: Access, db: DB, sid: int, targets: list[str]) -> list[dict[str, Any]]:
    return notebook.backlinks(db, sid, targets) if targets else []


def _mine(p: dict[str, Any], user: Principal) -> dict[str, Any]:
    """The page's fields as this person sees them: whether they pinned it, not who else did."""
    out = {k: v for k, v in p.items() if k not in ("space", "created_by", "updated_by", "pinned_by")}
    return {**out, "pinned": user.id in (p.get("pinned_by") or [])}


def _out(acl: Access, db: DB, p: dict[str, Any], user: Principal) -> NotePage:
    names = store.space_names(db)
    links = notebook.links(db, p["id"])
    found = notebook.labels(db, [x["target"] for x in links])
    readable = set(acl.spaces()) if not user.admin else set(names)
    out_links = []
    for x in links:
        t = found.get(x["target"])
        seen = t is not None and t.get("space") in readable
        out_links.append({**x, "name": t["name"] if seen else None, "namespace": names.get(t["space"]) if seen else None})
    writer = db.one("SELECT email FROM $r", r=store.R("account", p["created_by"])) if p.get("created_by") else None
    targets = [f"page:{p['id']}"] + ([p["about"]] if p.get("about") else [])
    return NotePage(
        **_mine(p, user),
        namespace=names.get(p["space"], ""),
        created_by=(writer or {}).get("email"),
        links=out_links,
        backlinks=[b for b in _backlinks(acl, db, p["space"], targets) if b["page"] != p["id"]],
        can_edit=user.can_write and auth.allows(acl.roles, p["space"], "editor"),
    )


@router.get("")
def list_pages(ns: str, user: CurrentUser, acl: Acl, db: Db, everything: bool = Query(False, alias="all")) -> NoteTree:
    """The namespace's free notes for the tree (no bodies), in order; `all=true` adds the pages of things."""
    sid = acl.namespace(ns)
    return NoteTree(namespace=ns, pages=[NotePageItem(**_mine(p, user)) for p in notebook.tree(db, sid, everything)])


@router.post("")
def create_page(body: NotePageCreate, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Write a free note (optionally inside `parent`), or the page of a thing (`about`, like "recording:12"), which
    each thing has one of. Needs editor access to the namespace."""
    sid = acl.namespace(body.ns, "editor")
    with domain_errors():
        pid = notebook.create(
            db,
            sid,
            user.id,
            body.title,
            body.body,
            body.summary,
            body.date,
            body.place,
            body.parent,
            body.about,
            doc=body.doc,
            view=body.view,
        )
    return _out(acl, db, notebook.get(db, pid), user)


@router.post("/today")
def today_page(body: NoteToday, user: Writer, acl: Acl, db: Db) -> NotePage:
    """The namespace's note for the day (`date`, your today), made the first time it's asked for: titled with the
    date, inside the "Daily notes" note. Needs editor access."""
    sid = acl.namespace(body.ns, "editor")
    with domain_errors():
        pid = notebook.today(db, sid, user.id, body.date)
    return _out(acl, db, notebook.get(db, pid), user)


@router.get("/targets")
def link_targets(
    ns: str, user: CurrentUser, acl: Acl, db: Db, q: str = "", sign: str = "@", limit: int = Query(20, ge=1, le=50)
) -> list[NoteLinkTarget]:
    """What a mention can link to, best matches first: `#` for topics; `@` for pages, assets, named people and other
    entities, collections and people."""
    sid = acl.namespace(ns)
    if sign not in ("@", "#"):
        raise HTTPException(400, "sign is @ or #")
    return [NoteLinkTarget(**t) for t in notebook.search_targets(db, sid, q, sign, limit)]


@router.get("/search")
def search_pages(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    q: str = Query("", max_length=200, description="words in the title, one-line summary or text"),
    ns: str | None = Query(None, description="only this namespace (default: every one you can read)"),
    place: Place | None = Query(None, description="only notes filed here"),
    free: bool = Query(False, description="only free notes, not the pages of things"),
    limit: int = Query(50, ge=1, le=200),
) -> NoteSearch:
    """Pages found by their words, those with them in the title first, then the latest changed, each with the words
    around the match: what the Notes page and Jump to show, and what the assistant's find_notes reads."""
    names = store.space_names(db)
    spaces = [acl.namespace(ns)] if ns else (list(names) if user.admin else acl.spaces())
    total, hits = notebook.find(db, spaces, q, place, limit, free=free)
    return NoteSearch(
        total=total,
        pages=[NoteHit(**{k: v for k, v in h.items() if k != "space"}, namespace=names.get(h["space"], "")) for h in hits],
    )


def _trashed(acl: Access, db: DB, pid: int, role: str = "viewer") -> dict[str, Any]:
    try:
        t = notebook.trashed(db, pid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    acl.need(t["space"], role)
    return t


def _attachment(name: str) -> str:
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}"


@router.get(
    "/export",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/zip": {}}, "description": "a zip of Markdown files"}},
)
def export_notes(ns: str, user: CurrentUser, acl: Acl, db: Db) -> StreamingResponse:
    """Every page of the namespace as Markdown files in a zip, for other notes apps (Obsidian and the like): free notes
    in folders that follow the tree, the pages of things under "Pages of things"."""
    sid = acl.namespace(ns)
    out = tempfile.SpooledTemporaryFile(max_size=32 * 1024 * 1024)
    notebook.export(db, sid, out)
    out.seek(0)

    def body():
        try:
            while part := out.read(256 * 1024):
                yield part
        finally:
            out.close()

    name = f"{notebook.file_name(ns)} notes.zip"
    return StreamingResponse(body(), media_type="application/zip", headers={"Content-Disposition": _attachment(name)})


@router.get("/deleted")
def deleted_pages(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    ns: str | None = Query(None, description="only this namespace (default: every one you can read)"),
) -> list[NoteDeleted]:
    """Pages deleted in the last 30 days, the latest first: each can be put back until then."""
    names = store.space_names(db)
    spaces = [acl.namespace(ns)] if ns else (list(names) if user.admin else acl.spaces())
    rows = notebook.trash(db, spaces)
    who = {a: (db.one("SELECT VALUE email FROM $r", r=store.R("account", a)) or None) for a in {r.get("deleted_by") for r in rows} if a}
    return [
        NoteDeleted(
            id=r["id"],
            title=r.get("title") or "Untitled",
            about=r.get("about"),
            namespace=names.get(r["space"], ""),
            deleted_at=r["deleted_at"],
            deleted_by=who.get(r.get("deleted_by")),
        )
        for r in rows
    ]


@router.post("/deleted/{pid}/restore")
def restore_deleted(pid: int, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Put a deleted page back where it was, with its links, files and history (Undo after Delete). Editors."""
    _trashed(acl, db, pid, "editor")
    with domain_errors():
        p = notebook.undelete(db, pid)
    return _out(acl, db, p, user)


@router.delete("/deleted/{pid}")
def purge_deleted(pid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Delete a deleted page for good now, with its files and history, rather than after 30 days. Editors."""
    _trashed(acl, db, pid, "editor")
    with _vault():
        notebook.purge(db, pid, cfg)
    return Ok()


@router.get("/about/{kind}/{key}")
def page_about(kind: str, key: int, user: CurrentUser, acl: Acl, db: Db) -> NotePage | NotePageDraft:
    """The page of an asset, entity, topic, collection or person: its page, or a draft while nobody has written one."""
    thing = f"{kind}:{key}"
    with domain_errors():
        notebook._about(thing)
        sid = notebook.owner(db, kind, key)
    acl.need(sid, "viewer")
    if kind == "recording":
        rec = db.one("SELECT space, collection FROM $r", r=store.R("recording", key))
        acl.need_in(rec["space"], rec.get("collection"))
    p = notebook.about(db, sid, thing)
    if p:
        return _out(acl, db, p, user)
    name = notebook.labels(db, [thing]).get(thing, {}).get("name") or thing
    return NotePageDraft(
        namespace=store.space_names(db).get(sid, ""),
        about=thing,
        title=str(name).rsplit("/", 1)[-1],
        backlinks=_backlinks(acl, db, sid, [thing]),
        can_edit=user.can_write and auth.allows(acl.roles, sid, "editor"),
    )


@router.get("/{pid}")
def get_page(pid: int, user: CurrentUser, acl: Acl, db: Db) -> NotePage:
    """A page with its body, its links (with their targets' current names) and the pages linking to it."""
    return _out(acl, db, _page(acl, db, pid), user)


@router.patch("/{pid}")
def update_page(pid: int, body: NotePageUpdate, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Change what's given. A new body without `doc` keeps the editor's state (drawings on the canvas live there) but
    marks it stale (`doc_stale`), so the editor brings its text in line with the body."""
    _page(acl, db, pid, "editor")
    given = body.model_fields_set
    kw: dict[str, Any] = {k: getattr(body, k) for k in ("summary", "date", "doc") if k in given}
    if body.view is not None:
        kw["view"] = body.view
    if "place" in given:
        kw["place"] = body.place or None
    with domain_errors():
        notebook.update(db, pid, user.id, title=body.title, body=body.body, **kw)
    return _out(acl, db, notebook.get(db, pid), user)


@router.get("/{pid}/suggestions")
def link_suggestions(pid: int, user: CurrentUser, acl: Acl, db: Db) -> list[NoteLinkSuggestion]:
    """What the page names but doesn't link yet: the namespace's topics and named things found in its text, to link
    with one click. Matched against the namespace's own vocabulary; no model is asked."""
    return [NoteLinkSuggestion(**x) for x in notebook.suggest_links(db, _page(acl, db, pid))]


@router.get("/{pid}/homes")
def home_suggestions(pid: int, user: CurrentUser, acl: Acl, db: Db) -> list[NoteHomeSuggestion]:
    """The project or area pages a free note at the top of the tree could go inside, best first, from the links and
    words they share; moving it there is one click (POST /{pid}/move). No model is asked."""
    return [NoteHomeSuggestion(**x) for x in notebook.suggest_homes(db, _page(acl, db, pid))]


def _version_out(db: DB, v: dict[str, Any], cls: type[NoteVersionItem] = NoteVersionItem) -> NoteVersionItem:
    who = db.one("SELECT email FROM $r", r=store.R("account", v["by"])) if v.get("by") else None
    return cls(**{**{k: x for k, x in v.items() if k not in ("by", "page", "doc")}, "by": (who or {}).get("email")})


@router.get(
    "/{pid}/markdown",
    response_class=Response,
    responses={200: {"content": {"text/markdown": {}}, "description": "the page as a Markdown file"}},
)
def page_markdown(pid: int, user: CurrentUser, acl: Acl, db: Db) -> Response:
    """The page as a Markdown file of its own: front matter with its title, date, filing and summary, then its text,
    with links to other pages as [[Title]] wiki links."""
    p = _page(acl, db, pid)
    titles = {r["id"]: r["title"] for r in notebook.tree(db, p["space"], everything=True)}
    return Response(
        notebook.markdown(p, titles),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": _attachment(f"{notebook.file_name(p['title'])}.md")},
    )


@router.get("/{pid}/history")
def page_history(pid: int, user: CurrentUser, acl: Acl, db: Db, limit: int = Query(50, ge=1, le=100)) -> NoteHistory:
    """What the page was before each change to its title, summary or text, newest first."""
    _page(acl, db, pid)
    return NoteHistory(versions=[_version_out(db, v) for v in notebook.history(db, pid, limit)])


@router.get("/{pid}/history/{vid}")
def page_version(pid: int, vid: int, user: CurrentUser, acl: Acl, db: Db) -> NoteVersion:
    """One earlier version, with its text."""
    _page(acl, db, pid)
    try:
        v = notebook.version(db, pid, vid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    return _version_out(db, v, NoteVersion)  # type: ignore[return-value]


@router.post("/{pid}/history/{vid}/restore")
def restore_version(pid: int, vid: int, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Put an earlier version back. What the page was becomes a version too, so this can be undone. Needs editor access."""
    _page(acl, db, pid, "editor")
    try:
        notebook.restore(db, pid, vid, user.id)
    except KeyError:
        raise HTTPException(404, "not found") from None
    return _out(acl, db, notebook.get(db, pid), user)


@router.post("/{pid}/move")
def move_page(pid: int, body: NotePageMove, user: Writer, acl: Acl, db: Db) -> NotePage:
    """Move a free note in the tree: inside `parent` (null: the top), before `before` (null: at the end)."""
    _page(acl, db, pid, "editor")
    with domain_errors():
        notebook.move(db, pid, body.parent, body.before)
    return _out(acl, db, notebook.get(db, pid), user)


@router.put("/{pid}/pin")
def pin_page(pid: int, user: CurrentUser, acl: Acl, db: Db) -> NotePage:
    """Pin the page for you: it shows first on your Notes page and in your tree. Pins are each person's own; anyone
    who can read the page can pin it."""
    _page(acl, db, pid)
    with domain_errors():
        notebook.pin(db, pid, user.id)
    return _out(acl, db, notebook.get(db, pid), user)


@router.delete("/{pid}/pin")
def unpin_page(pid: int, user: CurrentUser, acl: Acl, db: Db) -> NotePage:
    """Unpin the page for you."""
    _page(acl, db, pid)
    notebook.pin(db, pid, user.id, on=False)
    return _out(acl, db, notebook.get(db, pid), user)


@router.delete("/{pid}")
def delete_page(pid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    """Delete a page; the pages inside it move up a level. It can be put back from recently deleted for
    `TRASH_DAYS` (30) days (`POST /notes/deleted/{pid}/restore`). Needs editor access."""
    _page(acl, db, pid, "editor")
    with domain_errors():
        notebook.delete(db, pid, user.id)
    return Ok()


# ---------- files on a page ----------
@contextlib.contextmanager
def _vault():
    try:
        yield
    except keyring.Locked:
        raise HTTPException(
            423, "this namespace is a locked vault: its owners unlock it with a passkey, on its page in Admin, Namespaces"
        ) from None
    except RuntimeError as e:  # where files are kept didn't answer (Settings → Storage)
        raise HTTPException(502, f"where files are kept didn't answer: {e}") from None


SAFE_INLINE = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "application/pdf"}


@router.get("/{pid}/files")
def page_files(pid: int, user: CurrentUser, acl: Acl, db: Db) -> list[NoteFile]:
    """The page's files (images and attachments in its editor), oldest first."""
    _page(acl, db, pid)
    return [NoteFile(**f) for f in notebook.files(db, pid)]


@router.put("/{pid}/blobs", openapi_extra=CHUNK)
async def put_blob(
    pid: int,
    request: Request,
    user: Writer,
    acl: Acl,
    db: Db,
    cfg: Cfg,
    key: str = Query(min_length=8, max_length=128, description="the editor's key for it"),
    name: str | None = Query(None, max_length=255, description="its file name, when it has one"),
) -> NoteFile:
    """Keep a file the page's editor holds (an image, an attachment) as the raw request body, up to
    server.max_upload_mb, where Settings → Storage says. Encrypted before it leaves the machine. Editors."""
    p = await run_in_threadpool(_page, acl, db, pid, "editor")
    path = await _receive(request, cfg)
    try:
        with domain_errors(), _vault():
            f = await run_in_threadpool(notebook.add_file, db, cfg, p, key, path, name, request.headers.get("content-type"), user.id)
    finally:
        path.unlink(missing_ok=True)
    await run_in_threadpool(auth.audit, db, user.as_audit(), "note.file", f"note_page:{pid}", {"key": key, "size": f["size"]})
    return NoteFile(**f)


@router.get("/{pid}/blobs", response_class=StreamingResponse, responses={200: {"content": {"application/octet-stream": {}}}})
def get_blob(pid: int, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg, key: str = Query(min_length=8, max_length=128)) -> StreamingResponse:
    """A file of the page, as it was kept. Images and PDFs show in place; anything a browser could run comes as bytes."""
    _page(acl, db, pid)
    with domain_errors(), _vault():
        row, opened = notebook.open_file(db, cfg, pid, key)
        f = opened.__enter__()
    ctype = resource_files.served_type({"content_type": row.get("type")})
    inline = ctype in SAFE_INLINE
    name = row.get("name") or "file"
    ascii_name = name.encode("ascii", "replace").decode().replace('"', "").replace("?", "_")

    def body():
        try:
            while part := f.read(256 * 1024):
                yield part
        finally:
            opened.__exit__(None, None, None)

    disposition = "inline" if inline else "attachment"
    headers = {
        **resource_files.HEADERS,
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "private, max-age=86400",
    }
    return StreamingResponse(body(), media_type=ctype, headers=headers)


@router.delete("/{pid}/files")
def delete_page_file(pid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg, key: str = Query(min_length=8, max_length=128)) -> Ok:
    """Remove a file from the page and from where it's kept. Editors."""
    _page(acl, db, pid, "editor")
    with domain_errors(), _vault():
        notebook.delete_file(db, cfg, pid, key)
    auth.audit(db, user.as_audit(), "note.file.delete", f"note_page:{pid}", {"key": key})
    return Ok()
