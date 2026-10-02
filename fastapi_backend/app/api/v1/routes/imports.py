"""Importing transcripts: an uploaded file (base64 in JSON) or pasted text, and a preview that saves nothing; and
chosen files of a storage source."""

from __future__ import annotations

import base64
import binascii
import pathlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, AdminWriter, Cfg, Db, Writer, domain_errors
from app.domain import auth, convert, ingest, jobs, pipelines, sources, store, webcapture
from app.domain.store import DB
from app.schemas.imports import (
    ImportPreview,
    ImportPreviewRequest,
    ImportRequest,
    ImportResult,
    PreviewLine,
    SourceImport,
    SourceImportRequest,
    SourceImportResult,
    WebImportRequest,
)

router = APIRouter(prefix="/import", tags=["imports"])

IMPORT_EXT = {".txt", ".md", ".markdown", ".mdx", ".docx", ".doc", ".pdf", ".json", ".jsonl", ".srt", ".vtt", ".eml", ".ics"}


def _upload_name(filename: str | None) -> str:
    name = pathlib.Path(str(filename or "upload.txt")).name
    if pathlib.Path(name).suffix.lower() not in IMPORT_EXT:
        raise HTTPException(400, f"unsupported file type; use one of {', '.join(sorted(IMPORT_EXT))}")
    return name


def _decode(data: str, cfg: dict[str, Any]) -> bytes:
    try:
        raw = base64.b64decode(data, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(400, "file data is not valid base64") from None
    mb = cfg["server"]["max_upload_mb"]
    if len(raw) > mb * 1024 * 1024:
        raise HTTPException(413, f"files up to {mb} MB")
    return raw


def check_pipeline(db: Any, pid: int | None) -> None:
    """A pipeline chosen to run after an import must exist (400 before anything is saved)."""
    if pid is None:
        return
    try:
        pipelines.get(db, pid)
    except KeyError:
        raise HTTPException(400, "there's no such pipeline") from None


@contextmanager
def _unreadable() -> Iterator[None]:
    try:
        yield
    except SystemExit as e:
        raise HTTPException(400, str(e)) from None
    except (ValueError, KeyError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"could not read that transcript: {e}") from None


def check_collection(db: DB, sid: int | None, collection: int | None) -> None:
    """A collection asked for at import has to be one of the namespace's (404 otherwise; a new namespace has none)."""
    if collection is None:
        return
    try:
        if sid is None:
            raise KeyError(collection)
        store.home(db, sid, collection)
    except KeyError:
        raise HTTPException(404, "there's no such collection in that namespace") from None


@router.post("")
def import_transcript(body: ImportRequest, request: Request, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> ImportResult:
    """Import a transcript into a namespace (editors; admins may name a new namespace). The namespace's pipeline, or
    the one chosen (`pipeline`), is queued as a job."""
    ns = body.namespace.strip()
    if not store.NS_RX.match(ns):
        raise HTTPException(400, "choose a namespace: lowercase letters, digits, - and _")
    check_pipeline(db, body.pipeline)
    try:
        sid = store.ns_id(db, ns, create=False)
        acl.need(sid, "editor")
    except KeyError:
        if not user.admin:
            raise HTTPException(403, "only admins can create namespaces") from None
        sid = None
    check_collection(db, sid, body.collection)
    names = dict(kv.strip().split("=", 1) for kv in str(body.speakers or "").split(",") if "=" in kv) or None
    title = (body.title or "").strip()[:200] or None
    with _unreadable():
        if body.data:
            name = _upload_name(body.filename)
            raw = _decode(body.data, cfg)
            with tempfile.TemporaryDirectory() as d:
                path = pathlib.Path(d) / name
                path.write_bytes(raw)
                rid = ingest.import_transcript(
                    db, cfg, ns, path, title=title or path.stem, speaker_names=names, fmt=body.format, collection=body.collection
                )
            db.q("UPDATE $r SET path = $p", r=store.R("recording", rid), p="upload:" + name)
        else:
            rid = ingest.import_text(
                db, cfg, ns, body.text or "", title=title, fmt=body.format, speaker_names=names, collection=body.collection
            )
    job = jobs.enqueue(db, rid, None, by=user.email, pipeline=body.pipeline)
    auth.audit(db, user.as_audit(), "import", f"recording:{rid}")
    request.app.state.graph_cache.clear()
    return ImportResult(id=rid, job=job)


@router.post("/web")
def import_web_page(body: WebImportRequest, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> ImportResult:
    """Capture a web page as a document (editors; admins may name a new namespace): the namespace's pipeline, or
    `pipeline`, keeps the page as it is now, as a PDF (a link to a PDF is kept as it is; other pages are printed by
    headless Chromium), and reads it page by page. Only public addresses on ports 80 and 443 can be captured (and the
    networks in documents.web_networks). Audited as `import.web`."""
    ns = body.namespace.strip()
    if not store.NS_RX.match(ns):
        raise HTTPException(400, "choose a namespace: lowercase letters, digits, - and _")
    if not convert.chromium(cfg):
        raise HTTPException(400, "capturing web pages needs Chromium on the server (the lens:full image)")
    check_pipeline(db, body.pipeline)
    try:
        sid = store.ns_id(db, ns, create=False)
        acl.need(sid, "editor")
    except KeyError:
        if not user.admin:
            raise HTTPException(403, "only admins can create namespaces") from None
        sid = None
    check_collection(db, sid, body.collection)
    try:
        url = webcapture.check_url(cfg, body.url)
    except ValueError as e:
        raise HTTPException(400, str(e)) from None
    sid = sid if sid is not None else store.ns_id(db, ns)
    rid = webcapture.create(db, sid, url, body.title, body.collection, by=user.email)
    job = jobs.enqueue(db, rid, None, by=user.email, pipeline=body.pipeline)
    auth.audit(db, user.as_audit(), "import.web", f"recording:{rid}", {"url": url, "namespace": ns})
    return ImportResult(id=rid, job=job)


@router.post("/preview")
def preview_import(body: ImportPreviewRequest, user: Writer, cfg: Cfg) -> ImportPreview:
    """Parse without saving: what the importer would make of this text or file."""
    fmt = body.format
    with _unreadable():
        if body.data:
            name = _upload_name(body.filename)
            raw = _decode(body.data, cfg)
            with tempfile.TemporaryDirectory() as d:
                path = pathlib.Path(d) / name
                path.write_bytes(raw)
                t = ingest.read_transcript(path, fmt)
            detected = fmt if fmt != "auto" else pathlib.Path(name).suffix.lower().lstrip(".")
        else:
            text = body.text or ""
            t, detected = ingest.read_text_transcript(text, fmt), fmt if fmt != "auto" else ingest.sniff(text)
    segs = t["segments"]
    return ImportPreview(
        format=detected,
        title=t.get("title"),
        segments=len(segs),
        duration_ms=max((x["t1"] for x in segs), default=0),
        speakers=sorted({x.get("speaker") for x in segs if x.get("speaker")}),
        preview=[PreviewLine(time=store.tc(x["t0"]), speaker=x.get("speaker"), text=x["text"]) for x in segs[:25]],
    )


@router.post("/source")
def import_from_source(body: SourceImportRequest, request: Request, user: AdminWriter, db: Db, cfg: Cfg) -> SourceImport:
    """Import chosen files of a storage source into a namespace now, rather than watching their folder (admins, like
    sources; a new namespace is created). Audio, video, documents (PDFs) and images stay on the source and run the
    namespace's pipeline, or `pipeline`; transcripts are imported, and PDFs, Word and text files too with
    `documents_as: transcript`. Each file
    gets a result: queued, already (the namespace has it from this source), skipped (not audio, video, a document, an
    image or a transcript) or error. Audited as `import.source`."""
    ns = body.namespace.strip()
    if not store.NS_RX.match(ns):
        raise HTTPException(400, "choose a namespace: lowercase letters, digits, - and _")
    check_pipeline(db, body.pipeline)
    try:
        sources.get(db, body.source)
    except KeyError:
        raise HTTPException(400, "there's no such source") from None
    try:
        sid = store.ns_id(db, ns, create=False)
    except KeyError:
        sid = None
    check_collection(db, sid, body.collection)  # before a new namespace is made
    sid = sid if sid is not None else store.ns_id(db, ns)
    with domain_errors():
        results = sources.import_files(db, cfg, body.source, body.paths, sid, user.email, body.pipeline, body.collection, body.documents_as)
    queued = [r for r in results if r["status"] == "queued"]
    if queued:
        detail = {"namespace": ns, "files": len(queued), "recordings": [r["recording"] for r in queued]}
        auth.audit(db, user.as_audit(), "import.source", f"source:{body.source}", detail)
        request.app.state.graph_cache.clear()
    return SourceImport(results=[SourceImportResult(**r) for r in results])
