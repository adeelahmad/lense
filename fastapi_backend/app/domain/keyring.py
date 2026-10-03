"""Encryption at rest: a data key per namespace, wrapped by keys that can open it, and an encrypted file format.

Every namespace gets its own random 256-bit data key the first time something of it is encrypted. The key is never
stored as it is: it is kept wrapped (AES-256-GCM) by one or more key-encryption keys, in data_key:⟨space⟩.
  - "server" is derived with HKDF-SHA-256 from the server's secret (ARCHIVE_SECRET_KEY, or data_dir/secret.key), so
    background work (transcription, embeddings, routines, the assistant) can read the namespace without anyone signed in.
  - Further wrappers are added by whoever holds another key, e.g. "passkey:<credential id>" with a key derived from a
    passkey's WebAuthn PRF output. A namespace whose "server" wrapper is removed is a vault: it opens only when
    someone unlocks it with one of its other wrappers, and stays open in this process until locked again.
Rotation adds a new key version; what was written with older versions stays readable, since everything records its
version. Removing a namespace's data_key row destroys everything encrypted with it.

Files are written in chunks (the STREAM construction), so a range of a large video can be read without the rest:

    header  = MAGIC (6) | space id (8) | key version (4) | chunk size (4) | nonce prefix (7)
    chunk i = AES-256-GCM(key, nonce = prefix | i (4) | last (1), chunk, aad = header)

The last chunk is flagged in its nonce, so a file cut short, or with chunks swapped, fails to open instead of reading
as something else. An empty file is one empty last chunk.
"""

from __future__ import annotations

import base64
import contextlib
import io
import os
import pathlib
import secrets
import struct
import tempfile
import threading

from . import settings, store

R = store.R
MAGIC = b"LENSE1"
HEADER = struct.Struct(">6sQII7s")
CHUNK = 64 * 1024
TAG = 16
SERVER = "server"
_LOCK = threading.RLock()  # data_key() runs inside rotate() and the wrapper changes, which hold it too


class Locked(PermissionError):
    """A vault namespace nobody has unlocked in this process."""


class Damaged(ValueError):
    """An encrypted file that doesn't open: cut short, changed, or written with a key that's gone."""


def _aes(key):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    return AESGCM(key)


def derive(material, purpose, salt=b"lens-keyring"):
    """A 256-bit key for one purpose from key material (HKDF-SHA-256)."""
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.hkdf import HKDF

    return HKDF(algorithm=hashes.SHA256(), length=32, salt=salt, info=purpose.encode()).derive(material)


def server_kek(cfg):
    return derive(settings.secret_key(cfg), "lens/kek/v1")


def _ad(sid, version, wrapper):
    return f"data_key:{sid}:{version}:{wrapper}".encode()


def wrap(kek, key, sid, version, wrapper):
    nonce = secrets.token_bytes(12)
    return base64.b64encode(nonce + _aes(kek).encrypt(nonce, key, _ad(sid, version, wrapper))).decode()


def unwrap(kek, wrapped, sid, version, wrapper):
    raw = base64.b64decode(wrapped)
    return _aes(kek).decrypt(raw[:12], raw[12:], _ad(sid, version, wrapper))


def _cache(db):
    c = getattr(db, "_data_keys", None)
    if c is None:
        c = db._data_keys = {}
    return c


def _row(db, sid):
    return db.one("SELECT * FROM $r", r=R("data_key", int(sid)))


def _create(db, cfg, sid):
    key = secrets.token_bytes(32)
    row = {
        "space": int(sid),
        "current": 1,
        "keys": {"1": {"created_at": store.now(), "wrapped": {SERVER: wrap(server_kek(cfg), key, sid, 1, SERVER)}}},
    }
    try:
        db.q("CREATE $r CONTENT $d", r=R("data_key", int(sid)), d=row)
    except Exception:  # noqa: BLE001 - another process made it first; use theirs
        pass
    return _row(db, sid)


def _save(db, sid, row, keys, current=None):
    """Write the keys back only if nobody changed them since row was read (another process rotating, say)."""
    cur = row["current"] if current is None else current
    done = db.values(
        "UPDATE $r SET keys = $k, current = $n, rev = $rev + 1 WHERE (rev ?? 0) = $rev RETURN VALUE rev",
        r=R("data_key", int(sid)),
        k=keys,
        n=cur,
        rev=int(row.get("rev") or 0),
    )
    if not done:
        raise RuntimeError("the namespace's keys changed at the same time; try again")


def status(db, sid):
    """What a namespace's keys look like, without any key: versions, wrappers, and whether it's a vault."""
    row = _row(db, sid)
    if not row:
        return {"space": int(sid), "keys": False, "vault": False, "current": None, "versions": [], "wrappers": []}
    cur = row["keys"][str(row["current"])]
    return {
        "space": int(sid),
        "keys": True,
        "vault": SERVER not in cur["wrapped"],
        "current": row["current"],
        "versions": sorted(int(v) for v in row["keys"]),
        "wrappers": sorted(cur["wrapped"]),
        "unlocked": all((int(sid), int(v)) in _cache(db) for v in row["keys"]),
    }


def data_key(db, cfg, sid, version=None, create=True):
    """The namespace's data key (the current version unless one is named), as (version, key)."""
    sid = int(sid)
    cache = _cache(db)
    if version is not None and (sid, int(version)) in cache:
        return int(version), cache[(sid, int(version))]
    with _LOCK:
        row = _row(db, sid) or (_create(db, cfg, sid) if create else None)
    if not row:
        raise KeyError(sid)
    version = int(version or row["current"])
    if (sid, version) in cache:
        return version, cache[(sid, version)]
    entry = (row.get("keys") or {}).get(str(version))
    if not entry:
        raise Damaged(f"namespace {sid} has no key version {version}")
    wrapped = entry["wrapped"].get(SERVER)
    if wrapped is None:
        raise Locked(sid)
    try:
        key = unwrap(server_kek(cfg), wrapped, sid, version, SERVER)
    except Exception:  # noqa: BLE001
        raise Damaged(f"namespace {sid}'s key doesn't open with this server's secret (was ARCHIVE_SECRET_KEY changed?)") from None
    cache[(sid, version)] = key
    return version, key


def add_wrapper(db, cfg, sid, name, kek):
    """Let another key open the namespace (every version of its key); the namespace must be open now."""
    sid = int(sid)
    with _LOCK:
        row = _row(db, sid) or _create(db, cfg, sid)
        keys = row["keys"]
        for v in keys:
            _, key = data_key(db, cfg, sid, int(v))
            keys[v]["wrapped"][name] = wrap(kek, key, sid, int(v), name)
        _save(db, sid, row, keys)


def remove_wrapper(db, sid, name):
    """Stop a key opening the namespace. Removing "server" makes it a vault; the last wrapper can't be removed."""
    sid = int(sid)
    with _LOCK:
        row = _row(db, sid)
        if not row:
            return
        keys = row["keys"]
        for v in keys:
            w = keys[v]["wrapped"]
            if name in w and len(w) == 1:
                raise ValueError("the last key that opens this namespace can't be removed")
            w.pop(name, None)
        _save(db, sid, row, keys)


def unlock(db, sid, name, kek):
    """Open a namespace with one of its wrappers for this process; raises if that key doesn't open it."""
    sid = int(sid)
    row = _row(db, sid)
    if not row:
        raise KeyError(sid)
    opened = {}
    for v, entry in row["keys"].items():
        if name not in entry["wrapped"]:
            raise Locked(sid)
        try:
            opened[(sid, int(v))] = unwrap(kek, entry["wrapped"][name], sid, int(v), name)
        except Exception:  # noqa: BLE001 - wrong key
            raise Locked(sid) from None
    _cache(db).update(opened)


def lock(db, sid):
    """Forget a namespace's keys in this process (a vault then needs unlocking again)."""
    for k in [k for k in _cache(db) if k[0] == int(sid)]:
        del _cache(db)[k]


def rotate(db, cfg, sid, keks=None):
    """Start a new key version, wrapped by the same keys as the current one; returns its number. A vault's own
    wrappers need their keys passed in keks ({name: key}), since the server can't wrap for them."""
    sid = int(sid)
    with _LOCK:
        row = _row(db, sid) or _create(db, cfg, sid)
        old = row["current"]
        data_key(db, cfg, sid, old)  # must be open
        new, key = old + 1, secrets.token_bytes(32)
        wrapped = {}
        for name in row["keys"][str(old)]["wrapped"]:
            kek = server_kek(cfg) if name == SERVER else (keks or {}).get(name)
            if kek is None:
                raise ValueError(f"rotating needs the key for {name}")
            wrapped[name] = wrap(kek, key, sid, new, name)
        keys = {**row["keys"], str(new): {"created_at": store.now(), "wrapped": wrapped}}
        _save(db, sid, row, keys, current=new)
        _cache(db)[(sid, new)] = key  # only once stored: a rotation that lost a race must not write with its key
    return new


# ---------- small values ----------
def seal(db, cfg, sid, data: bytes, context: str) -> str:
    """Encrypt a small value with the namespace's key; the context is bound in, so it can't be moved elsewhere."""
    version, key = data_key(db, cfg, sid)
    nonce = secrets.token_bytes(12)
    return f"k1.{int(sid)}.{version}." + base64.urlsafe_b64encode(nonce + _aes(key).encrypt(nonce, data, context.encode())).decode()


def unseal(db, cfg, sealed: str, context: str) -> bytes:
    _, sid, version, body = sealed.split(".", 3)
    _, key = data_key(db, cfg, int(sid), int(version), create=False)
    raw = base64.urlsafe_b64decode(body)
    try:
        return _aes(key).decrypt(raw[:12], raw[12:], context.encode())
    except Exception:  # noqa: BLE001
        raise Damaged("sealed value doesn't open") from None


def is_sealed(value) -> bool:
    return isinstance(value, str) and value.startswith("k1.")


# ---------- files ----------
def _nonce(prefix, i, last):
    return prefix + struct.pack(">IB", i, 1 if last else 0)


def is_encrypted(path) -> bool:
    try:
        with open(path, "rb") as f:
            return f.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


class Writer:
    """Writes an encrypted file chunk by chunk; nothing is in place until close(), which renames it over the target.
    Leaving a `with` block on an error, or dropping the writer unclosed, throws the partial file away instead."""

    def __init__(self, db, cfg, sid, path, chunk=CHUNK):
        version, self._key = data_key(db, cfg, sid)
        self._aes = _aes(self._key)
        self._path = pathlib.Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._prefix = secrets.token_bytes(7)
        self._header = HEADER.pack(MAGIC, int(sid), version, chunk, self._prefix)
        self._chunk, self._buf, self._i, self._done = chunk, bytearray(), 0, False
        fd, self._tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".enc-")
        self._f = os.fdopen(fd, "wb")
        self._f.write(self._header)

    def __enter__(self):
        return self

    def __exit__(self, kind, *_):
        if kind is None:
            self.close()
        else:
            self.abort()

    def __del__(self):
        if not getattr(self, "_done", True):
            self.abort()

    def write(self, b):
        self._buf += b
        while len(self._buf) > self._chunk:  # keep at least one byte back: the last chunk is written on close
            part = bytes(self._buf[: self._chunk])
            del self._buf[: self._chunk]
            self._f.write(self._aes.encrypt(_nonce(self._prefix, self._i, False), part, self._header))
            self._i += 1
        return len(b)

    def close(self):
        if self._done:
            return
        self._done = True
        try:
            self._f.write(self._aes.encrypt(_nonce(self._prefix, self._i, True), bytes(self._buf), self._header))
            self._f.flush()
            os.fsync(self._f.fileno())
            self._f.close()
            os.replace(self._tmp, self._path)
        except BaseException:
            self.abort()
            raise

    def abort(self):
        """Throw away what was written; the target is left as it was."""
        self._done = True
        with contextlib.suppress(OSError):
            self._f.close()
        with contextlib.suppress(OSError):
            os.unlink(self._tmp)


class Reader(io.RawIOBase):
    """Reads an encrypted file as its plain bytes, with seek, decrypting only the chunks a read touches."""

    def __init__(self, db, cfg, path):
        self._f = open(path, "rb")
        head = self._f.read(HEADER.size)
        if len(head) < HEADER.size or not head.startswith(MAGIC):
            self._f.close()
            raise Damaged(f"{path} is not an encrypted file")
        _, sid, version, self._chunk, self._prefix = HEADER.unpack(head)
        self.space, self.version = sid, version
        try:
            _, key = data_key(db, cfg, sid, version, create=False)
        except BaseException:
            self._f.close()
            raise
        self._aes, self._header = _aes(key), head
        body = os.fstat(self._f.fileno()).st_size - HEADER.size
        step = self._chunk + TAG
        self._chunks = max(1, -(-body // step))
        last = body - (self._chunks - 1) * step - TAG
        if body < TAG or last < 0:
            self._f.close()
            raise Damaged(f"{path} is cut short")
        self.size = (self._chunks - 1) * self._chunk + last
        self._pos, self._cached = 0, (None, b"")

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self._pos

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self._pos, io.SEEK_END: self.size}[whence]
        self._pos = max(0, base + offset)
        return self._pos

    def _block(self, i):
        if self._cached[0] == i:
            return self._cached[1]
        step = self._chunk + TAG
        self._f.seek(HEADER.size + i * step)
        raw = self._f.read(step)
        try:
            plain = self._aes.decrypt(_nonce(self._prefix, i, i == self._chunks - 1), raw, self._header)
        except Exception:  # noqa: BLE001
            raise Damaged("an encrypted file was changed or cut short") from None
        self._cached = (i, plain)
        return plain

    def readinto(self, b):
        if self._pos >= self.size:
            return 0
        i, off = divmod(self._pos, self._chunk)
        data = self._block(i)[off : off + len(b)]
        b[: len(data)] = data
        self._pos += len(data)
        return len(data)

    def read(self, n=-1):
        if n is None or n < 0:
            n = self.size - self._pos
        out = bytearray()
        while len(out) < n and self._pos < self.size:
            i, off = divmod(self._pos, self._chunk)
            part = self._block(i)[off : off + n - len(out)]
            out += part
            self._pos += len(part)
        return bytes(out)

    def readall(self):
        return self.read(-1)

    def close(self):
        with contextlib.suppress(OSError):
            self._f.close()
        super().close()


def encrypt_file(db, cfg, sid, path, chunk=CHUNK, force=False):
    """Encrypt a file in place (atomically); a file already encrypted is left alone, unless `force` says it's known to
    be plain (one that has just arrived and only happens to start like one). Returns whether it changed."""
    if not force and is_encrypted(path):
        return False
    with Writer(db, cfg, sid, path, chunk=chunk) as w, open(path, "rb") as src:
        while part := src.read(1024 * 1024):
            w.write(part)
    return True


def decrypt_file(db, cfg, path):
    """Turn an encrypted file back into its plain bytes in place (atomically). Returns whether it changed."""
    if not is_encrypted(path):
        return False
    p = pathlib.Path(path)
    r = Reader(db, cfg, p)  # before the temp file, so a locked or damaged file leaves nothing behind
    fd, tmp = tempfile.mkstemp(dir=p.parent, prefix=".dec-")
    try:
        with r, os.fdopen(fd, "wb") as out:
            while part := r.read(1024 * 1024):
                out.write(part)
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
    return True


def open_plain(db, cfg, path):
    """A file's plain bytes for reading, encrypted or not."""
    return Reader(db, cfg, path) if is_encrypted(path) else open(path, "rb")


@contextlib.contextmanager
def plain_path(db, cfg, path):
    """A path to a file's plain bytes, for tools that need one (ffmpeg, LibreOffice): the file itself when it isn't
    encrypted, else a private copy under data_dir/tmp that is removed afterwards."""
    if not is_encrypted(path):
        yield str(path)
        return
    d = pathlib.Path(cfg["data_dir"]) / "tmp"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    r = Reader(db, cfg, path)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=pathlib.Path(path).suffix)
    try:
        with r, os.fdopen(fd, "wb") as out:
            while part := r.read(1024 * 1024):
                out.write(part)
        yield tmp
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp)


# ---------- files Lens keeps ----------
def enabled(cfg) -> bool:
    return bool((cfg.get("encryption") or {}).get("files"))


def owned(cfg, path) -> bool:
    """Whether a file is Lens's own, under data_dir (uploads, attachments, captures, imports), rather than one of the
    folders it scans, which it only ever reads."""
    try:
        return pathlib.Path(path).resolve().is_relative_to(pathlib.Path(cfg["data_dir"]).resolve())
    except (OSError, ValueError, TypeError):
        return False


def protect(db, cfg, sid, path) -> bool:
    """Encrypt a file Lens has just stored (so it is plain, whatever its first bytes), when encryption.files is on; its
    modification time is kept. Returns whether it was encrypted. A vault nobody has unlocked here raises Locked: its
    files are never stored plain."""
    if not (enabled(cfg) and owned(cfg, path)):
        return False
    st = os.stat(path)
    encrypt_file(db, cfg, sid, path, force=True)
    os.utime(path, (st.st_atime, st.st_mtime))
    return True


def plain_size(db, cfg, path) -> int:
    """A file's size as its plain bytes."""
    if is_encrypted(path):
        with Reader(db, cfg, path) as r:
            return r.size
    return os.path.getsize(path)


_HELD = threading.local()
_WORK = threading.Lock()


def _work_dir(cfg):
    d = pathlib.Path(cfg["data_dir"]) / "tmp" / "work"
    d.mkdir(parents=True, exist_ok=True, mode=0o700)
    return d


def _hold(path):
    """Keep a shared lock on a working copy until release(), so no sweep (in any process) removes it while in use."""
    import fcntl

    held = getattr(_HELD, "fds", None)
    if held is None:
        held = _HELD.fds = {}
    if path in held:
        return
    fd = os.open(path, os.O_RDONLY)
    fcntl.flock(fd, fcntl.LOCK_SH)
    try:
        if os.fstat(fd).st_ino != os.stat(path).st_ino:  # swept by another process meanwhile
            raise FileNotFoundError(path)
    except OSError:
        os.close(fd)
        raise
    held[path] = fd


def release():
    """Let go of the working copies this thread is using (at the end of a job)."""
    for fd in (getattr(_HELD, "fds", None) or {}).values():
        with contextlib.suppress(OSError):
            os.close(fd)
    _HELD.fds = {}


@contextlib.contextmanager
def work(cfg):
    """Around one piece of work (a job, one recording of a batch): the working copies it takes are let go after, and
    those nobody else holds are swept once unused."""
    try:
        yield
    finally:
        release()
        with contextlib.suppress(Exception):
            sweep(cfg)


def _in_use(path):
    import fcntl

    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except OSError:
        return True
    finally:
        os.close(fd)


def working_copy(db, cfg, path):
    """A path the tools that need one (ffmpeg, pdftoppm, LibreOffice, …) can read: the file itself when it isn't
    encrypted, else a plain copy under the same name in data_dir/tmp/work, made once. It is held until this thread
    calls release() (jobs do when they end) and removed by sweep() once nobody holds it and it has gone unused for
    encryption.work_minutes."""
    if not path or not is_encrypted(path):
        return path
    import hashlib

    st = os.stat(path)
    key = hashlib.sha256(f"{os.path.abspath(path)}:{st.st_size}:{st.st_mtime_ns}".encode()).hexdigest()[:32]
    folder = _work_dir(cfg) / key
    out = folder / pathlib.Path(path).name  # its own name: readers go by the extension, and some show the name
    with _WORK:
        _make(db, cfg, path, folder, out)
        try:
            _hold(str(out))
        except FileNotFoundError:  # another process swept it just now: make it again
            _make(db, cfg, path, folder, out)
            _hold(str(out))
        os.utime(folder)
    return str(out)


def _make(db, cfg, path, folder, out):
    if out.exists():
        return
    folder.mkdir(exist_ok=True, mode=0o700)
    r = Reader(db, cfg, path)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".work-")
    try:
        with r, os.fdopen(fd, "wb") as f:
            while part := r.read(1024 * 1024):
                f.write(part)
        os.replace(tmp, out)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def sweep(cfg, minutes=None):
    """Remove plain working copies nobody holds that have gone unused for encryption.work_minutes. Returns how many
    went."""
    import shutil
    import time

    d = pathlib.Path(cfg["data_dir"]) / "tmp" / "work"
    if not d.is_dir():
        return 0
    limit = time.time() - 60 * (minutes if minutes is not None else (cfg.get("encryption") or {}).get("work_minutes") or 30)
    gone = 0
    with _WORK:  # not while this process is making or taking one
        for p in d.iterdir():
            with contextlib.suppress(OSError):
                if p.stat().st_mtime >= limit or any(_in_use(f) for f in p.iterdir() if f.is_file()):
                    continue
                shutil.rmtree(p)
                gone += 1
    return gone


def stored_files(db, cfg):
    """(space, path) for every file Lens keeps of the archive: recordings' own files under data_dir (uploads, email
    attachments, web captures, IIIF imports) and resources' supplementary files. Files in scanned folders aren't
    among them."""
    from . import files as filemod

    for r in db.rows("SELECT space, path FROM recording WHERE path != NONE AND remote = NONE"):
        p = store.resolve_path(cfg, r["path"])
        if p and owned(cfg, p) and os.path.isfile(p):
            yield r["space"], p
    for f in db.rows("SELECT record::id(id) AS id, recording, space, name FROM resource_file"):
        p = filemod.path_of(cfg, f)
        if p.is_file():
            yield f["space"], str(p)


def encrypt_all(db, cfg, decrypt=False, log=print):
    """Encrypt (or, with decrypt, turn back) every file Lens keeps; files already that way are skipped, so it can run
    again after stopping half way. Returns how many changed."""
    changed = 0
    for sid, p in stored_files(db, cfg):
        try:
            st = os.stat(p)
            done = decrypt_file(db, cfg, p) if decrypt else encrypt_file(db, cfg, sid, p)
        except (Locked, Damaged, OSError) as e:  # a vault, a damaged file, one gone or not writable: the rest go on
            log(f"skipped {p}: {e}")
            continue
        if done:
            os.utime(p, (st.st_atime, st.st_mtime))
            changed += 1
    log(f"{'decrypted' if decrypt else 'encrypted'} {changed} file(s)")
    return changed
