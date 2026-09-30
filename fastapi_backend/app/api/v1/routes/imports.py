"""Importing transcripts: an uploaded file (base64 in JSON) or pasted text, and a preview that saves nothing."""

from __future__ import annotations

import base64
import binascii
import pathlib
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from fastapi import APIRouter, HTTPException, Request

from app.api.deps import Acl, Cfg, Db, Writer
from app.domain import auth, ingest, jobs, store
from app.schemas.imports import ImportPreview, ImportPreviewRequest, ImportRequest, ImportResult, PreviewLine

router = APIRouter(prefix="/import", tags=["imports"])

IMPORT_EXT = {".txt", ".md", ".markdown", ".mdx", ".docx", ".doc", ".pdf", ".json", ".jsonl", ".srt", ".vtt"}


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


@contextmanager
def _unreadable() -> Iterator[None]:
    try:
        yield
    except SystemExit as e:
        raise HTTPException(400, str(e)) from None
    except (ValueError, KeyError, UnicodeDecodeError) as e:
        raise HTTPException(400, f"could not read that transcript: {e}") from None


@router.post("")
def import_transcript(body: ImportRequest, request: Request, acl: Acl, user: Writer, db: Db, cfg: Cfg) -> ImportResult:
    """Import a transcript into a namespace (editors; admins may name a new namespace). Analysis is queued as a job."""
    ns = body.namespace.strip()
    if not store.NS_RX.match(ns):
        raise HTTPException(400, "choose a namespace: lowercase letters, digits, - and _")
    try:
        acl.need(store.ns_id(db, ns, create=False), "editor")
    except KeyError:
        if not user.admin:
            raise HTTPException(403, "only admins can create namespaces") from None
    names = dict(kv.strip().split("=", 1) for kv in str(body.speakers or "").split(",") if "=" in kv) or None
    title = (body.title or "").strip()[:200] or None
    with _unreadable():
        if body.data:
            name = _upload_name(body.filename)
            raw = _decode(body.data, cfg)
            with tempfile.TemporaryDirectory() as d:
                path = pathlib.Path(d) / name
                path.write_bytes(raw)
                rid = ingest.import_transcript(db, cfg, ns, path, title=title or path.stem, speaker_names=names, fmt=body.format)
            db.q("UPDATE $r SET path = $p", r=store.R("recording", rid), p="upload:" + name)
        else:
            rid = ingest.import_text(db, cfg, ns, body.text or "", title=title, fmt=body.format, speaker_names=names)
    job = jobs.enqueue(db, rid, None, by=user.email)
    auth.audit(db, user.as_audit(), "import", f"recording:{rid}")
    request.app.state.graph_cache.clear()
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
