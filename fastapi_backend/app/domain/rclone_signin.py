"""Signing a storage source in through rclone on this server, with rclone's own app for the service: Google Drive (as a
Google account), Dropbox and OneDrive. Nothing to install or run on another computer, and no app of your own.

Lens runs `rclone authorize <type>`, which waits on 127.0.0.1:53682 for the service to send the browser back, and hands
the person the service's sign-in page. After they sign in, the service sends their browser to
http://127.0.0.1:53682/?state=…&code=…. Where the browser runs on the same machine as Lens (a native install) that is
rclone itself, which takes it from there. Anywhere else the page doesn't load, and the person pastes its address into
Lens, which passes it on to rclone here: only its state, code and error, only to rclone's own address, and only when the
state is this sign-in's.

rclone swaps the code for a token with its own app, prints it and ends. Lens keeps the token sealed as the source's, as
if it had been pasted from `rclone authorize` on another computer, and rclone refreshes it the same way. The service's
page asks the person to allow rclone, not Lens.

rclone listens on one fixed address, so a server runs one of these sign-ins at a time: starting one ends the one
before. Each lasts ten minutes; rclone is stopped then, or once it's finished or cancelled.
"""

from __future__ import annotations

import base64
import contextlib
import datetime as dt
import json
import os
import re
import secrets
import shutil
import signal
import subprocess
import tempfile
import threading
import time
import urllib.parse

import httpx

from . import auth, sources, store

R = store.R
MINUTES = 10
HOSTS = ("127.0.0.1", "localhost")  # where the service sends the browser back (OneDrive's app says localhost)
LINK = re.compile(r"http://127\.0\.0\.1:(\d+)/auth\?state=([\w-]+)")
EXCHANGE = 45  # seconds for rclone to swap a code for a token at the service
GRAPH_DRIVE = "https://graph.microsoft.com/v1.0/me/drive"  # OneDrive: which drive is the account's own
_running: dict[str, subprocess.Popen] = {}  # the rclone each sign-in started in this process runs, by sign-in id


def _later(minutes):
    return (dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _blob(typ, params):
    """The options rclone's sign-in asks the service for (Drive's scope), as rclone reads them on its command line."""
    opts = {"scope": params["scope"]} if typ == "drive" and params.get("scope") else {}
    if not opts:
        return []
    return [base64.b64encode(json.dumps(opts, separators=(",", ":")).encode()).decode().rstrip("=")]


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _alive(row):
    proc = _running.get(row["id"])
    if proc is not None:
        return proc.poll() is None
    try:
        os.kill(row["pid"], 0)
    except (ProcessLookupError, PermissionError):
        return False
    return _is_rclone(row["pid"])


def _is_rclone(pid):
    """Whether `pid` is still an rclone sign-in (not a process that got its number since), where the system says."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            return b"authorize" in f.read()
    except OSError:
        return os.name != "posix" or not os.path.isdir("/proc")  # no /proc (macOS): trust the number


def _stop(row):
    """End a sign-in's rclone and remove what it wrote."""
    proc = _running.pop(row["id"], None)
    if proc is not None:
        if proc.poll() is None:
            proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(5)
    elif row.get("pid") and _alive(row):
        with contextlib.suppress(OSError):
            os.kill(row["pid"], signal.SIGTERM)
    if row.get("dir", "").startswith(tempfile.gettempdir()):
        shutil.rmtree(row["dir"], ignore_errors=True)


def _rows(db):
    return db.rows("SELECT record::id(id) AS id, * FROM rclone_signin")


def cancel(db, fid):
    """End sign-in `fid` (rclone stops waiting). KeyError when there's no such sign-in."""
    rows = db.rows("DELETE $r RETURN BEFORE", r=R("rclone_signin", fid))
    if not rows:
        raise KeyError(fid)
    _stop({**rows[0], "id": fid})


def cancel_all(db):
    for row in _rows(db):
        db.q("DELETE $r", r=R("rclone_signin", row["id"]))
        _stop(row)


def _expire(fid):
    proc = _running.pop(fid, None)
    if proc is not None and proc.poll() is None:
        proc.kill()
        with contextlib.suppress(subprocess.TimeoutExpired):
            proc.wait(5)


def start(db, cfg, typ=None, params=None, name=None, sid=None, by=None):
    """Start signing in through rclone, for a new source of type `typ` (with `params`, named `name`) or for signing
    source `sid` in again: returns (this sign-in's id, the service's sign-in page, the port rclone waits on). Ends any
    other one running. ValueError when the type doesn't sign in in the browser; KeyError for no such source;
    RuntimeError when rclone can't run here."""
    if sid is not None:
        src = sources.get(db, sid)
        typ, params = src["type"], {**(src.get("params") or {}), **(params or {})}
    spec = sources.BACKENDS.get(typ or "")
    if not spec or not spec.get("oauth"):
        raise ValueError(f"{spec['label'] if spec else typ} doesn't sign in in the browser")
    sources._check_params(typ, params, None)
    params = sources.tidy(typ, {**spec["fields"], **(params or {})})
    binary = sources._bin(cfg)
    cancel_all(db)  # rclone listens on one address
    d = tempfile.mkdtemp(prefix="lens-signin-")
    out, err = os.path.join(d, "out"), os.path.join(d, "err")
    with open(out, "wb") as o, open(err, "wb") as e:
        proc = subprocess.Popen(
            # a config of its own that doesn't exist: nothing from the server's rclone setup comes into it
            [binary, "--config", os.path.join(d, "rclone.conf"), "authorize", typ, *_blob(typ, params), "--auth-no-open-browser"],
            stdin=subprocess.DEVNULL,
            stdout=o,
            stderr=e,
            start_new_session=True,  # not stopped with a request, and not left behind as part of one
        )
    fid = secrets.token_urlsafe(12)
    _running[fid] = proc
    timer = threading.Timer(MINUTES * 60, _expire, (fid,))
    timer.daemon = True
    timer.start()
    deadline = time.monotonic() + 15
    while not (m := LINK.search(_read(err))):
        if proc.poll() is not None or time.monotonic() > deadline:
            text = _read(err)
            _stop({"id": fid, "dir": d})
            if "address already in use" in text:
                raise RuntimeError("something else on this server is using rclone's sign-in address (port 53682); try again later")
            raise RuntimeError(f"rclone couldn't start signing in: {sources._clean_err(text) or 'no answer'}")
        time.sleep(0.1)
    port, state = int(m[1]), m[2]
    try:  # rclone's own page sends the browser on to the service's, with rclone's app and this sign-in's state
        r = httpx.get(f"http://127.0.0.1:{port}/auth", params={"state": state}, follow_redirects=False, timeout=10, trust_env=False)
        url = r.headers.get("location") or ""
    except httpx.HTTPError:
        url = ""
    if not url.startswith("https://"):
        _stop({"id": fid, "dir": d})
        raise RuntimeError("rclone didn't give a sign-in page")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("rclone_signin", fid),
        d=store.clean(
            {
                "type": typ,
                "params": params,
                "name": (name or "").strip()[:80] or None,
                "source": sid,
                "by": by,
                "state": auth.sha(state),
                "port": port,
                "pid": proc.pid,
                "dir": d,
                "expires_at": _later(MINUTES),
            }
        ),
    )
    return fid, url, port


def _token(text):
    """The token rclone printed between its arrows: the token itself, or (when it was given options) its options with
    the token in them, base64-encoded. None when it hasn't printed one."""
    m = re.search(r"--->\s*\n(.*?)\n\s*<---End paste", text, re.S)
    if not m:
        return None
    body = "".join(m[1].split())
    try:
        tok = body if body.startswith("{") else json.loads(base64.b64decode(body + "=" * (-len(body) % 4)))["token"]
        if json.loads(tok).get("access_token"):
            return tok
    except (ValueError, KeyError, TypeError, AttributeError):
        pass
    raise ValueError("rclone printed something that isn't a token; start again")


def _why(text):
    """Why rclone stopped, from what it printed: its last fatal error (with the service's description, if any)."""
    tail = text.rsplit("Fatal error:", 1)[1] if "Fatal error:" in text else sources._clean_err(text)
    if "access_denied" in tail:
        return "signing in didn't finish: you cancelled"
    m = re.search(r"^Description:\s*(\S.*)$", tail, re.M)
    msg = m[1] if m else next((l.strip() for l in tail.splitlines() if l.strip()), "")
    return f"signing in didn't finish: {msg.replace('config failed to refresh token: ', '')[:300] or 'rclone stopped'}"


class WrongAddress(ValueError):
    """An address that isn't this sign-in's to finish: the sign-in goes on waiting for the right one."""


def _forward(row, address):
    """Hand the address the browser ended on to rclone, which is waiting for it here."""
    u = urllib.parse.urlsplit(address.strip())
    q = dict(urllib.parse.parse_qsl(u.query))
    try:
        port = u.port
    except ValueError:
        port = None
    if (
        u.scheme != "http"
        or u.hostname not in HOSTS
        or port != row["port"]
        or u.path not in ("", "/")
        or not (q.get("code") or q.get("error"))
    ):
        raise WrongAddress(f"paste the whole address the browser ended on: it starts with http://127.0.0.1:{row['port']}/?")
    if auth.sha(q.get("state", "")) != row["state"]:
        raise WrongAddress("that address is from another sign-in; paste the one this sign-in ended on")
    keep = {k: q[k] for k in ("state", "code", "scope", "error", "error_description") if k in q}
    with contextlib.suppress(httpx.HTTPError):  # rclone stops listening once it has the code, at times before it answers
        httpx.get(f"http://127.0.0.1:{row['port']}/", params=keep, timeout=EXCHANGE, trust_env=False)


def _wait(row, seconds):
    """The token rclone printed, waiting up to `seconds` for it; None when it's still waiting for the browser."""
    out, err = os.path.join(row["dir"], "out"), os.path.join(row["dir"], "err")
    deadline = time.monotonic() + seconds
    while (tok := _token(_read(out))) is None:
        if not _alive(row):
            time.sleep(0.2)  # its last words
            if (tok := _token(_read(out))) is not None:
                break
            raise ValueError(_why(_read(err)))
        if time.monotonic() >= deadline:
            if seconds:
                raise ValueError("the service didn't answer rclone in time; start again")
            return None
        time.sleep(0.2)
    return tok


def _row(db, fid):
    row = db.one("SELECT record::id(id) AS id, * FROM $r", r=R("rclone_signin", fid))
    if not row:
        raise KeyError(fid)
    return row


def _made(db, fid):
    """The connection another request is making from this sign-in's token, once it's made."""
    deadline = time.monotonic() + EXCHANGE
    while (sid := _row(db, fid).get("connected")) is None:
        if time.monotonic() > deadline:
            raise ValueError("connecting took too long; look for it in the list, or start again")
        time.sleep(0.2)
    return sid


def finish(db, cfg, fid, address=None):
    """Finish sign-in `fid`: with `address` (the one the browser ended on), hand it to rclone and wait for the token;
    without, only look whether rclone already has one (the browser reached it itself). Connects the source and returns
    its id (again, if asked again), or None while it's still waiting. KeyError for no such sign-in. ValueError says why
    it didn't work, and the sign-in is over then; WrongAddress (a ValueError) leaves it waiting for the right address."""
    row = _row(db, fid)
    if row.get("connected") is not None:
        return row["connected"]
    try:
        if (row.get("expires_at") or "") < store.now():
            raise ValueError("that sign-in ran out after ten minutes; start again")
        if address and _token(_read(os.path.join(row["dir"], "out"))) is None:
            _forward(row, address)
        tok = _wait(row, EXCHANGE if address else 0)
    except WrongAddress:
        raise
    except ValueError:
        with contextlib.suppress(KeyError):
            cancel(db, fid)
        raise
    if tok is None:
        return None
    # one request makes the connection (the page asks while a pasted address is on its way); any other gets its id
    if not db.rows("UPDATE $r SET taken = true WHERE taken IS NONE RETURN AFTER", r=R("rclone_signin", fid)):
        return _made(db, fid)
    try:
        sid = _connect(db, cfg, row, tok)
    except ValueError:
        with contextlib.suppress(KeyError):
            cancel(db, fid)
        raise
    db.q("UPDATE $r SET connected = $s", r=R("rclone_signin", fid), s=sid)
    _stop(row)  # rclone is done; the sign-in stays, saying what it made, until the next one starts
    return sid


def _my_drive(token):
    """The signed-in account's own OneDrive ({drive_id, drive_type}), which rclone needs named; {} when Microsoft
    won't say (rclone then asks for it on the connection's test)."""
    try:
        r = httpx.get(GRAPH_DRIVE, headers={"Authorization": f"Bearer {json.loads(token)['access_token']}"}, timeout=15)
        d = r.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return {}
    if r.status_code != 200 or not isinstance(d, dict) or not d.get("id"):
        return {}
    return {"drive_id": str(d["id"]), "drive_type": str(d.get("driveType") or "personal")}


def _connect(db, cfg, row, token):
    typ = row["type"]
    spec = sources.BACKENDS[typ]
    if typ == "onedrive" and not (row.get("params") or {}).get("drive_id"):
        row = {**row, "params": {**(row.get("params") or {}), **_my_drive(token)}}
    if row.get("source") is not None:
        sid = row["source"]
        others = {k: None for k in spec["secrets"] if k != "token"}  # signed in: no service account
        try:
            sources.update(db, cfg, sid, params=row.get("params"), secret_values={"token": token, **others})
        except KeyError:
            raise ValueError("that connection was deleted while you were signing in") from None
    else:
        sid = sources.create(db, cfg, row.get("name") or spec["label"], typ, row.get("params"), {"token": token}, row.get("by"))
    sources.test(db, cfg, sid)
    return sid
