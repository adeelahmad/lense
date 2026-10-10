"""Deleting a recording: everything Lens made from it goes, the media file stays where it is, and scans and watched
folders don't import it again (docs/api.md).

What goes: its transcript and everything derived from it (segments, speakers' appearances, chapters, entities'
mentions, terms, edits), video shots, text on screen and face tracks with their frames, the pages drawn of documents
and images, outputs and reports, shares,
notes, comments, highlights, supplementary files, permissions, requests for access, and its place in IP groups, fixed collections, chat
scopes and batch runs that haven't started it. Speakers and faces that only it had, and that nobody named, go too; named ones stay. Its jobs
are cancelled (a job that is running has to stop first), and harvesters hear a Delete when it was public.

What stays: the media file, the audit log, IIIF change discovery, and a note (`gone_recording`) of its path,
fingerprint and remote file, which scans and watched folders check. Importing the file on purpose (the Import dialog,
`lens import`, a IIIF manifest) brings it back and clears the note. Moving a recording to another namespace leaves the
same note in the namespace it left (domain/moving.py).
"""

from __future__ import annotations

import pathlib
import shutil

from . import access as acc, files as filemod, notebook, render, store, video

R = store.R
# rows that belong to the recording
OWN = (
    "mentions",
    "term",
    "topic_about",
    "section",
    "appearance",
    "segment",
    "output",
    "segment_edit",
    "entity_override",
    "shot",
    "ocr_span",
    "face_track",
    "object_track",
    "description",
    "share_link",
    "share_embed",
    "note",
    "comment",
    "highlight",
    "permission",
    "access_request",
    "resource_file",
    "file_line",
    "page",
    "passage",
    "photo_vector",
    "podcast",
)


class Running(RuntimeError):
    """A job is working on the recording; it has to stop before the recording can go."""


def _running(db, rid):
    return db.values("SELECT VALUE record::id(id) FROM job WHERE recording = $r AND status = 'running'", r=rid)


def stop_jobs(db, rid, verb="delete"):
    """Cancel the recording's waiting jobs. Raises Running when one is running: it is asked to stop after its step."""
    if _running(db, rid):
        raise Running(f"A job is working on it. Cancel it in Activity, or wait until it finishes, then {verb} it.")
    t = store.now()
    db.q(
        "UPDATE job SET status = 'cancelled', finished_at = $t, updated_at = $t WHERE recording = $r AND status IN ['queued', 'paused']",
        r=rid,
        t=t,
    )
    started = _running(db, rid)  # a worker took one before it was cancelled
    if started:
        db.q("UPDATE job SET cancel_requested = true, updated_at = $t WHERE id IN $j", j=[R("job", j) for j in started], t=t)
        raise Running("A job just started on it and stops after its current step. Try again then.")


def _files(db, cfg, rec, rid, ns):
    """The files Lens made from the recording: its frames, its report pages, the PDF made of a document, the exports
    only it wrote, and a cached copy of a remote file that no other recording uses. Never the media file."""
    data = pathlib.Path(cfg["data_dir"])
    reports = data / "reports" / (ns or "_")
    name = f"{render.slug(rec.get('title'))}-{rid}"
    out = [reports / f"{name}.html", *reports.glob(f"{name}--*.html"), data / "renditions" / f"{int(rid)}.pdf"]
    exports = data / "exports" / (ns or "_")
    mine = {
        (o.get("value") or {}).get("file")
        for o in db.rows("SELECT key, value FROM output WHERE recording = $r AND string::starts_with(key, 'export_')", r=rid)
    }
    for f in sorted(mine - {None}):
        shared = db.values(
            "SELECT VALUE id FROM output WHERE recording != $r AND value.file = $f AND recording IN "
            "(SELECT VALUE record::id(id) FROM recording WHERE space = $s) LIMIT 1",
            r=rid,
            f=f,
            s=rec["space"],
        )
        if not shared:
            out.append(exports / pathlib.PurePosixPath(f).name)
    remote = rec.get("remote") or {}
    if remote.get("source") is not None and remote.get("path"):
        from . import sources

        others = db.rows(
            "SELECT space FROM recording WHERE remote.source = $s AND remote.path = $p AND id != $r",
            s=remote["source"],
            p=remote["path"],
            r=R("recording", rid),
        )
        if not any(o.get("space") == rec.get("space") for o in others):  # the copy kept for its namespace
            out.append(sources.cache_file(cfg, remote["source"], remote["path"], rec.get("space")))
        if not others:  # and one from before copies were kept per namespace
            out.append(sources.cache_file(cfg, remote["source"], remote["path"]))
    return out


def orphans(db, speakers, faces):
    """Speakers and faces nothing else has, which nobody named: they only existed for the deleted recording."""
    for s in sorted(speakers):
        r = R("speaker", s)
        left = (
            db.values("SELECT VALUE id FROM segment WHERE speaker = $s LIMIT 1", s=s)
            or db.values("SELECT VALUE id FROM appearance WHERE speaker = $s LIMIT 1", s=s)
            or db.values("SELECT VALUE id FROM same_as WHERE in = $r OR out = $r LIMIT 1", r=r)  # linked by someone
        )
        row = db.one("SELECT name FROM $r", r=r)
        if left or not row or row.get("name"):
            continue
        db.run(
            [
                "DELETE suggestion WHERE speaker = $s OR candidate = $s",
                "DELETE same_as WHERE in = $r OR out = $r",
                "DELETE $r",
            ],
            s=s,
            r=R("speaker", s),
        )
    for f in sorted(faces):
        left = db.values("SELECT VALUE id FROM face_track WHERE face = $f LIMIT 1", f=f)
        row = db.one("SELECT name, speaker FROM $r", r=R("face", f))
        if left or not row or row.get("name") or row.get("speaker"):
            continue
        db.run(["DELETE face_suggestion WHERE face = $f OR candidate = $f", "DELETE $r"], f=f, r=R("face", f))


def delete(db, cfg, rid, by=None):
    """Delete a recording (see the module docstring). Returns what the audit log keeps of it. Raises KeyError when there
    is no such recording and Running when a job is working on it."""
    rec = db.one("SELECT title, space, path, fingerprint, remote FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    stop_jobs(db, rid)
    a = acc.of(db, rid)
    ns = store.space_names(db).get(rec["space"])
    speakers = set(db.values("SELECT VALUE speaker FROM appearance WHERE recording = $r", r=rid)) | {
        s for s in db.values("SELECT VALUE speaker FROM segment WHERE recording = $r AND speaker > 0", r=rid) if s
    }
    faces = {f for f in db.values("SELECT VALUE face FROM face_track WHERE recording = $r AND face > 0", r=rid) if f}
    files = _files(db, cfg, rec, rid, ns)
    db.run(
        [f"DELETE {t} WHERE recording = $r" for t in OWN]
        + [
            "CREATE $g CONTENT $note",
            "DELETE meta_edit WHERE target = $target",
            "UPDATE ip_group SET recordings = array::complement(recordings, [$r]) WHERE recordings CONTAINS $r",
            "UPDATE saved_collection SET recordings = array::complement(recordings, [$r]) WHERE recordings CONTAINS $r",
            "UPDATE chat SET scope.recordings = array::complement(scope.recordings, [$r]) WHERE scope.recordings CONTAINS $r",
            "UPDATE batch SET recordings = array::complement(recordings, [$r]) WHERE recordings CONTAINS $r AND started CONTAINSNOT $r",
            "UPDATE resource_file SET resource = NONE WHERE resource = $r",  # an email's attachment that this was
            "DELETE $rec",
        ],
        r=rid,
        g=R("gone_recording", db.next_id("gone_recording")),
        note=note(rec, rid, "deleted", by),
        target=f"recording:{rid}",
        rec=R("recording", rid),
    )
    notebook.release(db, f"recording:{rid}")
    shutil.rmtree(video.frames_dir(cfg, rid), ignore_errors=True)
    shutil.rmtree(filemod.folder(cfg, rid), ignore_errors=True)
    shutil.rmtree(pathlib.Path(cfg["data_dir"]) / "podcasts" / str(int(rid)), ignore_errors=True)  # an episode's audio
    for f in files:
        f.unlink(missing_ok=True)
    orphans(db, speakers, faces)
    if acc.published(a):
        acc.activity(db, rid, "Delete")  # harvesters drop it
    return store.clean({"title": rec.get("title"), "namespace": ns, "path": rec.get("path"), "access": a["access"]})


# ---------- what imports check ----------
def note(rec, rid, why, by=None, **more):
    """What a namespace remembers of a recording that left it (deleted, or moved): its scans and watches skip the file."""
    return store.clean(
        {
            "recording": rid,
            "space": rec["space"],
            "why": why,
            "path": rec.get("path"),
            "fingerprint": rec.get("fingerprint"),
            "remote": rec.get("remote"),
            "title": rec.get("title"),
            "by": by,
            "at": store.now(),
            **more,
        }
    )


def gone(db, space):
    """The paths and fingerprints of the recordings that left a namespace, which a scan skips."""
    rows = db.rows("SELECT path, fingerprint FROM gone_recording WHERE space = $s", s=space)
    return {r["path"] for r in rows if r.get("path")}, {r["fingerprint"] for r in rows if r.get("fingerprint")}


def gone_remote(db, space):
    """The remote files (source id, path) of the recordings that left a namespace, which watched folders skip."""
    rows = db.rows("SELECT remote FROM gone_recording WHERE space = $s AND remote != NONE", s=space)
    return {(r["remote"].get("source"), r["remote"].get("path")) for r in rows}


def forget(db, space, fingerprint=None, path=None):
    """Someone imports a file into a namespace on purpose: it may come back, and scans may find it again."""
    db.q(
        "DELETE gone_recording WHERE space = $s AND ((fingerprint != NONE AND fingerprint = $f) OR (path != NONE AND path = $p))",
        s=space,
        f=fingerprint,
        p=path,
    )
