"""Zip and tar archives (archives.py): read with care. Uploading one: tests/api/test_anytopdf.py."""

from __future__ import annotations

import io
import tarfile
import zipfile

import pytest

from app.domain import archives


def zip_of(files, encrypted=()):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    raw = bytearray(buf.getvalue())
    for name in encrypted:  # mark an entry encrypted, as a password-protected zip does
        with zipfile.ZipFile(io.BytesIO(bytes(raw))) as z:
            info = z.getinfo(name)
        raw[info.header_offset + 6] |= 1
        cd = raw.find(b"PK\x01\x02")
        while cd != -1:
            n = int.from_bytes(raw[cd + 28 : cd + 30], "little")
            if raw[cd + 46 : cd + 46 + n].decode() == name:
                raw[cd + 8] |= 1
            cd = raw.find(b"PK\x01\x02", cd + 4)
    return bytes(raw)


def test_reading_an_archive(tmp_path):
    z = tmp_path / "a.zip"
    z.write_bytes(zip_of({"notes/plan.txt": "Plan", "__MACOSX/._plan.txt": "junk", "empty/": "", "secret.txt": "x"}, ["secret.txt"]))
    inside, left = archives.read(z)
    assert [(f["name"], f["path"], f["data"]) for f in inside] == [("plan.txt", "notes/plan.txt", b"Plan")]
    assert left == [("secret.txt", "encrypted")]

    t = tmp_path / "b.tgz"
    with tarfile.open(t, "w:gz") as tf:
        for name, data in {"../../etc/evil.txt": b"ha", "ok.md": b"# Hi"}.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
        link = tarfile.TarInfo("link")
        link.type, link.linkname = tarfile.SYMTYPE, "/etc/passwd"
        tf.addfile(link)
    inside, left = archives.read(t)
    assert [(f["name"], f["path"]) for f in inside] == [("evil.txt", "../../etc/evil.txt"), ("ok.md", "ok.md")]  # a name only
    assert left == [("link", "not a plain file")]

    bad = tmp_path / "c.zip"
    bad.write_bytes(b"PK not really")
    with pytest.raises(ValueError):
        archives.read(bad)


def test_a_bomb_stops_at_its_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(archives, "MAX_BYTES", 1000)
    monkeypatch.setattr(archives, "MAX_FILE", 600)
    z = tmp_path / "bomb.zip"
    z.write_bytes(zip_of({"a.txt": "0" * 500, "b.txt": "0" * 501, "huge.txt": "0" * 100_000}))
    inside, left = archives.read(z)
    assert [f["name"] for f in inside] == ["a.txt"]
    assert left == [("b.txt", "too large to unpack"), ("huge.txt", "too large to unpack")]
