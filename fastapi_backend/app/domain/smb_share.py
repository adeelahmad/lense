"""Lens as a read-only SMB share: Finder, Explorer and Linux mount the file tree of the graph like any network drive.

What the share holds is the shared file tree (filetree.py, docs/file-tree.md): graph nodes are folders, nested by their links, with the
assets as files at the leaves. Each person sees what they may see in Lens, nothing more, and nobody can write: every
call that would change something is refused before it reaches the tree.

The server is impacket's SMB server (Apache-style license, pure Python), run inside the worker while Settings ›
File sharing › SMB is on. impacket serves folders on disk through `os`; here its module-level `os` is a small stand-in
(VirtualOS) that answers from the tree, and nothing outside the share's virtual root can be named. On top of that the
server is hardened for this use: a random NTLM challenge per connection, no guest or anonymous sessions, SMB1 refused
past the negotiation (clients move to SMB2), the NTLM responses impacket logs at INFO kept out of the log, and
connections only from the allowed networks.

People sign in with a file access key from Settings › File sharing (the same key the S3 and WebDAV shares take): the
key id is the user name and its secret the password. NTLM needs the server to know the password, which is why the
key's secret is sealed rather than hashed. Revoking a key ends its sessions within KEY_SECONDS.

Off by default. The port inside the container is 4445; docker-compose publishes it where the web app is
(LENS_SHARE_BIND, else LENS_BIND: this machine only unless install.sh opened Lens to the network). Windows only
talks to port 445: set LENS_SMB_PORT=445 for it. See docs/file-sharing.md.
"""

from __future__ import annotations

import errno
import ipaddress
import itertools
import logging
import os as _os
import posixpath
import socket
import stat as _stat
import threading
import time
import types
from dataclasses import dataclass

from . import store

R = store.R
ROOT = "/lens-smb"  # the share's virtual root: /lens-smb/<key id>/<path in the tree>
SHARE = "LENS"
PORT = 4445
SERVICE_SECONDS = 10  # how often the worker follows the settings
TREE_SECONDS = 10  # how long one person's view of the tree is reused
KEY_SECONDS = 30  # how long a key lookup is trusted before it's checked again (so revoking takes effect)
RETRY_SECONDS = 60  # after a port that couldn't be opened
_WRITE = _os.O_WRONLY | _os.O_RDWR | _os.O_CREAT | _os.O_TRUNC | _os.O_APPEND


@dataclass
class Entry:
    """One folder or file in the tree, as the share shows it (`source`: the tree's own entry, to read it by)."""

    name: str
    is_dir: bool
    size: int = 0
    mtime: float = 0.0
    source: object = None


# ---------- who is signed in ----------
class Keys:
    """File access keys, looked up when someone signs in and again every KEY_SECONDS while they stay connected.

    `lookup(key_id)` answers {account, secret} or None (s3.lookup_key: the S3 share's keys)."""

    def __init__(self, lookup, touch=None):
        self.lookup, self.touch = lookup, touch or (lambda key_id: None)
        self.cache: dict[str, tuple[float, dict | None]] = {}
        self.lock = threading.Lock()

    def get(self, key_id):
        key_id = (key_id or "").lower()
        if not key_id:
            return None
        with self.lock:
            hit = self.cache.get(key_id)
        if hit and time.monotonic() - hit[0] < KEY_SECONDS:
            return hit[1]
        try:
            row = self.lookup(key_id)
        except Exception:  # noqa: BLE001 - the database is away: nobody signs in rather than everybody
            row = None
        with self.lock:
            self.cache[key_id] = (time.monotonic(), row)
        if row is not None:
            try:
                self.touch(key_id)  # last used: at sign-in, then every KEY_SECONDS while connected
            except Exception:  # noqa: BLE001
                pass
        return row


class Credentials:
    """What impacket checks a sign-in against ({name: (uid, lmhash, nthash)}), from the keys as asked."""

    def __init__(self, keys: Keys):
        self.keys = keys

    def __len__(self):
        return 1  # never empty: impacket lets anyone in when it has no credentials at all

    def __contains__(self, name):
        return self.keys.get(name) is not None

    def __getitem__(self, name):
        row = self.keys.get(name)
        if row is None:
            raise KeyError(name)
        return (0, b"", nt_hash(row["secret"]))


def nt_hash(secret: str) -> bytes:
    """The NT hash NTLM checks a password with: MD4 of the UTF-16LE password."""
    from Cryptodome.Hash import MD4  # impacket's dependency

    return MD4.new(secret.encode("utf-16le")).digest()


# ---------- the file system impacket sees ----------
class VirtualOS(types.ModuleType):
    """Just enough of `os` for impacket.smbserver, answered from the tree: read-only, and nothing outside ROOT.

    Paths are ROOT/<key id>/<path>; the key says whose view of the tree it is (and is checked again as it ages)."""

    def __init__(self, tree, keys: Keys):
        super().__init__("os")
        for k in ("O_RDONLY", "O_WRONLY", "O_RDWR", "O_CREAT", "O_TRUNC", "O_APPEND", "O_EXCL", "SEEK_SET", "SEEK_CUR", "SEEK_END"):
            setattr(self, k, getattr(_os, k))
        self.sep, self.name, self.O_BINARY = "/", "posix", 0
        self.getpid, self.urandom, self.fspath, self.environ = _os.getpid, _os.urandom, _os.fspath, {}
        self.tree, self.keys = tree, keys
        self.fds: dict[int, list] = {}
        self.lock = threading.Lock()
        self.next_fd = itertools.count(1 << 20)
        self.path = _Path(self)

    def _where(self, p):
        p = posixpath.normpath(_os.fspath(p) if not isinstance(p, bytes) else p.decode())
        if p != ROOT and not p.startswith(ROOT + "/"):
            raise PermissionError(errno.EACCES, "outside the share", p)
        key_id, _, rel = p[len(ROOT) + 1 :].partition("/")
        row = self.keys.get(key_id)
        if row is None:
            raise PermissionError(errno.EACCES, "signed out", p)
        return row["account"], rel

    def _entry(self, p):
        if posixpath.normpath(_os.fspath(p)) == ROOT:  # the parent of every key's folder (a listing's "..")
            return None, None, Entry("", True)
        account, rel = self._where(p)
        e = self.tree.entry(account, rel)
        if e is None:
            raise FileNotFoundError(errno.ENOENT, "no such file or folder", p)
        return account, rel, e

    def stat(self, p, *a, **k):
        _, _, e = self._entry(p)
        mode = (_stat.S_IFDIR | 0o555) if e.is_dir else (_stat.S_IFREG | 0o444)
        t = float(e.mtime or 0)
        return _os.stat_result((mode, 0, 0, 1, 0, 0, int(e.size or 0), t, t, t))

    def listdir(self, p):
        account, rel, e = self._entry(p)
        if account is None:
            raise PermissionError(errno.EACCES, "not a share", p)
        if not e.is_dir:
            raise NotADirectoryError(errno.ENOTDIR, "not a folder", p)
        return [c.name for c in self.tree.children(account, rel)]

    def open(self, p, flags, mode=0o777, **k):
        if flags & _WRITE:
            raise PermissionError(errno.EROFS, "the share is read-only", p)
        account, rel, e = self._entry(p)
        if account is None:
            raise PermissionError(errno.EACCES, "not a share", p)
        reader = None if e.is_dir else self.tree.open(account, e)
        with self.lock:
            fd = next(self.next_fd)
            self.fds[fd] = [reader, e, 0]
        return fd

    def _fd(self, fd):
        f = self.fds.get(fd)
        if f is None:
            raise OSError(errno.EBADF, "bad file descriptor")
        return f

    def lseek(self, fd, pos, how):
        f = self._fd(fd)
        f[2] = max(0, pos if how == _os.SEEK_SET else (f[2] + pos if how == _os.SEEK_CUR else int(f[1].size or 0) + pos))
        return f[2]

    def read(self, fd, n):
        f = self._fd(fd)
        if f[0] is None:
            raise IsADirectoryError(errno.EISDIR, "is a folder")
        try:
            data = f[0].read(f[2], int(n)) or b""
        except OSError:
            raise
        except Exception as e:  # a locked vault, a storage connection that's away: the client sees "access denied"
            raise PermissionError(errno.EACCES, f"can't be read now: {type(e).__name__}") from None
        f[2] += len(data)
        return data

    def close(self, fd):
        with self.lock:
            f = self.fds.pop(fd, None)
        if f and f[0] is not None:
            f[0].close()

    def fsync(self, fd):
        return None

    def _read_only(self, *a, **k):
        raise PermissionError(errno.EROFS, "the share is read-only")

    write = remove = unlink = mkdir = makedirs = rmdir = rename = replace = utime = chmod = _read_only

    def setreuid(self, *a):
        return None

    setregid = setreuid


class _Path:
    """os.path for VirtualOS: the pure functions of posixpath, and the ones that look at files ask the tree."""

    join = staticmethod(posixpath.join)
    basename = staticmethod(posixpath.basename)
    dirname = staticmethod(posixpath.dirname)
    normpath = staticmethod(posixpath.normpath)
    splitext = staticmethod(posixpath.splitext)
    commonprefix = staticmethod(posixpath.commonprefix)
    sep = "/"

    def __init__(self, vos):
        self.vos = vos

    @staticmethod
    def realpath(p, *a, **k):
        return posixpath.normpath(posixpath.join("/", _os.fspath(p)))

    abspath = realpath

    def _get(self, p):
        try:
            return self.vos._entry(p)[2]
        except OSError:
            return None

    def exists(self, p):
        return self._get(p) is not None

    def isdir(self, p):
        e = self._get(p)
        return bool(e and e.is_dir)

    def isfile(self, p):
        e = self._get(p)
        return bool(e and not e.is_dir)

    def getsize(self, p):
        return int(self.vos._entry(p)[2].size or 0)


# ---------- the shared file tree (filetree.py) ----------
class FileTree:
    """filetree.Tree as the share needs it: entries by path for an account, and readers that stream a file onwards.

    A person's Tree (who they are and what they may see) is made at most every TREE_SECONDS, so a folder listing and
    the many look-ups an SMB client makes right after it don't each ask the database again."""

    def __init__(self, db, cfg_fn):
        self.db, self.cfg_fn = db, cfg_fn
        self.trees: dict[str, tuple[float, object]] = {}
        self.lock = threading.Lock()

    def _tree(self, account):
        from . import auth, filetree

        with self.lock:
            hit = self.trees.get(account)
        if hit and time.monotonic() - hit[0] < TREE_SECONDS:
            return hit[1]
        row = auth.get_account(self.db, account)
        if not row or row.get("disabled"):
            raise PermissionError(errno.EACCES, "no such account")
        t = filetree.Tree(self.db, self.cfg_fn(), filetree.Viewer.of(self.db, row))
        with self.lock:
            self.trees[account] = (time.monotonic(), t)
        return t

    @staticmethod
    def _entry(e):
        return Entry(e.name, bool(e.folder), int(e.size or 0), _seconds(e.modified), e)

    def entry(self, account, path):
        from . import filetree

        try:
            return self._entry(self._tree(account).stat(path))
        except filetree.NotFound:
            return None

    def children(self, account, path):
        from . import filetree

        try:
            return [self._entry(e) for e in self._tree(account).list(path)]
        except (filetree.NotFound, filetree.NotAFolder):
            return []

    def open(self, account, e):
        return Reader(lambda start, end: e.source.read(self.db, self.cfg_fn(), start, end), e.size)


class Reader:
    """Reads a file from `chunks(start, end)` (an iterator of bytes), going on from where the last read stopped, so
    a client reading a file through moves along one stream; a jump starts a new one from there."""

    def __init__(self, chunks, size):
        self.chunks, self.size = chunks, int(size or 0)
        self.it = None
        self.at = 0  # where the stream is
        self.buf = b""

    def read(self, offset, n):
        if offset >= self.size or n <= 0:
            return b""
        if self.it is None or offset != self.at:
            self.close()
            self.it, self.at, self.buf = iter(self.chunks(offset, self.size - 1)), offset, b""
        while len(self.buf) < n:
            chunk = next(self.it, None)
            if not chunk:
                break
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        self.at += len(out)
        return out

    def close(self):
        close = getattr(self.it, "close", None)
        if close:
            close()
        self.it = None


def _seconds(when):
    if not when:
        return 0.0
    try:
        import datetime as dt

        return dt.datetime.fromisoformat(str(when).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


# ---------- the server ----------
def allowed(networks, address):
    """Whether a client address is in one of the networks (an empty list allows none)."""
    try:
        ip = ipaddress.ip_address(str(address).split("%")[0])
    except ValueError:
        return False
    if getattr(ip, "ipv4_mapped", None):
        ip = ip.ipv4_mapped
    for n in networks or ():
        try:
            if ip in ipaddress.ip_network(n, strict=False):
                return True
        except ValueError:
            continue
    return False


def _impacket():
    """impacket's server module with Lens's hooks in place (once): its `os` is VirtualOS, shares are per key."""
    from impacket import smbserver

    if not getattr(smbserver, "_lens", False):
        logging.getLogger("impacket.smbserver").setLevel(logging.WARNING)  # it logs NTLM responses at INFO
        plain = smbserver.searchShare

        def search_share(conn_id, share, server):
            """The share for this session: the key's own view of the tree, or nothing for a guest."""
            found = plain(conn_id, share, server)
            if found is None or getattr(server, "keys", None) is None:
                return found
            key_id = (server.getConnectionData(conn_id, False).get("user_name") or "").lower()
            if not key_id or server.keys.get(key_id) is None:
                return None
            if str(share).upper() == SHARE:
                found["path"] = f"{ROOT}/{key_id}"
            return found

        smbserver.searchShare = search_share
        smbserver._lens = True
    return smbserver


class Server:
    """One SMB server on (bind, port), serving `tree` to holders of file access keys, in its own threads."""

    def __init__(self, bind, port, tree, keys: Keys, networks=None, name="Lens", log=None):
        smbserver = _impacket()
        self.log = log or (lambda *a: None)
        self.networks = list(networks if networks is not None else store.DEFAULTS["smb"]["allowed_networks"])
        self.keys = keys
        self.live: set = set()  # open connections, closed when the server stops
        self.lock = threading.Lock()
        vos = VirtualOS(tree, keys)
        smbserver.os = vos
        outer = self

        class LensSMB(smbserver.SMBSERVER):
            local = threading.local()
            daemon_threads, block_on_close = True, False

            def process_request(self, request, client_address):
                with outer.lock:
                    outer.live.add(request)
                super().process_request(request, client_address)

            def shutdown_request(self, request):
                with outer.lock:
                    outer.live.discard(request)
                super().shutdown_request(request)

            def getSMBChallenge(self):  # noqa: N802 - impacket's name
                """A random challenge per connection (impacket's is fixed); one thread serves one connection."""
                if getattr(self.local, "challenge", None) is None:
                    self.local.challenge = _os.urandom(8)
                return self.local.challenge

            def getCredentials(self):  # noqa: N802
                return creds

            def verify_request(self, request, client_address):
                ok = allowed(outer.networks, client_address[0])
                if not ok:
                    outer.log(f"smb: refused {client_address[0]}: not in the allowed networks")
                return ok

            def processRequest(self, conn_id, data):  # noqa: N802
                if data[:4] == b"\xffSMB" and len(data) > 4 and data[4] != 0x72:  # SMB1, past SMB_COM_NEGOTIATE
                    raise ConnectionAbortedError("SMB1 is not served")
                return super().processRequest(conn_id, data)

        creds = Credentials(keys)
        cfg = smbserver.configparser.ConfigParser()
        cfg.add_section("global")
        for k, v in {
            "server_name": name[:15] or "LENS",
            "server_os": "Lens",
            "server_domain": "LENS",
            "log_file": "None",
            "rpc_apis": "no",
            "credentials_file": "",
            "anonymous_logon": "False",
            "SMB2Support": "True",
        }.items():
            cfg.set("global", k, v)
        for share, path, comment, kind in (("IPC$", "", "", "3"), (SHARE, ROOT, "Lens: the graph as files (read-only)", "0")):
            cfg.add_section(share)
            for k, v in {"comment": comment, "read only": "yes", "share type": kind, "path": path}.items():
                cfg.set(share, k, v)
        self.server = LensSMB((bind, int(port)), config_parser=cfg, ipv6=":" in bind)
        self.server.keys = keys
        self.server.processConfigFile()
        self.port = self.server.server_address[1]
        self.extras = [smbserver.SRVSServer(), smbserver.WKSTServer()]  # share lists for Explorer and Finder
        for x in self.extras:
            x.daemon = True
        self.extras[0].setServerConfig(cfg)  # the shares it lists
        self.extras[0].processConfigFile()
        self.server.registerNamedPipe("srvsvc", ("127.0.0.1", self.extras[0].getListenPort()))
        self.server.registerNamedPipe("wkssvc", ("127.0.0.1", self.extras[1].getListenPort()))
        self.thread = None

    def start(self):
        for x in self.extras:
            x.start()
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True, name=f"smb-{self.port}")
        self.thread.start()
        return self

    def stop(self):
        """Stop listening and end every open session (turning SMB off, or moving it, disconnects people)."""
        try:
            self.server.shutdown()
        finally:
            self.server.server_close()
            with self.lock:
                live, self.live = list(self.live), set()
            for sock in live:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass
            for x in self.extras:
                try:
                    x.server.server_close()
                except Exception:  # noqa: BLE001
                    pass


# ---------- following the settings ----------
def conf(cfg):
    return {**store.DEFAULTS["smb"], **(cfg.get("smb") or {})}


def _keys(db, cfg_fn):
    from . import s3

    return Keys(lambda key_id: s3.lookup_key(db, cfg_fn(), key_id), lambda key_id: s3.touch(db, key_id))


class Runner:
    """Runs the server while SMB is on, moves it when its port or networks change, and says how it's doing."""

    def __init__(self, db, cfg_fn, name=None, log=None, tree=None, keys=None):
        self.db, self.cfg_fn, self.log = db, cfg_fn, log or (lambda *a: None)
        self.name = name or f"{socket.gethostname()}-{_os.getpid()}"
        self.tree, self.keys = tree, keys
        self.server: Server | None = None
        self.want = None
        self.error = None
        self.retry = 0.0

    def apply(self):
        s = conf(self.cfg_fn())
        want = (s["bind"], int(s["port"])) if s.get("enabled") else None
        if self.server is not None:
            self.server.networks = list(s.get("allowed_networks") or [])
        if want == self.want and (self.server is not None or want is None or time.monotonic() < self.retry):
            return
        self.close()
        self.want, self.error = want, None
        if want is None:
            return
        try:
            self.tree = self.tree or FileTree(self.db, self.cfg_fn)
            self.keys = self.keys or _keys(self.db, self.cfg_fn)
            self.server = Server(want[0], want[1], self.tree, self.keys, s.get("allowed_networks"), log=self.log).start()
            self.log(f"smb: serving the file tree on port {self.server.port} (read-only)")
        except (OSError, ImportError) as e:
            self.error = (
                f"port {want[1]}: {e.strerror or e}" if isinstance(e, OSError) else "impacket isn't installed (pip install impacket)"
            )
            self.retry = time.monotonic() + RETRY_SECONDS
            self.log(f"smb: {self.error}")

    def heartbeat(self):
        if self.want is None:
            self.db.q("DELETE $r", r=R("share_service", f"smb-{self.name}"))
            return
        d = {"kind": "smb", "process": self.name, "at": store.now(), "running": self.server is not None, "error": self.error}
        if self.server is not None:
            d["port"] = self.server.port
        self.db.q("UPSERT $r CONTENT $d", r=R("share_service", f"smb-{self.name}"), d=store.clean(d))

    def close(self):
        if self.server is not None:
            try:
                self.server.stop()
            except Exception:  # noqa: BLE001
                pass
            self.server = None

    def loop(self, stop):
        while True:
            try:
                self.apply()
                self.heartbeat()
            except Exception as e:  # noqa: BLE001 - keep following the settings
                self.log(f"smb: {type(e).__name__}: {e}")
            if stop.wait(SERVICE_SECONDS):
                break
        self.close()


def start(db, cfg_fn, stop, name=None, log=None):
    """The SMB share thread, until `stop` is set. It does nothing while SMB is off."""
    r = Runner(db, cfg_fn, name, log)
    threading.Thread(target=r.loop, args=(stop,), daemon=True, name="smb-share").start()
    return r


def published_port(cfg):
    """The port clients use: where Docker publishes the share (LENS_SMB_PORT), else the one Lens listens on."""
    try:
        return int(_os.environ.get("LENS_SMB_PORT") or conf(cfg)["port"])
    except ValueError:
        return int(conf(cfg)["port"])


def status(db, cfg):
    """Whether SMB is on and served (by the workers heard from in the last minute), and how to connect to it."""
    s = conf(cfg)
    cut = time.time() - 60
    rows = [r for r in db.rows("SELECT * FROM share_service WHERE kind = 'smb'") if _seconds(r.get("at")) >= cut]
    running = [r for r in rows if r.get("running")]
    error = next((r.get("error") for r in rows if r.get("error")), None)
    port = published_port(cfg)
    try:
        import importlib.util

        installed = importlib.util.find_spec("impacket") is not None
    except (ImportError, ValueError):
        installed = False
    phase = "off" if not s.get("enabled") else "running" if running else "error" if error else "starting"
    host = "<lens-machine>"
    return {
        "enabled": bool(s.get("enabled")),
        "phase": phase,
        "port": port,
        "share": SHARE,
        "allowed_networks": list(s.get("allowed_networks") or []),
        "error": error if phase != "running" else None,
        "process": running[0].get("process") if running else None,
        "installed": installed,
        "address": f"smb://{host}{'' if port == 445 else f':{port}'}/{SHARE}",
        "mount": f"sudo mount -t cifs -o port={port},username=<key id>,ro,vers=2.0 //{host}/{SHARE} /mnt/lens",
    }
