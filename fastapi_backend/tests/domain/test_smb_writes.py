"""Adding files over SMB (docs/smb.md, docs/file-tree.md#adding-files), end to end with impacket's own SMB client and
the real file tree: a file copied in is imported where it's put, as an upload is; what may not be added is refused."""

from __future__ import annotations

import io
import time

import pytest

pytest.importorskip("impacket")

from impacket.smb3structs import FILE_DELETE_ON_CLOSE, FILE_NON_DIRECTORY_FILE, FILE_OVERWRITE_IF, GENERIC_ALL  # noqa: E402
from impacket.smbconnection import SessionError, SMBConnection  # noqa: E402

from app.domain import hierarchy, share_import, smb_share, store  # noqa: E402
from tests.helpers import make_user, seed, text_pdf  # noqa: E402

PDF = text_pdf([["Harbour minutes", "The lamp was lit at dusk."]])
S = smb_share.SHARE


@pytest.fixture
def env(db, cfg, folder):
    a, _, _ = seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    talks = hierarchy.create(db, pods, "Talks")
    users = {
        "lked": make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"}),
        "lkvi": make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"}),
    }
    keys = smb_share.Keys(lambda kid: {"account": users[kid], "secret": f"{kid}-secret"} if kid in users else None)
    conf = {"cfg": {**cfg, "smb": {**cfg.get("smb", {}), "add_files": True}}}
    share_import.REGISTRY.items.clear()
    s = smb_share.Server("127.0.0.1", 0, smb_share.FileTree(db, lambda: conf["cfg"]), keys, ["127.0.0.0/8"], add_files=True).start()
    yield {"port": s.port, "pods": pods, "talks": talks, "asset": a, "conf": conf, "server": s}
    s.stop()


def _connect(env, who="lked"):
    c = SMBConnection("127.0.0.1", "127.0.0.1", sess_port=env["port"])
    c.login(who, f"{who}-secret")
    return c


def _names(c, path):
    return sorted(f.get_longname() for f in c.listPath(S, path + "\\*") if f.get_longname() not in (".", ".."))


def _recording(db, title):
    return db.one("SELECT record::id(id) AS id, collection, source, size FROM recording WHERE title = $t", t=title)


def test_a_file_copied_into_a_collection_is_imported_there(db, env):
    c = _connect(env)
    c.putFile(S, "pods\\Collections\\Talks\\minutes.pdf", io.BytesIO(PDF).read)
    rec = _recording(db, "minutes")
    assert rec["collection"] == env["talks"] and rec["source"] == "document" and rec["size"] == len(PDF)
    assert db.one("SELECT VALUE id FROM job WHERE recording = $r LIMIT 1", r=rec["id"])  # the namespace's pipeline
    audit = db.one("SELECT * FROM audit_log WHERE action = 'share.import'")
    assert audit["detail"]["via"] == "smb" and audit["detail"]["path"] == "pods/Collections/Talks/minutes.pdf"
    # the client finds it under the name it gave, beside the asset's own files, and reads it back
    names = _names(c, "pods\\Collections\\Talks")
    assert "minutes.pdf" in names and f"minutes [{rec['id']}].pdf" in names
    out = io.BytesIO()
    c.getFile(S, "pods\\Collections\\Talks\\minutes.pdf", out.write)
    assert out.getvalue() == PDF
    # only for them: others see the asset as Lens names it
    assert "minutes.pdf" not in _names(_connect(env, "lkvi"), "pods\\Collections\\Talks")
    # the same file again (Finder's "Replace") is the same asset
    c.putFile(S, "pods\\Collections\\Talks\\minutes.pdf", io.BytesIO(PDF).read)
    assert db.values("SELECT VALUE record::id(id) FROM recording WHERE title = 'minutes'") == [rec["id"]]
    # a file at the top of the namespace goes to its default collection
    c.putFile(S, "pods\\notes.pdf", io.BytesIO(PDF + b"\n%2").read)
    assert _recording(db, "notes")["collection"] == store.default_collection(db, env["pods"])


def test_new_folders_under_collections_are_new_collections(db, env):
    c = _connect(env)
    c.createDirectory(S, "pods\\Collections\\Harbour")
    c.createDirectory(S, "pods\\Collections\\Harbour\\2026")
    assert "Harbour" in _names(c, "pods\\Collections") and _names(c, "pods\\Collections\\Harbour") == ["2026"]
    c.putFile(S, "pods\\Collections\\Harbour\\2026\\minutes.pdf", io.BytesIO(PDF).read)
    rec = _recording(db, "minutes")
    made = db.one("SELECT record::id(id) AS id, parent FROM collection WHERE name = '2026'")
    harbour = db.one("SELECT VALUE record::id(id) FROM collection WHERE name = 'Harbour'")
    assert rec["collection"] == made["id"] and made["parent"] == harbour
    with pytest.raises(SessionError):  # it's there already
        c.createDirectory(S, "pods\\Collections\\Harbour")
    with pytest.raises(SessionError):  # folders go under Collections only
        c.createDirectory(S, "pods\\Topics\\New topic")
    assert db.values("SELECT VALUE action FROM audit_log WHERE action = 'collection.create'") == ["collection.create"] * 2


def test_an_apps_own_files_and_temporary_names_are_kept_aside(db, env):
    c = _connect(env)
    c.putFile(S, "pods\\Collections\\Talks\\.DS_Store", io.BytesIO(b"\0\0\0\1Bud1").read)
    c.putFile(S, "pods\\Collections\\Talks\\empty.pdf", io.BytesIO(b"").read)
    c.putFile(S, "pods\\Collections\\Talks\\draft.tmp", io.BytesIO(PDF).read)
    assert not db.values("SELECT VALUE id FROM recording WHERE source = 'document'")
    names = _names(c, "pods\\Collections\\Talks")
    assert {".DS_Store", "empty.pdf", "draft.tmp"} <= set(names)
    c.deleteFile(S, "pods\\Collections\\Talks\\.DS_Store")
    assert ".DS_Store" not in _names(c, "pods\\Collections\\Talks")
    # renamed to an ordinary name, it's imported (an app saving under a temporary name)
    c.rename(S, "pods\\Collections\\Talks\\draft.tmp", "pods\\Collections\\Talks\\draft.pdf")
    assert _recording(db, "draft")["collection"] == env["talks"]
    # a file deleted as it's closed is never imported
    tid = c.connectTree(S)
    fid = c.createFile(tid, "pods\\Collections\\Talks\\scratch.pdf", GENERIC_ALL, creationOption=FILE_NON_DIRECTORY_FILE | FILE_DELETE_ON_CLOSE,
                       creationDisposition=FILE_OVERWRITE_IF)  # fmt: skip
    c.writeFile(tid, fid, PDF)
    c.closeFile(tid, fid)
    assert not _recording(db, "scratch") and "scratch.pdf" not in _names(c, "pods\\Collections\\Talks")
    # nor is a file still open when its client goes away
    other = _connect(env)
    tid = other.connectTree(S)
    fid = other.createFile(tid, "pods\\Collections\\Talks\\half.pdf")
    other.writeFile(tid, fid, PDF[:100])
    other.getSMBServer().get_socket().close()
    for _ in range(50):  # the server notices in its own time
        held = [i.holds for names in share_import.REGISTRY.items.values() for n, i in names.items() if n == "half.pdf"]
        if held == [0]:
            break
        time.sleep(0.05)
    assert held == [0] and not _recording(db, "half")


def test_what_may_not_be_added_is_refused(db, env):
    c = _connect(env)
    with pytest.raises(SessionError):  # a viewer
        _connect(env, "lkvi").putFile(S, "pods\\Collections\\Talks\\minutes.pdf", io.BytesIO(PDF).read)
    with pytest.raises(SessionError):  # a type Lens doesn't take: refused as it's made
        c.putFile(S, "pods\\Collections\\Talks\\tool.exe", io.BytesIO(b"MZ").read)
    asset = f"pods\\Collections\\General\\ep1 [{env['asset']}].txt"
    with pytest.raises(SessionError):  # what Lens made can't be written over, deleted or renamed
        c.putFile(S, asset, io.BytesIO(b"x").read)
    with pytest.raises(SessionError):
        c.deleteFile(S, asset)
    with pytest.raises(SessionError):
        c.rename(S, asset, "pods\\Collections\\General\\mine.txt")
    with pytest.raises(SessionError):  # namespaces are made in the web app
        c.createDirectory(S, "new-namespace")
    env["conf"]["cfg"] = {**env["conf"]["cfg"], "uploads": {**env["conf"]["cfg"]["uploads"], "max_mb": 1}}
    with pytest.raises(SessionError):  # over uploads.max_mb: cut off as it's written
        c.putFile(S, "pods\\Collections\\Talks\\big.pdf", io.BytesIO(PDF + b"\0" * (2 << 20)).read)
    assert not db.values("SELECT VALUE id FROM recording WHERE source = 'document'")
    # off: read-only again, whatever the share says
    env["conf"]["cfg"] = {**env["conf"]["cfg"], "smb": {**env["conf"]["cfg"]["smb"], "add_files": False}}
    with pytest.raises(SessionError):
        c.putFile(S, "pods\\Collections\\Talks\\minutes.pdf", io.BytesIO(PDF).read)
    assert not _recording(db, "minutes")


def test_the_share_is_read_only_while_add_files_is_off(db, cfg):
    live = {**cfg, "smb": {**cfg.get("smb", {}), "enabled": True, "bind": "127.0.0.1", "port": 0, "add_files": False}}
    r = smb_share.Runner(db, lambda: live, "w1")
    r.apply()
    try:
        assert r.server is not None and smb_share.status(db, live)["add_files"] is False
        first = r.server
        live["smb"]["add_files"] = True  # turning it on restarts the server with a writable share
        r.apply()
        assert r.server is not first and smb_share.status(db, live)["add_files"] is True
        assert ",ro," not in smb_share.status(db, live)["mount"]
    finally:
        r.close()


def test_smb_add_files_is_checked():
    from app.domain import settings

    assert settings._smb_setting("add_files", False) is False
    with pytest.raises(ValueError):
        settings._smb_setting("add_files", "no")
