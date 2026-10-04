"""Reaching Lens from the internet through a Cloudflare Tunnel, run by Lens itself (Settings › Remote access).

`cloudflared` makes an outbound connection to Cloudflare, so nothing on the router has to be opened, and Cloudflare
serves the web app at an https:// address (where passkeys work). Three ways to set it up:

* **quick**: a random https://<words>.trycloudflare.com address, no Cloudflare account needed. It changes every time
  the tunnel starts; for trying things out.
* **token**: a tunnel made in the Cloudflare dashboard (Zero Trust › Networks › Tunnels), its token pasted here, and
  the public hostname set up there. Give the hostname here too, so Lens knows its address.
* **managed**: Lens makes the tunnel, points it at the web app and adds the DNS record for a hostname on your
  Cloudflare domain (like lens.example.com), with an API token allowed to edit tunnels and DNS.

One process runs cloudflared at a time: it holds a lease on the `app_service:tunnel` row (renewed every few seconds)
and keeps what the tunnel is doing there (its address, whether it's connected, the last lines it printed), so every
process knows the address the web app is reached at.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.parse

import httpx

from . import settings, store

R = store.R
ROW = R("app_service", "tunnel")
MODES = ("off", "quick", "token", "managed")
LEASE_SECONDS = 30
TICK_SECONDS = 5
RETRY = (5, 15, 60, 300)
LOG_LINES = 40
API = "https://api.cloudflare.com/client/v4"
QUICK_URL = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
HOST = re.compile(r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
RELEASES = "https://github.com/cloudflare/cloudflared/releases/latest/download/"


class TunnelError(RuntimeError):
    """Setting the tunnel up didn't work; the message says why, for the admin."""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ts(t=None):
    return (t or _now()).isoformat(timespec="seconds")


def conf(cfg):
    return cfg.get("tunnel") or {}


def hostname(cfg):
    """The fixed public hostname (token and managed tunnels), lower case, or ""."""
    t = conf(cfg)
    return (t.get("hostname") or "").strip().lower().rstrip(".") if t.get("mode") in ("token", "managed") else ""


def origin(cfg):
    """Where cloudflared sends visitors: the web app, as this server reaches it."""
    from app.config import settings as env

    return (conf(cfg).get("origin") or os.environ.get("LENS_TUNNEL_ORIGIN") or env.FRONTEND_URL).rstrip("/")


def check(cfg):
    """Settings › Remote access before saving: ValueError for what can't work."""
    t = conf(cfg)
    mode = t.get("mode") or "off"
    if mode not in MODES:
        raise ValueError(f"the tunnel mode is one of {', '.join(MODES)}")
    host = (t.get("hostname") or "").strip().lower().rstrip(".")
    if mode in ("token", "managed") and not HOST.fullmatch(host):
        raise ValueError("enter the public hostname, like lens.example.com")
    if mode == "token" and not t.get("token"):
        raise ValueError("paste the tunnel's token from the Cloudflare dashboard")
    if mode == "managed" and not t.get("api_token"):
        raise ValueError("enter a Cloudflare API token that can edit tunnels and DNS")
    o = urllib.parse.urlsplit(origin(cfg))
    if o.scheme not in ("http", "https") or not o.hostname:
        raise ValueError("the web app's address for the tunnel is an http:// address, like http://frontend:3000")


# ---------- what other processes see ----------
_SEEN: dict[str, tuple[float, dict]] = {}


def state(db):
    """The tunnel's row: owner, mode, url, connected, error, log, ..."""
    row = db.one("SELECT * FROM $r", r=ROW) or {}
    row.pop("id", None)
    if row.get("log") and isinstance(row["log"], str):
        row["log"] = json.loads(row["log"])
    return row


def public_host(db):
    """The host name the tunnel serves the web app at right now (cached for a few seconds), or ""."""
    hit = _SEEN.get("host")
    if hit and time.monotonic() - hit[0] < TICK_SECONDS:
        return hit[1].get("host", "")
    s = state(db)
    live = s.get("at") and s["at"] >= _ts(_now() - dt.timedelta(seconds=LEASE_SECONDS))
    host = (urllib.parse.urlsplit(s.get("url") or "").hostname or "").lower() if live and s.get("url") else ""
    _SEEN["host"] = (time.monotonic(), {"host": host})
    return host


def status(db, cfg):
    s = state(db)
    live = bool(s.get("at") and s["at"] >= _ts(_now() - dt.timedelta(seconds=LEASE_SECONDS)))
    mode = conf(cfg).get("mode") or "off"
    return {
        "mode": mode,
        "running": live and bool(s.get("running")),
        "connected": live and bool(s.get("connected")),
        "url": (s.get("url") if live else None) or (f"https://{hostname(cfg)}" if hostname(cfg) else None),
        "error": s.get("error") if mode != "off" else None,
        "log": s.get("log") or [],
        "origin": origin(cfg),
        "process": s.get("owner") if live else None,
    }


# ---------- cloudflared ----------
def binary(cfg):
    """cloudflared on PATH, or the copy Lens fetched into data_dir/bin; None when there's neither."""
    found = shutil.which("cloudflared")
    if found:
        return found
    p = pathlib.Path(cfg["data_dir"]) / "bin" / "cloudflared"
    return str(p) if p.exists() else None


def fetch_binary(cfg, say=None):
    """Download cloudflared from Cloudflare's GitHub releases into data_dir/bin. Returns its path."""
    system = platform.system().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm"}.get(platform.machine().lower())
    if system not in ("linux", "darwin") or not arch:
        raise TunnelError(f"install cloudflared yourself on this system ({system} {platform.machine()}); Lens finds it on PATH")
    name = f"cloudflared-{system}-{arch}" + (".tgz" if system == "darwin" else "")
    dest = pathlib.Path(cfg["data_dir"]) / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    tmp = dest / (name + ".part")
    if say:
        say(f"downloading cloudflared ({name})")
    try:
        with httpx.stream("GET", RELEASES + name, follow_redirects=True, timeout=120) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise TunnelError(f"couldn't download cloudflared: {e}") from None
    out = dest / "cloudflared"
    if name.endswith(".tgz"):
        with tarfile.open(tmp) as t:
            member = next((m for m in t.getmembers() if m.name.endswith("cloudflared") and m.isfile()), None)
            if not member:
                raise TunnelError("the cloudflared download has no program in it")
            with t.extractfile(member) as src, open(out, "wb") as f:
                shutil.copyfileobj(src, f)
        tmp.unlink()
    else:
        tmp.replace(out)
    out.chmod(0o755)
    return str(out)


# ---------- managed tunnels (Cloudflare API) ----------
def _api(token, method, path, **kw):
    try:
        r = httpx.request(method, API + path, headers={"Authorization": f"Bearer {token}"}, timeout=30, **kw)
        body = r.json()
    except (httpx.HTTPError, ValueError):
        raise TunnelError("can't reach Cloudflare's API; try again") from None
    if not body.get("success"):
        errs = "; ".join(e.get("message", "") for e in body.get("errors") or []) or f"HTTP {r.status_code}"
        raise TunnelError(f"Cloudflare said: {errs}")
    return body.get("result")


def _zone(token, host):
    """The Cloudflare zone (domain) the hostname is in: the longest one the token can see."""
    parts = host.split(".")
    for i in range(len(parts) - 1):
        name = ".".join(parts[i:])
        found = _api(token, "GET", "/zones", params={"name": name})
        if found:
            return found[0]
    raise TunnelError(f"none of your Cloudflare domains holds {host} (or the API token can't read zones)")


def provision(cfg, host, origin_url, saved=None):
    """Make (or reuse) a tunnel for `host`, route it to the web app and add the DNS record. Returns
    {tunnel_id, account, zone, host, token}."""
    api_token = conf(cfg).get("api_token")
    zone = _zone(api_token, host)
    account = (zone.get("account") or {}).get("id")
    if not account:
        raise TunnelError("the API token can't see which Cloudflare account the domain belongs to")
    name = "lens-" + host.replace(".", "-")
    tid = saved.get("tunnel_id") if saved and saved.get("account") == account else None
    if not tid:
        found = _api(api_token, "GET", f"/accounts/{account}/cfd_tunnel", params={"name": name, "is_deleted": "false"})
        tid = found[0]["id"] if found else None
    if not tid:
        tid = _api(api_token, "POST", f"/accounts/{account}/cfd_tunnel", json={"name": name, "config_src": "cloudflare"})["id"]
    _api(
        api_token,
        "PUT",
        f"/accounts/{account}/cfd_tunnel/{tid}/configurations",
        json={"config": {"ingress": [{"hostname": host, "service": origin_url}, {"service": "http_status:404"}]}},
    )
    target = f"{tid}.cfargotunnel.com"
    records = _api(api_token, "GET", f"/zones/{zone['id']}/dns_records", params={"name": host})
    record = {"type": "CNAME", "name": host, "content": target, "proxied": True, "comment": "Lens tunnel"}
    if records:
        r = records[0]
        if r.get("type") != "CNAME" or not str(r.get("content", "")).endswith(".cfargotunnel.com"):
            raise TunnelError(f"{host} already has a {r.get('type')} record; remove it in Cloudflare or pick another hostname")
        if r.get("content") != target:
            _api(api_token, "PUT", f"/zones/{zone['id']}/dns_records/{r['id']}", json=record)
    else:
        _api(api_token, "POST", f"/zones/{zone['id']}/dns_records", json=record)
    token = _api(api_token, "GET", f"/accounts/{account}/cfd_tunnel/{tid}/token")
    return {"tunnel_id": tid, "account": account, "zone": zone["id"], "host": host, "token": token}


# ---------- the runner ----------
def _problem(line):
    """What a cloudflared log line says is wrong, for Settings › Remote access, or None."""
    m = re.search(r"\|\s*ERROR:\s*(.+?)\s*\|?\s*$", line)  # its boxed advice, like "Allow outbound QUIC traffic ..."
    if m:
        return m.group(1)
    if " ERR " in line:
        msg = line.split(" ERR ", 1)[1]
        m = re.search(r'error="([^"]+)"', msg)
        return (msg.split(" error=")[0] + (f": {m.group(1)}" if m else "")).strip()[:300]
    return None


class Runner:
    """Holds the lease and runs cloudflared while Settings › Remote access says to."""

    def __init__(self, db, cfg_fn, name, log=None):
        self.db, self.cfg_fn, self.name, self.log = db, cfg_fn, name, log or (lambda *a: None)
        self.proc: subprocess.Popen | None = None
        self.sig = None
        self.lines: list[str] = []
        self.url = None
        self.connected = False
        self.error = None
        self.fails = 0
        self.next_try = 0.0
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

    def _save(self, **extra):
        with self.lock:
            d = {
                "running": self.proc is not None and self.proc.poll() is None,
                "connected": self.connected,
                "url": self.url,
                "error": self.error,
                "log": json.dumps(self.lines[-LOG_LINES:]),
                **extra,
            }
        self.db.q("UPDATE $r MERGE $d WHERE owner = $me", r=ROW, d=d, me=self.name)

    def _signature(self, cfg):
        t = conf(cfg)
        return json.dumps([t.get("mode"), t.get("token"), hostname(cfg), t.get("api_token"), origin(cfg)], sort_keys=True)

    def stop_process(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.connected = False

    def _read(self, proc, quick):
        for raw in proc.stdout:
            line = raw.rstrip()
            if not line:
                continue
            with self.lock:
                before = (self.url, self.connected, self.error)
                self.lines = (self.lines + [line[:300]])[-LOG_LINES:]
                m = QUICK_URL.search(line) if quick else None
                if m:
                    self.url = m.group(0)
                if "Registered tunnel connection" in line:
                    self.connected, self.error, self.fails = True, None, 0
                elif not self.connected and (hint := _problem(line)):
                    self.error = hint  # still trying; says why it isn't connected yet
                changed = before != (self.url, self.connected, self.error)
            if changed:
                self._save()
        code = proc.wait()
        if self.proc is proc:
            with self.lock:
                self.connected = False
                self.error = f"cloudflared stopped (exit {code}); {self.lines[-1] if self.lines else 'no output'}"
            self.fails += 1
            self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]
            self._save()

    def _command(self, cfg, exe):
        t, base = conf(cfg), [exe, "tunnel", "--no-autoupdate"]
        env = {k: v for k, v in os.environ.items() if not k.startswith("TUNNEL_")}
        mode = t.get("mode")
        if mode == "quick":
            return base + ["--url", origin(cfg)], env, None
        if mode == "token":
            env["TUNNEL_TOKEN"] = t["token"]  # not on the command line, where `ps` shows it
            return base + ["run"], env, f"https://{hostname(cfg)}"
        saved = self._managed(cfg)
        env["TUNNEL_TOKEN"] = saved["token"]
        return base + ["run"], env, f"https://{saved['host']}"

    def _managed(self, cfg):
        """The managed tunnel for the hostname, made with the API the first time (kept, with its token sealed)."""
        host, s = hostname(cfg), state(self.db)
        saved = s.get("managed") or {}
        if saved.get("host") == host and saved.get("origin") == origin(cfg) and saved.get("token"):
            try:
                return {**saved, "token": settings.unseal(cfg, saved["token"], "tunnel.token")}
            except Exception:  # noqa: BLE001 - the key changed: make it again
                pass
        made = provision(cfg, host, origin(cfg), saved)
        keep = {**made, "origin": origin(cfg), "token": settings.seal(cfg, made["token"], "tunnel.token")}
        self.db.q("UPDATE $r SET managed = $m WHERE owner = $me", r=ROW, m=keep, me=self.name)
        return made

    def tick(self):
        cfg = self.cfg_fn()
        mode = conf(cfg).get("mode") or "off"
        if mode == "off":
            if self.proc:
                self.stop_process()
                self.url, self.error = None, None
                self._save()
            return
        if not self._lease():
            if self.proc:  # someone else runs it now
                self.stop_process()
            return
        sig = self._signature(cfg)
        if sig != self.sig:
            self.stop_process()
            self.sig, self.fails, self.next_try, self.url, self.error, self.lines = sig, 0, 0.0, None, None, []
        if self.proc and self.proc.poll() is None:
            self._save()
            return
        if time.monotonic() < self.next_try:
            self._save()
            return
        try:
            exe = binary(cfg) or fetch_binary(cfg, say=lambda m: self.lines.append(m))
            cmd, env, url = self._command(cfg, exe)
        except (TunnelError, OSError, KeyError) as e:
            self.error = str(e)
            self.fails += 1
            self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]
            self._save()
            return
        self.url = url
        self.proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self.proc, mode == "quick"), daemon=True, name="cloudflared-log").start()
        self.log("tunnel: started cloudflared (%s)", mode)
        self._save(mode=mode)

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
        self.stop_process()
        self.db.q("UPDATE $r SET owner = NONE, running = false, connected = false WHERE owner = $me", r=ROW, me=self.name)


def start(db, cfg_fn, stop, name, log=None):
    r = Runner(db, cfg_fn, name, log)
    threading.Thread(target=r.loop, args=(stop,), daemon=True, name="tunnel").start()
    return r
