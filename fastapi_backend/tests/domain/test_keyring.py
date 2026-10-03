"""Namespace data keys, their wrappers, and the encrypted file format (app/domain/keyring.py)."""

from __future__ import annotations

import io
import os

import pytest

from app.domain import keyring, store


@pytest.fixture
def sid(db):
    return store.ns_id(db, "pods")


def test_each_namespace_gets_its_own_key_kept_only_wrapped(db, cfg, sid):
    v, key = keyring.data_key(db, cfg, sid)
    assert v == 1 and len(key) == 32
    other = keyring.data_key(db, cfg, store.ns_id(db, "calls"))[1]
    assert other != key
    row = db.one("SELECT * FROM $r", r=store.R("data_key", sid))
    assert key.hex() not in str(row) and row["keys"]["1"]["wrapped"]["server"]
    db._data_keys.clear()  # a fresh process opens it again with the server's secret
    assert keyring.data_key(db, cfg, sid)[1] == key
    assert keyring.status(db, sid) | {"unlocked": None} == {
        "space": sid,
        "keys": True,
        "vault": False,
        "current": 1,
        "versions": [1],
        "wrappers": ["server"],
        "unlocked": None,
    }


def test_sealed_values_open_only_in_their_own_context(db, cfg, sid):
    s = keyring.seal(db, cfg, sid, b"call notes", "note:7")
    assert keyring.is_sealed(s) and b"call notes" not in s.encode()
    assert keyring.unseal(db, cfg, s, "note:7") == b"call notes"
    with pytest.raises(keyring.Damaged):
        keyring.unseal(db, cfg, s, "note:8")


@pytest.mark.parametrize("size", [0, 1, 99, 100, 101, 1000, 4321])
def test_files_round_trip_and_seek(db, cfg, sid, folder, size):
    data = os.urandom(size)
    p = folder / "a.bin"
    p.write_bytes(data)
    assert keyring.encrypt_file(db, cfg, sid, p, chunk=100)
    assert keyring.is_encrypted(p) and (size < 16 or data[:16] not in p.read_bytes())
    assert not keyring.encrypt_file(db, cfg, sid, p)  # already encrypted
    with keyring.Reader(db, cfg, p) as r:
        assert r.size == size and r.space == sid
        assert r.read() == data
        for off in (0, 1, 99, 100, 250, size - 1):
            if 0 <= off < size:
                r.seek(off)
                assert r.read(150) == data[off : off + 150]
        r.seek(-3, io.SEEK_END)
        assert r.read() == data[-3:] if size >= 3 else True
    with keyring.plain_path(db, cfg, p) as plain:
        assert open(plain, "rb").read() == data
    assert not os.path.exists(plain)
    assert keyring.decrypt_file(db, cfg, p) and p.read_bytes() == data


def test_changed_cut_or_reordered_files_do_not_open(db, cfg, sid, folder):
    p = folder / "a.bin"
    p.write_bytes(os.urandom(350))
    keyring.encrypt_file(db, cfg, sid, p, chunk=100)
    good = p.read_bytes()
    step = 116
    head = keyring.HEADER.size
    cases = [
        good[:-1],  # cut short
        good[: head + 2 * step],  # whole chunks dropped from the end
        good[:-5] + bytes([good[-5] ^ 1]) + good[-4:],  # one bit flipped
        good[:head] + good[head + step : head + 2 * step] + good[head : head + step] + good[head + 2 * step :],  # swapped
    ]
    for bad in cases:
        p.write_bytes(bad)
        with pytest.raises(keyring.Damaged):
            with keyring.Reader(db, cfg, p) as r:
                r.read()


def test_a_file_encrypted_for_one_install_does_not_open_on_another(db, cfg, sid, folder, monkeypatch):
    p = folder / "a.bin"
    p.write_bytes(b"x" * 10)
    keyring.encrypt_file(db, cfg, sid, p)
    db._data_keys.clear()
    monkeypatch.setenv("ARCHIVE_SECRET_KEY", "a different secret")
    with pytest.raises(keyring.Damaged):
        keyring.Reader(db, cfg, p)


def test_rotation_keeps_older_files_readable(db, cfg, sid, folder):
    old = folder / "old.bin"
    old.write_bytes(b"before")
    keyring.encrypt_file(db, cfg, sid, old)
    assert keyring.rotate(db, cfg, sid) == 2
    new = folder / "new.bin"
    new.write_bytes(b"after")
    keyring.encrypt_file(db, cfg, sid, new)
    db._data_keys.clear()
    with keyring.Reader(db, cfg, old) as a, keyring.Reader(db, cfg, new) as b:
        assert (a.version, a.read(), b.version, b.read()) == (1, b"before", 2, b"after")


def test_a_vault_opens_only_with_its_own_key(db, cfg, sid, folder):
    p = folder / "a.bin"
    p.write_bytes(b"private")
    keyring.encrypt_file(db, cfg, sid, p)
    phone, laptop = keyring.derive(b"prf output 1", "lens/passkey"), keyring.derive(b"prf output 2", "lens/passkey")
    keyring.add_wrapper(db, cfg, sid, "passkey:phone", phone)
    keyring.add_wrapper(db, cfg, sid, "passkey:laptop", laptop)
    keyring.remove_wrapper(db, sid, "server")
    assert keyring.status(db, sid)["vault"]
    keyring.lock(db, sid)
    with pytest.raises(keyring.Locked):
        keyring.Reader(db, cfg, p)
    with pytest.raises(keyring.Locked):
        keyring.unlock(db, sid, "passkey:phone", laptop)
    keyring.unlock(db, sid, "passkey:laptop", laptop)
    assert keyring.Reader(db, cfg, p).read() == b"private"
    assert keyring.rotate(db, cfg, sid, keks={"passkey:phone": phone, "passkey:laptop": laptop}) == 2
    keyring.lock(db, sid)
    keyring.unlock(db, sid, "passkey:phone", phone)
    assert keyring.data_key(db, cfg, sid)[0] == 2
    keyring.remove_wrapper(db, sid, "passkey:laptop")
    with pytest.raises(ValueError):
        keyring.remove_wrapper(db, sid, "passkey:phone")  # the last key that opens it


def test_rotating_a_key_this_process_has_not_opened_yet(db, cfg, sid):
    keyring.data_key(db, cfg, sid)
    db._data_keys.clear()
    assert keyring.rotate(db, cfg, sid) == 2


def test_key_changes_made_elsewhere_meanwhile_are_not_overwritten(db, cfg, sid):
    keyring.data_key(db, cfg, sid)
    stale = db.one("SELECT * FROM $r", r=store.R("data_key", sid))
    keyring.rotate(db, cfg, sid)
    with pytest.raises(RuntimeError):
        keyring._save(db, sid, stale, stale["keys"])
    assert keyring.status(db, sid)["versions"] == [1, 2]


def test_a_write_that_fails_leaves_the_original_file(db, cfg, sid, folder):
    folder = folder / "w"
    folder.mkdir()
    p = folder / "a.bin"
    p.write_bytes(b"original")
    with pytest.raises(OSError), keyring.Writer(db, cfg, sid, p) as w:
        w.write(b"half")
        raise OSError("disk went away")
    assert p.read_bytes() == b"original" and [x.name for x in folder.iterdir()] == ["a.bin"]
    w = keyring.Writer(db, cfg, sid, p)
    w.write(b"dropped")
    del w
    assert p.read_bytes() == b"original" and [x.name for x in folder.iterdir()] == ["a.bin"]


def test_a_locked_file_leaves_no_temporary_copy(db, cfg, sid, folder):
    p = folder / "a.bin"
    p.write_bytes(b"private")
    keyring.encrypt_file(db, cfg, sid, p)
    keyring.add_wrapper(db, cfg, sid, "passkey:x", keyring.derive(b"x", "t"))
    keyring.remove_wrapper(db, sid, "server")
    keyring.lock(db, sid)
    with pytest.raises(keyring.Locked):
        keyring.decrypt_file(db, cfg, p)
    with pytest.raises(keyring.Locked), keyring.plain_path(db, cfg, p):
        pass
    assert [x.name for x in folder.iterdir() if x.name != "data"] == ["a.bin"]
    assert not list((folder / "data" / "tmp").iterdir())
