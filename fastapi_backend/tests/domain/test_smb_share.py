"""The SMB share (smb_share.py), end to end with impacket's own SMB client: a key's view, read-only, refusals."""

from __future__ import annotations

import io
import posixpath

import pytest

pytest.importorskip("impacket")

from impacket.smbconnection import SessionError, SMBConnection  # noqa: E402

from app.domain import smb_share  # noqa: E402

BIG = bytes(range(256)) * 4000


class FakeTree:
    """Two people's views: ada sees the 'pods' node, bob sees nothing."""

    FILES = {"ada": {"Pods/Episode 1/audio.txt": b"hello lens\n", "Pods/big.bin": BIG}, "bob": {}}

    def _dirs(self, account):
        d = {""}
        for p in self.FILES[account]:
            parts = p.split("/")
            d.update("/".join(parts[:i]) for i in range(1, len(parts)))
        return d

    def entry(self, account, path):
        files = self.FILES[account]
        if path in files:
            return smb_share.Entry(posixpath.basename(path), False, len(files[path]), 1.7e9, path)
        if path in self._dirs(account):
            return smb_share.Entry(posixpath.basename(path), True, 0, 1.7e9)
        return None

    def children(self, account, path):
        pre = f"{path}/" if path else ""
        names = {p[len(pre) :].split("/")[0] for p in [*self.FILES[account], *self._dirs(account)] if p.startswith(pre) and p[len(pre) :]}
        return [self.entry(account, pre + n) for n in sorted(names)]

    def open(self, account, e):
        data = self.FILES[account][e.source]
        return smb_share.Reader(lambda start, end: (data[i : min(i + 1000, end + 1)] for i in range(start, end + 1, 1000)), len(data))


KEYS = {"lk_ada": {"account": "ada", "secret": "ada-secret-1"}, "lk_bob": {"account": "bob", "secret": "bob-secret-2"}}


@pytest.fixture
def server():
    keys = smb_share.Keys(lambda k: KEYS.get(k))
    s = smb_share.Server("127.0.0.1", 0, FakeTree(), keys, ["127.0.0.0/8"]).start()
    yield s, keys
    s.stop()


def connect(port, user, password):
    c = SMBConnection("127.0.0.1", "127.0.0.1", sess_port=port)
    c.login(user, password)
    return c


def test_reads_the_tree_as_its_key_sees_it(server):
    s, _ = server
    c = connect(s.port, "lk_ada", "ada-secret-1")
    names = sorted(f.get_longname() for f in c.listPath(smb_share.SHARE, "*") if f.get_longname() not in (".", ".."))
    assert names == ["Pods"]
    inner = {f.get_longname(): f for f in c.listPath(smb_share.SHARE, "Pods\\*")}
    assert inner["Episode 1"].is_directory() and inner["big.bin"].get_filesize() == len(BIG)
    out = io.BytesIO()
    c.getFile(smb_share.SHARE, "Pods\\big.bin", out.write)
    assert out.getvalue() == BIG
    out = io.BytesIO()
    c.getFile(smb_share.SHARE, "Pods\\Episode 1\\audio.txt", out.write)
    assert out.getvalue() == b"hello lens\n"
    # bob signs in fine and sees an empty share, not ada's
    b = connect(s.port, "lk_bob", "bob-secret-2")
    assert [f.get_longname() for f in b.listPath(smb_share.SHARE, "*") if f.get_longname() not in (".", "..")] == []
    with pytest.raises(SessionError):
        b.getFile(smb_share.SHARE, "Pods\\big.bin", io.BytesIO().write)


def test_nothing_can_be_written(server):
    s, _ = server
    c = connect(s.port, "lk_ada", "ada-secret-1")
    with pytest.raises(SessionError):
        c.putFile(smb_share.SHARE, "Pods\\new.txt", io.BytesIO(b"x").read)
    with pytest.raises(SessionError):
        c.createDirectory(smb_share.SHARE, "Pods\\new")
    with pytest.raises(SessionError):
        c.deleteFile(smb_share.SHARE, "Pods\\big.bin")
    with pytest.raises(SessionError):
        c.rename(smb_share.SHARE, "Pods\\big.bin", "Pods\\moved.bin")
    assert len(FakeTree.FILES["ada"]) == 2


def test_wrong_password_guest_and_revoked_keys_are_refused(server):
    s, keys = server
    with pytest.raises(SessionError):
        connect(s.port, "lk_ada", "wrong")
    with pytest.raises(SessionError):
        connect(s.port, "nobody", "ada-secret-1")
    guest = SMBConnection("127.0.0.1", "127.0.0.1", sess_port=s.port)
    try:
        guest.login("", "")
        with pytest.raises(SessionError):
            guest.listPath(smb_share.SHARE, "*")
    except SessionError:
        pass  # refused at sign-in: also fine
    # a revoked key stops working once the cached lookup ages out
    c = connect(s.port, "lk_ada", "ada-secret-1")
    gone = KEYS.pop("lk_ada")
    try:
        keys.cache.clear()
        with pytest.raises(SessionError):
            c.listPath(smb_share.SHARE, "*")
    finally:
        KEYS["lk_ada"] = gone


def test_outside_the_allowed_networks_is_refused():
    keys = smb_share.Keys(lambda k: KEYS.get(k))
    s = smb_share.Server("127.0.0.1", 0, FakeTree(), keys, ["10.0.0.0/8"]).start()
    try:
        with pytest.raises(Exception):  # noqa: B017 - the connection is closed before anything is said
            connect(s.port, "lk_ada", "ada-secret-1")
    finally:
        s.stop()


def test_virtual_os_never_reaches_the_disk():
    vos = smb_share.VirtualOS(FakeTree(), smb_share.Keys(lambda k: KEYS.get(k)))
    for p in ("/etc/passwd", "/lens-smb/../etc/passwd", "/lens-smb-other/x"):
        with pytest.raises(PermissionError):
            vos.stat(p)
        assert not vos.path.exists(p)
    with pytest.raises(PermissionError):
        vos.open("/lens-smb/lk_ada/Pods/big.bin", smb_share._os.O_RDWR)
    with pytest.raises(PermissionError):
        vos.stat("/lens-smb/lk_nobody/Pods")


def test_allowed():
    nets = ["192.168.0.0/16", "::1/128"]
    assert smb_share.allowed(nets, "192.168.1.5") and smb_share.allowed(nets, "::ffff:192.168.1.5") and smb_share.allowed(nets, "::1")
    assert not smb_share.allowed(nets, "8.8.8.8") and not smb_share.allowed([], "127.0.0.1") and not smb_share.allowed(nets, "junk")


def test_reader_streams_on_and_restarts_on_a_jump():
    calls = []

    def chunks(start, end):
        calls.append(start)
        return (BIG[i : min(i + 777, end + 1)] for i in range(start, end + 1, 777))

    r = smb_share.Reader(chunks, len(BIG))
    got = b"".join(r.read(o, 4096) for o in range(0, len(BIG), 4096))
    assert got == BIG and calls == [0]
    assert r.read(10, 5) == BIG[10:15] and calls == [0, 10]
    assert r.read(len(BIG), 5) == b""
    r.close()


def test_nt_hash_matches_the_known_vector():
    assert smb_share.nt_hash("password").hex() == "8846f7eaee8fb117ad06bdd830b7586c"
