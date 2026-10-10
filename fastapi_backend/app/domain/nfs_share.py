"""The file tree over NFS (docs/file-tree.md, NFS): graph nodes as folders and assets as files, read-only, for the
machines on the networks you allow (Settings → NFS share).

Off by default. Turned on, one worker runs `rclone serve nfs --read-only` (rclone is in Lens's image already) over
Lens's own S3 server, signed in with an S3 key made for the account the share shows the tree as (`nfs.account`;
nobody picked: the first admin), so the share shows exactly what that person sees in the web app and nothing else.
NFS has no sign-in of its own, so rclone listens on 127.0.0.1 only and a relay (share_relay.py) on the published port
lets in only `nfs.allowed_networks`.

One process runs it at a time, holding a lease on the `app_service:nfs` row like the local model does, and keeps its
state there (running, the port, the last lines rclone printed).
"""

from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import socket
import subprocess
import threading
import time

from . import share_relay, store

R = store.R
ROW = R("app_service", "nfs")
LEASE_SECONDS = 30
TICK_SECONDS = 5
RETRY = (5, 30, 120, 600)
LOG_LINES = 40


def conf(cfg):
    return {**store.DEFAULTS["nfs"], **(cfg.get("nfs") or {})}


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ts(t=None):
    return (t or _now()).isoformat()


def state(db):
    row = db.one("SELECT * FROM $r", r=ROW) or {}
    row.pop("id", None)
    if isinstance(row.get("log"), str):
        row["log"] = json.loads(row["log"])
    return row


def status(db, cfg):
    """What Settings → NFS share shows: on or off, running where, the mount command, and what went wrong."""
    s, c = state(db), conf(cfg)
    live = bool(s.get("at") and s["at"] >= _ts(_now() - dt.timedelta(seconds=LEASE_SECONDS)))
    phase = (s.get("phase") or "off") if live and c["enabled"] else "off"
    published = os.environ.get("LENS_NFS_PORT") or c["port"]  # Docker publishes it on another port of this machine
    return {
        "enabled": c["enabled"],
        "phase": phase,
        "port": int(published),
        "allowed_networks": c["allowed_networks"],
        "account": s.get("account") if phase == "running" else None,
        "error": s.get("error") if c["enabled"] else None,
        "refused": s.get("refused") or 0,
        "log": s.get("log") or [],
        "process": s.get("owner") if live else None,
        "mount": f"sudo mount -t nfs -o port={published},mountport={published},tcp,vers=3,ro <this machine>:/ /mnt/lens",
        "rclone": bool(rclone_binary()),
    }


def rclone_binary():
    return shutil.which("rclone")


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _account(db, c):
    """The account the share shows the tree as: nfs.account, or the first admin."""
    if c.get("account"):
        row = db.one("SELECT record::id(id) AS id, email, admin, disabled FROM $r", r=R("account", int(c["account"])))
        if not row or row.get("disabled"):
            raise ShareError("the account picked in Settings → NFS share is gone or disabled: pick another")
        return row
    rows = db.rows("SELECT record::id(id) AS id, email, admin FROM account WHERE admin = true AND disabled != true ORDER BY id LIMIT 1")
    if not rows:
        raise ShareError("there is no admin account to show the tree as yet")
    return rows[0]


class ShareError(RuntimeError):
    pass


def s3_remote(db, cfg, account):
    """rclone's command-line remote and environment for Lens's own S3 server, signed in as `account`. The S3 thread's
    server (s3_server.py) owns the endpoint and the keys; this only asks it for them."""
    from . import s3_server  # noqa: PLC0415 - optional until the S3 server is in

    endpoint, key_id, secret = s3_server.service_key(db, cfg, account["id"], "nfs")
    env = {
        "RCLONE_S3_PROVIDER": "Other",
        "RCLONE_S3_ENDPOINT": endpoint,
        "RCLONE_S3_ACCESS_KEY_ID": key_id,
        "RCLONE_S3_SECRET_ACCESS_KEY": secret,
        "RCLONE_S3_FORCE_PATH_STYLE": "true",
    }
    return ":s3:", env


class Runner:
    """Holds the lease and runs rclone serve nfs plus the relay while nfs.enabled is on."""

    def __init__(self, db, cfg_fn, name, log=None, remote=s3_remote):
        self.db, self.cfg_fn, self.name, self.log, self.remote = db, cfg_fn, name, log or (lambda *a: None), remote
        self.proc: subprocess.Popen | None = None
        self.relay: share_relay.Relay | None = None
        self.sig = None
        self.lines: list[str] = []
        self.phase, self.error, self.account = "off", None, None
        self.fails, self.next_try = 0, 0.0
        self.lock = threading.Lock()

    def _lease(self):
        now = _now()
        return bool(
            self.db.rows(
                "UPSERT $r MERGE {owner: $me, at: $t} WHERE owner = NONE OR owner = $me OR at < $stale RETURN AFTER",
                r=ROW,
                me=self.name,
                t=_ts(now),
                stale=_ts(now - dt.timedelta(seconds=LEASE_SECONDS)),
            )
        )

    def _save(self):
        with self.lock:
            d = {"phase": self.phase, "error": self.error, "account": self.account, "log": json.dumps(self.lines[-LOG_LINES:]),
                 "refused": self.relay.refused if self.relay else 0}  # fmt: skip
        self.db.q("UPDATE $r MERGE $d WHERE owner = $me", r=ROW, d=d, me=self.name)

    def _say(self, line):
        with self.lock:
            self.lines = (self.lines + [str(line)[:300]])[-LOG_LINES:]

    def stop_serving(self):
        relay, self.relay = self.relay, None
        if relay:
            relay.stop()
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def _read(self, proc):
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                self._say(line)
        code = proc.wait()
        if self.proc is proc:
            self._failed(f"rclone stopped (exit {code}); {self.lines[-1] if self.lines else 'no output'}")
            self.stop_serving()
            self._save()

    def _failed(self, error):
        self.error, self.phase = error, "error"
        self.fails += 1
        self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]

    def _start(self, cfg, c):
        exe = rclone_binary()
        if not exe:
            raise ShareError("rclone isn't installed here (it is in Lens's Docker image)")
        account = _account(self.db, c)
        remote, env_extra = self.remote(self.db, cfg, account)
        inner = _free_port()
        cmd = [exe, "serve", "nfs", remote, "--addr", f"127.0.0.1:{inner}", "--read-only", "--vfs-cache-mode", "off",
               "--dir-cache-time", "30s", "--no-checksum"]  # fmt: skip
        env = {**os.environ, **env_extra}  # the key in the environment, not on the command line where `ps` shows it
        self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="nfs-rclone-log").start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and self.proc and self.proc.poll() is None:
            try:
                socket.create_connection(("127.0.0.1", inner), timeout=1).close()
                break
            except OSError:
                time.sleep(0.2)
        if not (self.proc and self.proc.poll() is None):
            raise ShareError(f"rclone didn't start: {self.lines[-1] if self.lines else 'no output'}")
        try:
            self.relay = share_relay.Relay(c["bind"], int(c["port"]), inner, share_relay.networks(c["allowed_networks"]), self.log).start()
        except OSError as e:
            raise ShareError(f"port {c['port']}: {e.strerror or e}") from None
        self.account = account.get("email")
        self.phase, self.error, self.fails = "running", None, 0
        self.log(f"nfs: serving the file tree as {self.account} on port {self.relay.port}")

    def tick(self):
        cfg = self.cfg_fn()
        c = conf(cfg)
        if not c["enabled"]:
            if self.proc or self.relay or self.phase != "off":
                self.stop_serving()
                self.sig, self.phase, self.error, self.account = None, "off", None, None
                if self._lease():
                    self._save()
            return
        if not self._lease():
            if self.proc or self.relay:
                self.stop_serving()
            return
        sig = json.dumps([c["port"], c["account"], c["bind"]])
        if sig != self.sig:
            self.stop_serving()
            self.sig, self.fails, self.next_try, self.error, self.lines = sig, 0, 0.0, None, []
        if self.relay is not None:
            self.relay.nets = share_relay.networks(c["allowed_networks"])  # follows the setting without a restart
        if (self.proc and self.proc.poll() is None) or time.monotonic() < self.next_try:
            self._save()
            return
        try:
            self._start(cfg, c)
        except (ShareError, OSError, ImportError) as e:
            self.stop_serving()
            self._failed(str(e) if not isinstance(e, ImportError) else "this Lens has no S3 server yet: upgrade")
        self._save()

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the runner alive; the error shows in Settings
                self.error = f"{type(e).__name__}: {e}"
                try:
                    self._save()
                except Exception:  # noqa: BLE001
                    pass
            stop.wait(TICK_SECONDS)
        self.stop_serving()
        self.db.q("UPDATE $r SET owner = NONE, phase = 'off' WHERE owner = $me", r=ROW, me=self.name)


def start(db, cfg_fn, stop, name, log=None):
    r = Runner(db, cfg_fn, name, log)
    threading.Thread(target=r.loop, args=(stop,), daemon=True, name="nfs-share").start()
    return r
