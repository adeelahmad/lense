"""Evidence PDFs (docs/api.md#evidence-pdfs): a recording, or a collection of them, as one PDF to hand on, made by
anytopdf (anytopdf.py). Each resource's own file goes in as it is (a document, an image, a captured web page, the audio
or video with Lens's transcript of it), and anytopdf ends the PDF with a provenance page that gives every source's
SHA-256 and size, so whoever gets it can check each page against the file it came from. Its text is searchable and its
chunks are embedded (PDF/A-3), so what an agent cites from it stays cited.

A resource without a file of its own (an imported transcript) goes in as its text. After each resource's file come
the pages of what Lens found about it (evidence_report.py): its details, metadata, tags, topics, summary, analysis,
sections, speakers, emotion and sounds, entities, keywords, what is seen in it and the places and dates in it, each a
part that can be left out (`parts`); the notes, highlights and comments people shared on it go in when asked for.
Nothing is kept: the PDF is made when asked for, from plain copies in a folder of its own that is removed straight
after.
"""

from __future__ import annotations

import pathlib
import re
import shutil
import tempfile

from . import anytopdf, evidence_report, ingest, keyring, render, store

R = store.R
MAX = 50  # recordings in one evidence PDF, at most


class Unavailable(RuntimeError):
    """Evidence PDFs can't be made here: anytopdf isn't allowed (documents.converter lens) or has no build for it."""


def _name(i, title):
    """A file name that keeps its place in the PDF and says what it is, with nothing a path could misread."""
    plain = re.sub(r"[^\w .,()&'-]+", " ", title or "", flags=re.UNICODE)
    plain = re.sub(r"\s+", " ", plain).strip(" .")[:80] or "recording"
    return f"{i:02d} {plain}"


def ready(cfg, say=None):
    """Make sure anytopdf can run: here, fetched on first use unless documents.converter is `lens`."""
    if anytopdf.available(cfg):
        return
    if anytopdf.mode(cfg) == "lens":
        raise Unavailable("evidence PDFs are made by anytopdf, and Settings › Documents says never to use it")
    if not anytopdf.archive():
        raise Unavailable("anytopdf has no build for this server; set a conversion node in Settings › Documents")
    anytopdf.fetch(cfg, say)


def _gather(db, cfg, rid, i, into, parts, places):
    """Put one recording's file (and its transcript, for audio or video) and what Lens found about it into the folder:
    ([its files, in order], transcript or None)."""
    rec = db.one("SELECT space, path, remote, source, title, media FROM $r", r=R("recording", rid))
    if not rec:
        raise KeyError(rid)
    kind, name = render.kind(rec), _name(i, rec.get("title"))
    d = render.player_data(db, rid)
    out, said = [], None
    if "file" in parts:
        out, said = _own(db, cfg, rec, rid, kind, name, into, evidence_report.marked(d) if "emotion" in parts else d)
    if evidence_report.has_findings(parts):
        found = into / f"{name} - what Lens found.txt"
        found.write_text(evidence_report.write(db, cfg, rid, parts, d, places), encoding="utf-8")
        out.append(found)
    return out, said


def _own(db, cfg, rec, rid, kind, name, into, d):
    """The recording's own file copied into the folder, or its text when it has none: ([that file], its transcript or
    None)."""
    src = None
    if kind != "transcript" and (rec.get("path") or rec.get("remote")):
        try:
            src = ingest.audio_path(db, cfg, rec)
        except (FileNotFoundError, ValueError):
            src = None
    if not src or not pathlib.Path(src).is_file():
        if kind in ("document", "image"):
            raise FileNotFoundError(f"the file of “{rec.get('title') or rid}” is missing on the server")
        src = None
    if src is None:  # its text is all there is
        out = into / f"{name}.txt"
        out.write_text(render.export_text(d, "txt") or "(no text)\n", encoding="utf-8")
        return [out], None
    out = into / f"{name}{pathlib.PurePosixPath(str((rec.get('remote') or {}).get('path') or src)).suffix.lower()}"
    shutil.copyfile(src, out)
    said = None
    if kind in ("audio", "video") and d["segments"]:
        said = into / "transcripts" / f"{name}.srt"
        said.parent.mkdir(exist_ok=True)
        said.write_text(render.export_text(d, "srt"), encoding="utf-8")
    return [out], said


def make(db, cfg, rids, include=None, places=None):
    """The evidence PDF of these recordings, in this order, as bytes. `include`: its parts (evidence_report.PARTS;
    None: everything but people's notes). `places`: the recordings whose places may go in (None: all of them)."""
    rids = list(dict.fromkeys(int(r) for r in rids))
    if not rids:
        raise ValueError("there's nothing to put in it")
    if len(rids) > MAX:
        raise ValueError(f"an evidence PDF holds {MAX} assets at most; this has {len(rids)}")
    parts = evidence_report.parts_of(include)
    if not parts:
        raise ValueError("there's nothing to put in it: include its file or something Lens found")
    ready(cfg)
    work = pathlib.Path(cfg["data_dir"]) / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    with keyring.work(cfg), tempfile.TemporaryDirectory(dir=work, prefix="evidence-") as tmp:
        into = pathlib.Path(tmp) / "in"
        into.mkdir(mode=0o700)
        inputs, transcripts = [], []
        for i, rid in enumerate(rids, 1):
            files, t = _gather(db, cfg, rid, i, into, parts, places is None or rid in places)
            inputs += files
            transcripts += [t] if t else []
        out = pathlib.Path(tmp) / "evidence.pdf"
        anytopdf.evidence(cfg, inputs, transcripts, out)
        return out.read_bytes()


def file_name(title):
    return f"{render.slug(title)}-evidence.pdf"
