"""Zip and tar archives as resources: a page listing what's inside, and the files in it kept as the archive's files.

An archive is a document: its rendition is a contents page (each file's path and size, and what was left out), and
its files are kept as it keeps an email's attachments (convert.keep_attachments): each one a file of the archive, and
those Lens can read made resources of their own, which say which archive they came from (attached_to). A WhatsApp
"Export chat" with media is a zip like this: its chat becomes a chat export (chats.py) and its photos resources.

Archives are read with care, since anyone can make one: only plain files are taken (no links, devices or folders),
a file's path is never used to write anywhere, encrypted zip entries are left out, and reading stops at MAX_FILES
files or MAX_BYTES unpacked, whichever comes first, so an archive that unpacks to far more than it holds (a zip bomb)
can't fill the disk or the memory. An archive inside an archive is kept as a file, not opened.
"""

from __future__ import annotations

import html
import pathlib
import tarfile
import zipfile

EXT = (".zip", ".tar", ".tgz")
MAX_FILES = 100  # files read from an archive, at most (as many as a resource keeps: files.MAX_FILES)
MAX_BYTES = 512 * 1024 * 1024  # unpacked, in all
MAX_FILE = 200 * 1024 * 1024  # one file, unpacked
SKIP = ("__MACOSX/", ".DS_Store", "Thumbs.db")


def is_archive(name):
    return pathlib.PurePosixPath(str(name or "")).suffix.lower() in EXT


def _skipped(path):
    return any(path.startswith(s) or path.endswith(s) for s in SKIP) or path.rsplit("/", 1)[-1].startswith("._")


def _zip_members(path):
    with zipfile.ZipFile(path) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            if info.flag_bits & 0x1:
                yield info.filename, info.file_size, None, "encrypted"
                continue
            yield info.filename, info.file_size, lambda info=info: z.open(info), None


def _tar_members(path):
    with tarfile.open(path, "r:*") as t:
        for m in t:
            if m.isdir():
                continue
            if not m.isfile():
                yield m.name, 0, None, "not a plain file"
                continue
            yield m.name, m.size, lambda m=m: t.extractfile(m), None


def read(path):
    """The archive's files: ([{name, path, data}], [(path, why left out)]). ValueError when it isn't an archive Lens
    can read."""
    path = pathlib.Path(path)
    try:
        members = _zip_members(path) if zipfile.is_zipfile(path) else _tar_members(path)
        files, left, total = [], [], 0
        for name, size, opener, why in members:
            name = name.replace("\\", "/").lstrip("/")
            if _skipped(name):
                continue
            if why:
                left.append((name, why))
                continue
            if len(files) >= MAX_FILES:
                left.append((name, f"past the first {MAX_FILES} files"))
                continue
            if size > MAX_FILE or total + size > MAX_BYTES:
                left.append((name, "too large to unpack"))
                continue
            with opener() as f:
                data = f.read(min(MAX_FILE, MAX_BYTES - total) + 1)  # never more than it may hold, whatever it claims
            if len(data) > MAX_FILE or total + len(data) > MAX_BYTES:
                left.append((name, "too large to unpack"))
                continue
            total += len(data)
            files.append({"name": name.rsplit("/", 1)[-1] or "file", "path": name, "data": data})
    except (zipfile.BadZipFile, tarfile.TarError, EOFError, OSError, RuntimeError, NotImplementedError) as e:
        raise ValueError(f"this archive can't be read ({e})") from None
    return files, left


def _size(n):
    for unit in ("bytes", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "bytes" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n} bytes"


def page(title, files, left):
    """The archive's contents page: each file's path and size, then what was left out and why."""
    from . import convert

    rows = "".join(f"<li>{html.escape(f['path'])} ({_size(len(f['data']))})</li>" for f in files)
    body = f"<h1>{html.escape(title)}</h1><p>{len(files)} file(s)</p><ul>{rows}</ul>"
    if left:
        out = "".join(f"<li>{html.escape(p)}: {html.escape(why)}</li>" for p, why in left)
        body += f"<h2>Left out</h2><ul>{out}</ul>"
    return convert.page_html(title, body)
