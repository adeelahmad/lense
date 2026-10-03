"""A resource's files: the primary one, the audio or video its pipeline runs on, and any number of supplementary files
kept beside it (docs/api.md#files).

Each supplementary file has a role, and a language, a label and a description of its own. Transcripts, captions,
translations and indexes are also read into lines (file_line) that search finds, the resource's page shows and IIIF
publishes; the file itself stays as it was uploaded, to download. Files live in data_dir/files/<resource>/<file>/,
keyed by the resource, so moving it to another namespace changes only their rows; deleting it deletes them.

A public resource opens its files the way it opens its parts (docs/access.md): transcripts, captions and translations
with its transcript, indexes with its index, thumbnails with its media. Attachments always need permission.
"""

from __future__ import annotations

import json
import mimetypes
import pathlib
import re
import secrets
import shutil
import unicodedata
import xml.etree.ElementTree as ET

from . import access as acc, ingest, keyring, render, store
from .metadata import LANG_RX

R = store.R
ROLES = ("transcript", "captions", "translation", "index", "thumbnail", "attachment")
LABELS = {
    "transcript": "Transcript",
    "captions": "Captions",
    "translation": "Translation",
    "index": "Index",
    "thumbnail": "Thumbnail",
    "attachment": "Attachment",
}
PARSED = frozenset({"transcript", "captions", "translation", "index"})
# the part of a resource (access.PARTS) whose being open to everyone opens its files with this role; None: never
PART = {
    "transcript": "transcript",
    "captions": "transcript",
    "translation": "transcript",
    "index": "index",
    "thumbnail": "media",
    "attachment": None,
}
TEXT_TYPES = frozenset({".txt", ".text", ".md", ".markdown", ".mdx", ".json", ".jsonl", ".srt", ".vtt", ".docx", ".doc", ".pdf"})
IMAGE_TYPES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".gif"})
# the types of file each role takes (None: any); an index can also be OHMS XML
TYPES = {
    "transcript": TEXT_TYPES,
    "captions": frozenset({".vtt", ".srt"}),
    "translation": TEXT_TYPES,
    "index": TEXT_TYPES | {".xml"},
    "thumbnail": IMAGE_TYPES,
    "attachment": None,
}
MAX_FILES = 100  # on one resource
MAX_LINES = 20000  # read from one file
READ_MAX = 25 * 2**20  # the largest file whose lines are read
NAME_MAX, LABEL_MAX, DESCRIPTION_MAX, KEYWORDS_MAX = 200, 200, 2000, 50
# types a browser would run (pages, scripts, SVG): downloads of these are plain bytes, never served as themselves
ACTIVE = frozenset(
    {"text/html", "application/xhtml+xml", "image/svg+xml", "text/xml", "application/xml", "text/javascript", "application/javascript"}
)
# what a download says about itself: never run in a browser (nor framed), saved under its own name
HEADERS = {"X-Content-Type-Options": "nosniff", "Content-Security-Policy": "sandbox; default-src 'none'; frame-ancestors 'none'"}
OWN_TYPES = {
    ".vtt": "text/vtt",
    ".srt": "application/x-subrip",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".mdx": "text/markdown",
    ".jsonl": "application/jsonl",
    ".text": "text/plain",
}
FIELDS = (
    "record::id(id) AS id, recording, space, role, name, size, content_type, language, label, description, lines, timed, "
    "resource, created_at, created_by, updated_at"
)
LINE_FIELDS = "idx, t0, t1, text, speaker, title, synopsis, keywords"
CLOCK = re.compile(r"(?:\d{1,2}:)?\d{1,2}:\d{2}(?:[.,]\d{1,3})?")
# an index entry in text: a time, then its title ("00:05:10 Childhood", "[5:10] - Childhood")
TIME_LINE = re.compile(rf"^[\[(]?({CLOCK.pattern})[\])]?\s*(?:[-–—:|]\s*)?(.*)$")


# ---------- where files are kept ----------
def folder(cfg, rid):
    """A resource's supplementary files: one folder per file inside it."""
    return pathlib.Path(cfg["data_dir"]) / "files" / str(int(rid))


def path_of(cfg, f):
    return folder(cfg, f["recording"]) / str(f["id"]) / f["name"]


def incoming(cfg):
    """A new, empty place to write an upload to until it is checked and kept (on the same disk as the files)."""
    d = pathlib.Path(cfg["data_dir"]) / "files" / ".partial"
    d.mkdir(parents=True, exist_ok=True)
    return d / secrets.token_hex(12)


# ---------- what a file is ----------
def clean_name(name):
    """A file name to keep: its last part, without control characters or leading and trailing dots, at most NAME_MAX
    characters (keeping its extension). ValueError when nothing is left."""
    n = unicodedata.normalize("NFC", str(name or "")).replace("\\", "/").rsplit("/", 1)[-1]
    n = re.sub(r"[\x00-\x1f\x7f]", "", n).strip().strip(".").strip()
    if not n:
        raise ValueError("Name the file.")
    if len(n) > NAME_MAX:
        ext = pathlib.PurePosixPath(n).suffix
        ext = ext if len(ext) <= 16 else ""
        n = n[: NAME_MAX - len(ext)].rstrip(". ") + ext
    return n


def check(role, name):
    """ValueError unless a file with this name can have this role."""
    if role not in ROLES:
        raise ValueError(f"A file's role is one of {', '.join(ROLES)}.")
    ext = pathlib.PurePosixPath(name).suffix.lower()
    allowed = TYPES[role]
    if allowed is not None and ext not in allowed:
        raise ValueError(f"{LABELS[role]} files are {' '.join(sorted(allowed))}; this is {ext or 'a file without an extension'}.")


def content_type(name):
    ext = pathlib.PurePosixPath(name).suffix.lower()
    return OWN_TYPES.get(ext) or mimetypes.guess_type(name)[0] or "application/octet-stream"


def served_type(f):
    """What a download says it is: its type, unless a browser would run it (then just bytes)."""
    t = f.get("content_type") or "application/octet-stream"
    return "application/octet-stream" if t.split(";")[0].strip().lower() in ACTIVE else t


def _language(v):
    if v in (None, ""):
        return None
    v = str(v).strip()
    if not LANG_RX.match(v) or v == "none":
        raise ValueError("Use a language code such as en or pt-BR.")
    return v


def _short(v, most, what):
    v = " ".join(str(v or "").split())
    if len(v) > most:
        raise ValueError(f"A file's {what} can have up to {most} characters.")
    return v or None


def _description(v):
    v = str(v or "").strip()
    if len(v) > DESCRIPTION_MAX:
        raise ValueError(f"A file's description can have up to {DESCRIPTION_MAX} characters.")
    return v or None


def open_to(a, seen, role):
    """Whether someone who sees the resource as `seen` (access.view()) may download its files with this role."""
    part = PART[role]
    return seen == "full" or (part is not None and acc.usable(a, seen, part))


# ---------- reading lines ----------
def _clock(s):
    parts = s.replace(",", ".").split(":")
    return int(round(sum(float(p) * 60**i for i, p in enumerate(reversed(parts))) * 1000))


def _seconds(v):
    """A time given in seconds (a number, or a string of one) or as a clock (01:02:03.5), in ms; else None."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return int(round(float(v) * 1000)) if v >= 0 else None
    v = str(v or "").strip()
    if re.fullmatch(r"\d+(\.\d+)?", v):
        return int(round(float(v) * 1000))
    return _clock(v) if CLOCK.fullmatch(v) else None


def _text_of(path):
    """A file's text, documents (.docx, .doc, .pdf) included."""
    ext = path.suffix.lower()
    if ext in ingest.DOC_READERS:
        return ingest.DOC_READERS[ext](path)
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _index_cues(raw):
    """WebVTT or SRT chapters: each cue's text is an entry's title."""
    out = []
    for block in re.split(r"\n\s*\n", raw.replace("\r", "")):
        lines = [l.strip() for l in block.strip().split("\n") if l.strip()]
        for i, l in enumerate(lines):
            m = ingest.VTT_TIME.search(l)
            if m:
                title = re.sub(r"<[^>]+>", "", " ".join(lines[i + 1 :])).strip()
                if title:
                    out.append({"t0": _clock(m.group(1)), "t1": _clock(m.group(2)), "title": title})
                break
    return out


def _keywords(v):
    items = v if isinstance(v, list) else re.split(r"[;\n]", str(v or ""))
    return [k for k in (" ".join(str(x).split()) for x in items) if k][:KEYWORDS_MAX]


def _index_json(raw):
    """A list of entries (or {"index"|"chapters"|"segments"|"items"|"points": [...]}), each with a start (start_ms or
    t0 in ms; start or time in seconds or as a clock), a title, and maybe an end, a synopsis and keywords."""
    j = json.loads(raw)
    if isinstance(j, dict):
        j = next((j[k] for k in ("index", "chapters", "segments", "items", "points") if isinstance(j.get(k), list)), None)
    if not isinstance(j, list):
        raise ValueError("an index in JSON is a list of entries, each with a start time and a title")
    out = []
    for e in j:
        if not isinstance(e, dict):
            continue
        ms = lambda *keys: next((int(e[k]) for k in keys if isinstance(e.get(k), (int, float)) and not isinstance(e[k], bool)), None)  # noqa: E731
        sec = lambda *keys: next((t for t in (_seconds(e.get(k)) for k in keys if e.get(k) is not None) if t is not None), None)  # noqa: E731
        t0 = ms("start_ms", "t0")
        t0 = sec("start", "time", "start_time", "startTime") if t0 is None else t0
        t1 = ms("end_ms", "t1")
        t1 = sec("end", "end_time", "endTime") if t1 is None else t1
        title = " ".join(str(e.get("title") or e.get("label") or e.get("name") or e.get("heading") or "").split())
        synopsis = str(e.get("synopsis") or e.get("description") or e.get("summary") or e.get("text") or "").strip()
        if title or synopsis:
            out.append(
                {"t0": t0, "t1": t1, "title": title, "synopsis": synopsis, "keywords": _keywords(e.get("keywords") or e.get("tags"))}
            )
    return out


def _local(tag):
    return tag.rsplit("}", 1)[-1].lower() if isinstance(tag, str) else ""


def _index_ohms(raw):
    """An OHMS index: <point> elements with <time> (seconds), <title>, <synopsis> (or <partial_transcript>),
    <keywords> and <subjects> (separated by ;). ElementTree fetches no external entities."""
    out = []
    for p in ET.fromstring(raw).iter():
        if _local(p.tag) != "point":
            continue
        child = {_local(c.tag): (c.text or "").strip() for c in p}
        title, synopsis = " ".join(child.get("title", "").split()), child.get("synopsis") or child.get("partial_transcript") or ""
        if title or synopsis:
            kws = _keywords(child.get("keywords", "")) + _keywords(child.get("subjects", ""))
            out.append(
                {"t0": _seconds(child.get("time")), "t1": None, "title": title, "synopsis": synopsis, "keywords": kws[:KEYWORDS_MAX]}
            )
    if not out:
        raise ValueError("it has no index points (OHMS <point> elements)")
    return out


def _index_text(raw):
    """Entries that each start on a line with a time ("00:05:10 Childhood in Lahore"); the lines after one are its
    synopsis. Without any times, each line is an entry."""
    out, cur = [], None
    for line in raw.replace("\r", "").split("\n"):
        line = line.strip()
        if not line:
            continue
        m = TIME_LINE.match(line)
        if m:
            cur = {"t0": _clock(m.group(1)), "title": m.group(2).strip(), "synopsis": ""}
            out.append(cur)
        elif cur is not None and not cur["title"]:
            cur["title"] = line
        elif cur is not None and cur["t0"] is not None:
            cur["synopsis"] = (cur["synopsis"] + " " + line).strip()
        else:
            out.append({"t0": None, "title": line, "synopsis": ""})
    return [e for e in out if e["title"] or e["synopsis"]]


def _index(path):
    ext = path.suffix.lower()
    raw = _text_of(path)
    fmt = {".vtt": "vtt", ".srt": "vtt", ".json": "json", ".xml": "xml"}.get(ext)
    if fmt is None and ext not in ingest.DOC_READERS:
        head = raw.lstrip()[:200]
        fmt = "json" if head[:1] in "[{" else "xml" if head.startswith("<") else "vtt" if head.startswith("WEBVTT") else None
    if fmt == "vtt":
        return _index_cues(raw)
    if fmt == "json":
        return _index_json(raw)
    if fmt == "xml":
        return _index_ohms(raw)
    return _index_text(ingest.strip_markdown(raw) if ext in (".md", ".markdown", ".mdx") else raw)


def _ends(entries, duration_ms=None):
    """An index entry without an end runs to the next entry's start (the last to the end of the recording)."""
    starts = [e["t0"] for e in entries]
    for i, e in enumerate(entries):
        if e["t0"] is None or (e.get("t1") is not None and e["t1"] > e["t0"]):
            continue
        nxt = next((t for t in starts[i + 1 :] if t is not None and t > e["t0"]), None)
        e["t1"] = nxt if nxt is not None else (duration_ms if duration_ms and duration_ms > e["t0"] else None)
    return entries


def read(path, role, duration_ms=None):
    """The lines of a transcript, captions, translation or index, and whether they say when they are. Lines are
    {t0, t1, text, speaker} (index entries {t0, t1, title, synopsis, keywords} with all three in text, which search
    reads); times are None in a file that doesn't give them. ValueError when it can't be read or has no text."""
    path = pathlib.Path(path)
    if path.stat().st_size > READ_MAX:
        raise ValueError(f"Lens reads {LABELS[role].lower()} files up to {READ_MAX // 2**20} MB; keep it as an attachment.")
    try:
        if role == "index":
            entries = _ends(_index(path), duration_ms)
            timed = any(e["t0"] is not None for e in entries)
            lines = [
                {
                    "t0": e["t0"],
                    "t1": e.get("t1") if e["t0"] is not None else None,
                    "title": e["title"] or None,
                    "synopsis": e["synopsis"] or None,
                    "keywords": e.get("keywords") or None,
                    "text": "\n".join(x for x in (e["title"], e["synopsis"], "; ".join(e.get("keywords") or [])) if x),
                }
                for e in entries
            ]
        else:
            t = ingest.read_transcript(path)
            timed = bool(t.get("timed"))
            lines = [
                {"t0": s["t0"] if timed else None, "t1": s["t1"] if timed else None, "text": s["text"], "speaker": s.get("speaker")}
                for s in t["segments"]
            ]
    except SystemExit as e:  # the readers' "needs pdftotext", "no text layer"
        raise ValueError(str(e).replace(str(path), path.name)) from None
    except ValueError as e:
        raise ValueError(f"Lens couldn't read {path.name} as {AS[role]}: {e}") from None
    except Exception:  # noqa: BLE001 - a file anyone uploads can fail to read in many ways
        raise ValueError(f"Lens couldn't read {path.name} as {AS[role]}.") from None
    if not lines:
        raise ValueError(f"{path.name} has no text Lens can read as {AS[role]}.")
    if len(lines) > MAX_LINES:
        raise ValueError(f"{path.name} has more than {MAX_LINES:,} lines; keep it as an attachment.")
    return lines, timed


# what a file is read as, in messages
AS = {"transcript": "a transcript", "captions": "captions", "translation": "a translation", "index": "an index"}


# ---------- the files of a resource ----------
def get(db, rid, fid):
    """One of the resource's files, or KeyError."""
    f = db.one(f"SELECT {FIELDS} FROM $r", r=R("resource_file", int(fid)))
    if not f or f.get("recording") != int(rid):
        raise KeyError(fid)
    return f


def of(db, rid):
    """A resource's supplementary files: by role (in ROLES order), then the earliest added first."""
    rows = db.rows(f"SELECT {FIELDS} FROM resource_file WHERE recording = $r", r=int(rid))
    return sorted(rows, key=lambda f: (ROLES.index(f["role"]) if f.get("role") in ROLES else len(ROLES), f["id"]))


def _line_rows(f, lines):
    return [
        store.clean({"file": f["id"], "recording": f["recording"], "space": f["space"], "idx": i, **line}) for i, line in enumerate(lines)
    ]


def _write_lines(db, fid, rows):
    db.q("DELETE file_line WHERE file = $f", f=int(fid))
    for k in range(0, len(rows), 2000):
        db.q("INSERT INTO file_line $rows", rows=rows[k : k + 2000])


def add(db, cfg, rid, src, name, role, language=None, label=None, description=None, by=None):
    """Keep the file at `src` (moved into place) as one of the resource's supplementary files, reading its lines when
    its role has them. Returns the new file. ValueError for a role or type that doesn't fit, contents that can't be read
    or too many files; KeyError for no such resource. `src` is gone afterwards either way."""
    src, dest, fid = pathlib.Path(src), None, None
    try:
        rec = db.one("SELECT space, duration_ms FROM $r", r=R("recording", int(rid)))
        if not rec:
            raise KeyError(rid)
        name = clean_name(name)
        check(role, name)
        row = {
            "recording": int(rid),
            "space": rec["space"],
            "role": role,
            "name": name,
            "size": src.stat().st_size,
            "content_type": content_type(name),
            "language": _language(language),
            "label": _short(label, LABEL_MAX, "label"),
            "description": _description(description),
        }
        if len(db.values("SELECT VALUE id FROM resource_file WHERE recording = $r", r=int(rid))) >= MAX_FILES:
            raise ValueError(f"A resource can have up to {MAX_FILES} files; delete one first.")
        fid = db.next_id("resource_file")
        dest = folder(cfg, rid) / str(fid) / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dest))  # under its own name: readers go by the extension
        lines, timed = read(dest, role, rec.get("duration_ms")) if role in PARSED else ([], None)
        keyring.protect(db, cfg, rec["space"], dest)
        t = store.now()
        row.update(lines=len(lines) if role in PARSED else None, timed=timed, created_at=t, created_by=by, updated_at=t)
        db.q("CREATE $r CONTENT $d", r=R("resource_file", fid), d=store.clean(row))
        _write_lines(db, fid, _line_rows({"id": fid, **row}, lines))
    except BaseException:
        if dest is not None:
            db.run(["DELETE file_line WHERE file = $f", "DELETE $r"], f=fid, r=R("resource_file", fid))
            shutil.rmtree(dest.parent, ignore_errors=True)
        raise
    finally:
        src.unlink(missing_ok=True)
    return get(db, rid, fid)


def update(db, cfg, rid, fid, changes):
    """Change a file's role, language, label or description (the keys in `changes`; None clears one). A new role reads
    the file's lines again, or drops them. Returns the file, and what changed as {field: [before, after]}."""
    f = get(db, rid, fid)
    after = {}
    if "role" in changes and changes["role"] != f["role"]:
        role = changes["role"]
        check(role, f["name"])
        if role in PARSED:
            duration = (db.one("SELECT duration_ms FROM $r", r=R("recording", int(rid))) or {}).get("duration_ms")
            with keyring.plain_path(db, cfg, path_of(cfg, f)) as plain:
                lines, timed = read(plain, role, duration)
        else:
            lines, timed = [], None
        _write_lines(db, fid, _line_rows(f, lines))
        after.update(role=role, lines=len(lines) if role in PARSED else None, timed=timed)
    if "language" in changes:
        after["language"] = _language(changes["language"])
    if "label" in changes:
        after["label"] = _short(changes["label"], LABEL_MAX, "label")
    if "description" in changes:
        after["description"] = _description(changes["description"])
    changed = {k: [f.get(k), v] for k, v in after.items() if f.get(k) != v and k not in ("lines", "timed")}
    if after:
        sets = [f"{k} = NONE" if v is None else f"{k} = ${k}" for k, v in after.items()]
        db.q(
            f"UPDATE $r SET {', '.join(sets)}, updated_at = $t",
            r=R("resource_file", int(fid)),
            t=store.now(),
            **{k: v for k, v in after.items() if v is not None},
        )
    return get(db, rid, fid), changed


def delete(db, cfg, rid, fid):
    """Delete one of the resource's files and its lines. Returns what it was."""
    f = get(db, rid, fid)
    db.run(["DELETE file_line WHERE file = $f", "DELETE $r"], f=int(fid), r=R("resource_file", int(fid)))
    shutil.rmtree(folder(cfg, rid) / str(int(fid)), ignore_errors=True)
    return f


def lines_of(db, fid, offset=0, limit=None):
    """A file's lines in order."""
    page = f" LIMIT {int(limit)} START {int(offset)}" if limit is not None else ""
    return db.rows(f"SELECT {LINE_FIELDS} FROM file_line WHERE file = $f ORDER BY idx{page}", f=int(fid))


def as_vtt(db, f):
    """A timed transcript, captions, translation or index as WebVTT (an index's titles are its cues)."""
    rows = [r for r in lines_of(db, f["id"]) if r.get("t0") is not None and r.get("t1") is not None]
    names = sorted({r["speaker"] for r in rows if r.get("speaker")})
    d = {
        "title": f.get("label") or f["name"],
        "speakers": [{"key": i, "name": n} for i, n in enumerate(names)],
        "segments": [
            {
                "t0": r["t0"],
                "t1": r["t1"],
                "s": names.index(r["speaker"]) if r.get("speaker") else None,
                "text": (r.get("title") or r["text"]) if f["role"] == "index" else r["text"],
            }
            for r in rows
        ],
    }
    return render.export_text(d, "vtt")


def thumbnail(db, rid):
    """The resource's first thumbnail file, if it has one."""
    return next((f for f in of(db, rid) if f["role"] == "thumbnail"), None)
