"""Updates (docs/updates.md): telling admins when a new Lens release is out, and installing it from the web app.

**The check.** Once a day (updates.check_hours) one process asks GitHub for the repository's latest release
(updates.repo) and keeps the answer in `app_service:updates`, so every process and the web app's banner read the same
thing. That is the only call it makes: no install id and nothing about this install goes with it. updates.check: false
turns it off.

**The upgrade.** Lens can't replace its own containers (or its microsandbox) from inside them, so the machine does it:
install.sh sets up a small agent there (a systemd path unit on Linux, a launchd agent on a Mac) that watches the
control folder, `<install folder>/.lens-control`, which the containers see as LENS_CONTROL_DIR. Upgrading from the
web app backs the database up first, then drops a request in its inbox; the agent runs install.sh again for that
release (packaging/host/lens-upgrade.sh), the way a person would by hand, and writes how it went next to it
(upgrade-status, upgrade.log), which the web app shows. With no agent (an install that didn't use install.sh, or a
machine where it couldn't set one up) the web app shows the one line to run instead.

updates.auto_install installs a new release by itself as soon as it is seen; off unless an admin turns it on.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import pathlib
import re
import secrets
import threading

import httpx

from . import migrations, store

log = logging.getLogger("lens")
R = store.R
ROW = R("app_service", "updates")
API = "https://api.github.com"  # tests point it at a fake
TAG = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]{0,99}$")
REPO = re.compile(r"^[A-Za-z0-9-]{1,39}/[A-Za-z0-9._-]{1,100}$")
TICK_SECONDS = 600
NOTES_MAX = 20_000
LOG_LINES = 60
ACTIVE = ("backing-up", "queued", "running")
# How long a request may sit in the inbox before the web app says nobody picked it up
PICKUP_SECONDS = 120


class UpdateError(RuntimeError):
    """An upgrade can't start; the message says why, for the admin."""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ts(t=None):
    return (t or _now()).isoformat(timespec="seconds")


def current():
    from . import __version__

    return __version__


def version_key(v):
    """(major, minor, patch) of "1.2.3" or "v1.2.3", else None."""
    m = TAG.match(v if str(v).startswith("v") else f"v{v}")
    return tuple(int(x) for x in m.groups()) if m else None


def newer(tag, than):
    a, b = version_key(tag or ""), version_key(than or "")
    return bool(a and b and a > b)


# ---------- the release check ----------
def _row(db):
    return db.one("SELECT * OMIT id FROM $r", r=ROW) or {}


def _put(db, **fields):
    db.q("UPSERT $r MERGE $d", r=ROW, d=fields)


def check(db, cfg):
    """Ask GitHub for the latest release now and keep the answer; returns what was kept."""
    u = cfg["updates"]
    repo = u.get("repo") or ""
    if not REPO.match(repo):
        raise UpdateError(f"updates.repo is owner/name on GitHub, not {repo!r}")
    got = {"checked_at": _ts(), "error": None}
    try:
        r = httpx.get(
            f"{API}/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json", "User-Agent": f"lens/{current()}"},
            timeout=15,
            follow_redirects=True,
        )
        if r.status_code == 404:  # no release yet
            got["latest"] = None
        else:
            r.raise_for_status()
            j = r.json()
            tag = str(j.get("tag_name") or "")
            if not TAG.match(tag):
                raise ValueError(f"the latest release's tag isn't vX.Y.Z: {tag[:40]!r}")
            got["latest"] = {
                "tag": tag,
                "name": str(j.get("name") or tag)[:200],
                "url": str(j.get("html_url") or f"https://github.com/{repo}/releases/tag/{tag}")[:500],
                "published_at": j.get("published_at"),
                "notes": str(j.get("body") or "")[:NOTES_MAX],
            }
    except (httpx.HTTPError, ValueError) as e:
        got["error"] = f"couldn't reach GitHub: {e}"[:500]
    _put(db, **got)
    return {**_row(db), **got}


def due(db, cfg):
    u = cfg["updates"]
    if not u.get("check"):
        return False
    last = _row(db).get("checked_at")
    if not last:
        return True
    try:
        age = (_now() - dt.datetime.fromisoformat(str(last))).total_seconds()
    except ValueError:
        return True
    return age >= float(u.get("check_hours") or 24) * 3600


# ---------- the machine's agent (packaging/host/lens-upgrade.sh) ----------
def control_dir():
    d = os.environ.get("LENS_CONTROL_DIR")
    return pathlib.Path(d) if d else None


def _kv(path):
    """A key=value file the agent writes."""
    out = {}
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            k, sep, v = line.partition("=")
            if sep and k.strip():
                out[k.strip()] = v.strip()
    except OSError:
        pass
    return out


def agent():
    """What upgrades this machine ("systemd", "launchd"), or None when nothing would pick a request up."""
    d = control_dir()
    if not d:
        return None
    a = _kv(d / "agent").get("kind")
    inbox = d / "inbox"
    return a if a and inbox.is_dir() and os.access(inbox, os.W_OK) else None


def manual(cfg, tag=None):
    """The line to run on the machine when the web app can't upgrade it."""
    repo = cfg["updates"].get("repo") or "adeelahmad/lense"
    where = os.environ.get("LENS_HOST_DIR") or ""
    env = f"LENS_DIR={where} " if where and re.match(r"^[A-Za-z0-9._/~-]+$", where) else ""
    return f"curl -fsSL https://raw.githubusercontent.com/{repo}/main/install.sh | {env}LENS_REF={tag or 'main'} sh"


def _log_tail(d):
    try:
        lines = (d / "upgrade.log").read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return []
    return [ln[:500] for ln in lines[-LOG_LINES:]]


def run_state(db):
    """The last upgrade asked for: what the web app did (back up, hand over), then what the agent says."""
    run = dict(_row(db).get("upgrade") or {})
    if not run:
        return None
    d = control_dir()
    if d:
        s = _kv(d / "upgrade-status")
        if s.get("id") == run.get("id"):
            run["state"] = s.get("state") or run.get("state")
            for k in ("started_at", "finished_at", "version"):
                if s.get(k):
                    run[k] = s[k]
            if s.get("state") == "failed":
                run["error"] = s.get("error") or f"install.sh stopped (exit {s.get('code', '?')}); see the log"
        if run.get("state") in ACTIVE or run.get("id") == s.get("id"):
            run["log"] = _log_tail(d)
        if run.get("state") == "queued" and (d / "inbox" / "upgrade").exists():
            try:
                waited = (_now() - dt.datetime.fromisoformat(str(run.get("queued_at")))).total_seconds()
            except ValueError:
                waited = 0
            if waited > PICKUP_SECONDS:
                run["error"] = "the machine hasn't picked the upgrade up yet: is its upgrade agent running?"
    return run


def view(db, cfg):
    row = _row(db)
    latest = row.get("latest")
    u = cfg["updates"]
    return {
        "current": current(),
        "latest": latest,
        "available": bool(latest and newer(latest.get("tag"), current())),
        "checked_at": row.get("checked_at"),
        "error": row.get("error"),
        "check": bool(u.get("check")),
        "auto_install": bool(u.get("auto_install")),
        "agent": agent(),
        "manual": manual(cfg, (latest or {}).get("tag")),
        "upgrade": run_state(db),
    }


def upgrade(db, cfg, ref=None, by=None, backup=True, wait=False):
    """Back up, then ask the machine's agent to install `ref` (default: the latest release). Returns the run."""
    if not agent():
        raise UpdateError("this machine has no upgrade agent; run the line shown instead")
    if ref is None:
        ref = ((_row(db).get("latest") or {}).get("tag")) or None
        if not ref:
            raise UpdateError("no release is known yet: check for updates first")
    if not REF.match(ref) or ".." in ref:
        raise UpdateError(f"{ref!r} isn't a release tag or branch name")
    if version_key(ref) and newer(current(), ref):
        raise UpdateError(f"{ref} is older than this version ({current()}): restore a backup taken before instead")
    run = run_state(db) or {}
    if run.get("state") in ACTIVE and not run.get("error"):
        raise UpdateError(f"an upgrade to {run.get('ref')} is already under way")
    rid = secrets.token_hex(8)
    run = {"id": rid, "ref": ref, "by": by, "state": "backing-up" if backup else "queued", "asked_at": _ts(), "error": None}
    _put(db, upgrade=run)
    t = threading.Thread(target=_hand_over, args=(db, run, backup), daemon=True, name="upgrade")
    t.start()
    if wait:
        t.join()
    return run_state(db)


def reconfigure(db, cfg, https, by=None, wait=False):
    """Have this machine run install.sh again for the version it runs, with the HTTPS ports opened (https=True) or
    closed (docs/https.md). Lens restarts; nothing else changes. Returns the run."""
    if not agent():
        raise UpdateError("this machine has no upgrade agent; run the line shown instead")
    run = run_state(db) or {}
    if run.get("state") in ACTIVE and not run.get("error"):
        raise UpdateError("an upgrade is already under way; try again when it's done")
    run = {"id": secrets.token_hex(8), "ref": "", "https": bool(https), "by": by, "state": "queued", "asked_at": _ts(), "error": None}
    _put(db, upgrade=run)
    t = threading.Thread(target=_hand_over, args=(db, run, False), daemon=True, name="reconfigure")
    t.start()
    if wait:
        t.join()
    return run_state(db)


def manual_https(cfg, https):
    """The line that opens (or closes) the HTTPS ports by hand, on the machine Lens runs on."""
    line = manual(cfg)
    return f"{line[: -len(' sh')]} LENS_HTTPS={1 if https else 0} sh"


def _hand_over(db, run, backup):
    try:
        if backup:
            dst = migrations.backup(db, "before-upgrade")
            if dst:
                migrations._prune(dst.parent, "before-upgrade")
                run["backup"] = dst.name
        inbox = control_dir() / "inbox"
        part = inbox / f".upgrade.{run['id']}"
        extra = f"https={1 if run['https'] else 0}\n" if run.get("https") is not None else ""
        part.write_text(f"id={run['id']}\nref={run['ref']}\n{extra}by={run.get('by') or ''}\nat={_ts()}\n", encoding="utf-8")
        part.replace(inbox / "upgrade")
        run.update(state="queued", queued_at=_ts())
    except Exception as e:  # noqa: BLE001 - the admin reads why
        log.exception("couldn't start the upgrade")
        run.update(
            state="failed", error=f"{'backing up' if run['state'] == 'backing-up' else 'handing the upgrade over'} failed: {e}"[:500]
        )
    _put(db, upgrade=run)


# ---------- the background check ----------
def _once(db, name):
    """True for the one process (and the one time) that claims `name`."""
    try:
        db.q("CREATE $r CONTENT $d", r=R("app_service", name), d={"at": _ts()})
        return True
    except Exception as e:  # noqa: BLE001
        if "already exists" in str(e):
            return False
        raise


def announce(db, cfg, log_fn=None):
    """Tell people about a newer release, once per release: the namespaces' notification targets that want
    lens.release, and the admins by email (updates.email). True when this call told them."""
    rel = _row(db).get("latest") or {}
    tag = rel.get("tag")
    if not (tag and newer(tag, current()) and _once(db, f"release-notice-{tag}")):
        return False
    if log_fn:
        log_fn(f"Lens {tag} is out (this is {current()})")
    from . import notify

    try:
        notify.release(db, cfg, rel, current())
    except Exception:  # noqa: BLE001 - the email still goes
        log.exception("couldn't queue the release notifications")
    if cfg["updates"].get("email", True):
        admins = db.values("SELECT VALUE email FROM account WHERE admin = true AND disabled != true")
        try:
            import asyncio

            from app.email import send_release_email

            asyncio.run(send_release_email(cfg, [a for a in admins if a], tag, current(), rel.get("url")))
        except Exception:  # noqa: BLE001 - the banner still shows it
            log.exception("couldn't email the admins about %s", tag)
    return True


def tick(db, cfg, log_fn=None):
    """Check when due and tell people about a new release; with auto_install, start the upgrade to it once."""
    if due(db, cfg):
        check(db, cfg)
    announce(db, cfg, log_fn)
    if not cfg["updates"].get("auto_install"):
        return
    tag = ((_row(db).get("latest") or {}).get("tag")) or ""
    if not (newer(tag, current()) and agent()):
        return
    if not _once(db, f"auto-upgrade-{tag}"):  # once per release, across every process
        return
    try:
        upgrade(db, cfg, tag, by="auto_install")
    except UpdateError as e:
        if log_fn:
            log_fn(f"couldn't install {tag} by itself: {e}")


def start(db, cfg_fn, stop, name=None, log=None):
    def loop():
        while not stop.is_set():
            try:
                tick(db, cfg_fn(), log)
            except Exception:  # noqa: BLE001 - try again next time
                logging.getLogger("lens").exception("update check failed")
            stop.wait(TICK_SECONDS)

    threading.Thread(target=loop, daemon=True, name="updates").start()
