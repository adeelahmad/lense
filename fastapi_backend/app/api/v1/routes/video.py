"""Video: the media file and its frames, corrections to text on screen, and people on screen.

Face detection and recognition are off until a namespace owner turns them on and, for recognition, says what it is
for. Descriptors (face prints) exist only while recognition is on; turning faces off removes every face and track.
Media and frames are served to anyone the recording is visible to: a bearer token with a role in its namespace, a
share link (``?s=``) or a signed link from a JSON response.
"""

from __future__ import annotations

import pathlib
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, Response

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Track, Writer, domain_errors
from app.api.media import sign_urls
from app.api.v1.routes.recordings import serve_audio
from app.domain import auth, convert, documents, files, ingest, jobs, store, video
from app.domain import faces as facemod
from app.domain.store import API, DB, R
from app.schemas.common import Ok
from app.schemas.video import (
    FaceDismiss,
    FaceMerge,
    FaceMerged,
    FaceRename,
    FacesMode,
    FacesModeSet,
    FaceSpeaker,
    NamespaceFaces,
    OcrFix,
)

router = APIRouter(tags=["video"])

FRAME_RX = re.compile(r"[\w.-]+\.jpg")


# ---------- media and frames ----------
@router.get(
    "/recordings/{rid}/media",
    response_class=Response,
    responses={
        200: {"content": {"video/*": {}, "audio/*": {}, "application/pdf": {}, "image/*": {}}},
        206: {"description": "a byte range"},
    },
)
def get_media(rid: int, request: Request, acl: Acl, db: Db, cfg: Cfg, track: Track, s: str = "") -> Response:
    """The video or audio file, with byte ranges; a document's or an image's file, to save. Accepts a bearer token, a
    share link (``?s=``) or a signed link."""
    rec = acl.recording(rid, share=s)
    if rec.get("source") in documents.KINDS:
        track("download", rid, rec["space"], rec.get("collection"))  # a file to save; audio and video are played
        return serve_document(db, cfg, rec)
    return serve_audio(db, cfg, rec, rid, request)


def serve_document(db: DB, cfg: dict[str, Any], rec: dict[str, Any]) -> FileResponse:
    """A document's or an image's own file, as a download that browsers never run."""
    with domain_errors():  # its source was removed (404) or the path is no longer allowed (400)
        path = ingest.audio_path(db, cfg, rec)
    if not path or not pathlib.Path(path).is_file():
        raise HTTPException(404, "the file is missing on the server")
    name = pathlib.PurePosixPath(str((rec.get("remote") or {}).get("path") or path)).name
    ctype = documents.content_type(name) or "application/octet-stream"
    return FileResponse(path, media_type=ctype, filename=name, headers=files.HEADERS)


@router.get("/recordings/{rid}/pdf", response_class=FileResponse, responses={200: {"content": {"application/pdf": {}}}})
def get_pdf(rid: int, acl: Acl, cfg: Cfg, track: Track, s: str = "") -> FileResponse:
    """The PDF made of a document that isn't one (a Word file, an email, …): what its pages are drawn from, to save.
    Accepts a bearer token, a share link (``?s=``) or a signed link."""
    rec = acl.recording(rid, share=s)
    path = convert.rendition_path(cfg, rid)
    if rec.get("source") != "document" or not path.is_file():
        raise HTTPException(404, "not found")
    track("download", rid, rec["space"], rec.get("collection"))
    name = pathlib.PurePosixPath(str((rec.get("remote") or {}).get("path") or rec.get("path") or "document")).stem or "document"
    return FileResponse(path, media_type="application/pdf", filename=f"{name}.pdf", headers=files.HEADERS)


@router.get("/recordings/{rid}/frames/{name}", response_class=FileResponse, responses={200: {"content": {"image/jpeg": {}}}})
def get_frame(rid: int, name: str, acl: Acl, cfg: Cfg, db: Db, s: str = "") -> Response:
    """A still (shot frame, text-on-screen frame or face crop), or a document's or an image's page. Where the namespace
    pixelates faces, a visitor (no role in the namespace, nor a link the API signed for a member) gets the faces found
    on it pixelated."""
    rec = acl.recording(rid, share=s)
    p = video.frames_dir(cfg, rid) / name
    if not FRAME_RX.fullmatch(name) or not p.is_file():
        raise HTTPException(404, "not found")
    return _picture(db, rec, rid, name, p, acl.member(rec))


def _picture(db: DB, rec: dict[str, Any], rid: int, name: str, path: pathlib.Path, member: bool) -> Response:
    headers = {"Cache-Control": "private, max-age=3600"}
    if not member and facemod.pixelates(db, rec["space"]):
        data = facemod.pixelated(db, path, rid, name)
        if data is not None:
            return Response(data, media_type="image/jpeg", headers={**headers, "Vary": "Authorization"})
    return FileResponse(path, media_type="image/jpeg", headers=headers)


# ---------- corrections on one recording ----------
@router.patch("/recordings/{rid}/ocr/{span}")
def fix_screen_text(rid: int, span: str, body: OcrFix, user: Writer, acl: Acl, db: Db) -> Ok:
    """Correct text read off the screen (the machine's reading is kept as `machine_text`)."""
    acl.recording(rid, "editor")
    row = db.one("SELECT recording, text FROM $r", r=R("ocr_span", span))
    text = body.text.strip()
    if not row or row["recording"] != rid:
        raise HTTPException(404, "not found")
    if not text:
        raise HTTPException(400, "the text can't be empty")
    db.q("UPDATE $r SET text = $t, edited = true, machine_text = $m", r=R("ocr_span", span), t=text[:2000], m=row["text"])
    auth.audit(db, user.as_audit(), "ocr.fix", f"recording:{rid}", {"span": span})
    return Ok()


@router.delete("/recordings/{rid}/faces/{track}")
def delete_face_track(rid: int, track: str, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Not a face: remove this track (and its crop) from the recording."""
    acl.recording(rid, "editor")
    row = db.one("SELECT recording, cover FROM $r", r=R("face_track", track))
    if not row or row["recording"] != rid:
        raise HTTPException(404, "not found")
    if row.get("cover"):
        (video.frames_dir(cfg, rid) / row["cover"]).unlink(missing_ok=True)
    db.q("DELETE $r", r=R("face_track", track))
    auth.audit(db, user.as_audit(), "face.not_a_face", f"recording:{rid}")
    return Ok()


# ---------- people on screen, per namespace ----------
def _face_space(db: DB, acl: Access, fid: int, role: str = "viewer") -> int:
    row = db.one("SELECT space FROM $r", r=R("face", int(fid)))
    if not row:
        raise HTTPException(404, "not found")
    acl.need(row["space"], role)
    return row["space"]


@router.get("/namespaces/{name}/faces")
def get_namespace_faces(name: str, user: CurrentUser, acl: Acl, db: Db) -> NamespaceFaces:
    """The face mode (with who set it and why), the faces found (with suggested names) and recent merges.

    Each face's `cover_url` is a signed link to its crop.
    """
    sid = acl.namespace(name)
    sp = db.one("SELECT faces_mode, faces_pixelate, faces_purpose, faces_set_by, faces_set_at FROM $s", s=R("space", sid)) or {}
    mode = sp.get("faces_mode") or "off"
    merges = db.rows(
        "SELECT record::id(id) AS id, src, dst, by, at, undone FROM face_merge WHERE space = $s ORDER BY at DESC LIMIT 30", s=sid
    )
    faces = facemod.list_faces(db, sid) if mode != "off" else []
    for f in faces:
        c = f.get("cover")
        f["cover_url"] = f"{API}/recordings/{c['recording']}/frames/{c['file']}" if c else None
    out = {
        "mode": mode,
        "pixelate": bool(sp.get("faces_pixelate")),
        "purpose": sp.get("faces_purpose"),
        "set_by": sp.get("faces_set_by"),
        "set_at": sp.get("faces_set_at"),
        "faces": faces,
        "merges": merges,
    }
    return NamespaceFaces.model_validate(sign_urls(out, full=True))


@router.put("/namespaces/{name}/faces/mode")
def set_namespace_faces_mode(name: str, body: FacesMode, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> FacesModeSet:
    """off, detect or recognize (which needs a purpose), and whether faces are pixelated for visitors. Owners only.
    `reprocess` queues the namespace's videos, documents and images (faces on their pages)."""
    sid = acl.namespace(name, "owner")
    if body.mode is None and body.pixelate is None:
        raise HTTPException(400, "say the mode, or whether to pixelate faces")
    with domain_errors():
        if body.mode is not None:
            facemod.set_mode(db, sid, body.mode, body.purpose, user.email, cfg)
        if body.pixelate is not None:
            facemod.set_pixelate(db, sid, body.pixelate)
    auth.audit(
        db, user.as_audit(), "faces.mode", name, store.clean({"mode": body.mode, "purpose": body.purpose, "pixelate": body.pixelate})
    )
    queued = []
    if body.reprocess and body.mode and body.mode != "off":
        for r in db.rows("SELECT record::id(id) AS id, media FROM recording WHERE space = $s", s=sid):
            if (r.get("media") or {}).get("kind") in ("video", "document", "image"):
                queued.append(jobs.enqueue(db, r["id"], ["faces"], by=user.email))
    return FacesModeSet(jobs=queued)


@router.delete("/namespaces/{name}/faces")
def delete_namespace_faces(name: str, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Delete every face, track and crop in the namespace. Owners only."""
    sid = acl.namespace(name, "owner")
    facemod.delete_namespace(db, cfg, sid)
    auth.audit(db, user.as_audit(), "faces.delete_all", name)
    return Ok()


@router.post("/faces/merges/{mid}/undo")
def undo_face_merge(mid: int, user: Writer, acl: Acl, db: Db) -> Ok:
    m = db.one("SELECT space FROM $r", r=R("face_merge", mid))
    if not m:
        raise HTTPException(404, "not found")
    acl.need(m["space"], "editor")
    with domain_errors():
        facemod.undo(db, mid)
    auth.audit(db, user.as_audit(), "face.merge.undo", f"face_merge:{mid}")
    return Ok()


@router.post("/faces/{fid}")
def rename_face(fid: int, body: FaceRename, user: Writer, acl: Acl, db: Db) -> Ok:
    _face_space(db, acl, fid, "editor")
    facemod.rename(db, fid, body.name)
    auth.audit(db, user.as_audit(), "face.rename", f"face:{fid}")
    return Ok()


@router.post("/faces/{fid}/merge")
def merge_face(fid: int, body: FaceMerge, user: Writer, acl: Acl, db: Db) -> FaceMerged:
    """Merge this face into another (the same person)."""
    _face_space(db, acl, fid, "editor")
    _face_space(db, acl, body.into, "editor")
    try:
        mid = facemod.merge(db, fid, body.into, user.email)
    except (ValueError, KeyError) as e:
        raise HTTPException(400, str(e)) from None
    auth.audit(db, user.as_audit(), "face.merge", f"face:{fid}", {"into": body.into})
    return FaceMerged(merge=mid)


@router.post("/faces/{fid}/speaker")
def link_face_speaker(fid: int, body: FaceSpeaker, user: Writer, acl: Acl, db: Db) -> Ok:
    """Say whose voice goes with this face (null unlinks)."""
    _face_space(db, acl, fid, "editor")
    with domain_errors():
        facemod.link_speaker(db, fid, body.speaker)
    auth.audit(db, user.as_audit(), "face.speaker", f"face:{fid}", {"speaker": body.speaker})
    return Ok()


@router.post("/faces/{fid}/dismiss")
def dismiss_face_suggestion(fid: int, body: FaceDismiss, user: Writer, acl: Acl, db: Db) -> Ok:
    """Drop a suggestion (`kind` face or speaker, with its id)."""
    _face_space(db, acl, fid, "editor")
    facemod.dismiss(db, fid, body.kind or "face", body.id)
    return Ok()


@router.delete("/faces/{fid}")
def delete_face(fid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Delete a face with its tracks and crops. Owners only."""
    _face_space(db, acl, fid, "owner")
    facemod.delete_face(db, cfg, fid)
    auth.audit(db, user.as_audit(), "face.delete", f"face:{fid}")
    return Ok()
