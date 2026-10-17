"""Lens's optional containers, turned on and off from the web app: the MCP runner, the browser and monitoring
(docs/assistant.md, docs/prometheus.md).

The containers can't start other containers, so the machine's upgrade agent does it, as it installs releases
(updates.py, packaging/host/lens-upgrade.sh): a request with action=services, profile=<name> and turn=on|off in its
inbox makes it add the profile to COMPOSE_PROFILES in the install's .env, or take it out, and start or stop that
profile's containers. It says how it went in `<control>/services-status`, which is about the last request only,
whichever profile it was for, so what it said about a profile's request is kept with that profile once it's finished.
The agent does one request at a time, so a profile isn't turned while another is still changing.

Monitoring adds its own steps around this (monitoring.py: /metrics and its token). A profile added here needs its
containers named in the agent's services_of too, or the agent refuses it.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
import socket
import urllib.parse

from . import store, updates

# profile: (what it's called, what it is, the address it answers on while it runs, or None)
PROFILES = {
    "mcp": (
        "MCP runner",
        "Runs the MCP servers that are programs (npx, uvx) in a container of their own, with none of Lens's data or keys",
        "LENS_MCP_RUNNER_URL",
    ),
    "browser": (
        "Browser",
        "A headless Chromium the assistant drives over MCP (Playwright MCP), in a container that reaches public web "
        "addresses only, through a proxy that checks each one",
        "LENS_BROWSER_URL",
    ),
    "monitoring": (
        "Monitoring",
        "Prometheus, node-exporter and Grafana: charts of Lens and this machine over time",
        None,
    ),
}
ACTIVE = ("queued", "running")
FINAL = ("done", "failed")
STATUS_FILE = "services-status"


class ServiceError(RuntimeError):
    """A profile can't be turned on or off; the message says why, for the admin."""


def _check(profile):
    if profile not in PROFILES:
        raise ServiceError(f"Lens has no optional containers called {profile!r}: {', '.join(PROFILES)}")


def _row(db, profile):
    return db.one("SELECT * OMIT id FROM $r", r=store.R("app_service", profile)) or {}


def _put(db, profile, run):
    db.q("UPSERT $r CONTENT $d", r=store.R("app_service", profile), d=run)


def manual(profile):
    """The line to run on the machine when there is no agent."""
    where = os.environ.get("LENS_HOST_DIR") or ""
    cd = f"cd {where} && " if where and updates.REF.match(where.lstrip("/~")) else ""
    return f"{cd}docker compose -f docker-compose.prod.yml --profile {profile} up -d"


def running(profile):
    """Whether the profile's container answers now (it takes connections where Lens reaches it), or None when Lens
    can't tell (monitoring, or no address set)."""
    env = PROFILES[profile][2]
    u = urllib.parse.urlsplit(os.environ.get(env, "") if env else "")
    if not u.hostname:
        return None
    try:
        with socket.create_connection((u.hostname, u.port or 80), timeout=1.5):
            return True
    except OSError:
        return False


def reported(db, profile, run=None):
    """What the agent said about the profile's last request: its status file while that's about the request, else
    what was kept once it finished ({} before the agent said anything)."""
    run = _row(db, profile) if run is None else run
    said = dict(run.get("said") or {})
    d = updates.control_dir()
    if d and run.get("run"):
        s = updates._kv(d / STATUS_FILE)
        if s.get("id") == run["run"]:
            said = s
            if s.get("state") in FINAL and run.get("said") != s:
                _put(db, profile, {**run, "said": s})
    return said


def view(db, profile, probe=True):
    """Whether the profile is on (as last asked, or, never asked here, whether it runs), and how the agent's last
    change went."""
    _check(profile)
    run = _row(db, profile)
    label, about, _ = PROFILES[profile]
    up = running(profile) if probe else None
    out = {
        "profile": profile,
        "label": label,
        "description": about,
        "on": bool(run.get("on")) if run else bool(up),
        "running": up,
        "state": run.get("state"),
        "error": run.get("error"),
        "asked_at": run.get("asked_at"),
        "finished_at": None,
        "agent": updates.agent(),
        "manual": manual(profile),
    }
    s = reported(db, profile, run)
    if s:
        out["state"] = s.get("state") or out["state"]
        out["finished_at"] = s.get("finished_at")
        if s.get("state") == "failed":
            out["error"] = s.get("error") or f"the machine couldn't change it (exit {s.get('code', '?')})"
    d = updates.control_dir()
    if d and out["state"] == "queued" and (d / "inbox" / "upgrade").exists():
        try:
            asked = dt.datetime.fromisoformat(str(run.get("asked_at")))
            waited = (dt.datetime.now(dt.timezone.utc) - asked).total_seconds()
        except ValueError:
            waited = 0
        if waited > updates.PICKUP_SECONDS:
            out["error"] = "the machine hasn't picked it up yet: is its upgrade agent running?"
    return out


def views(db):
    """Every optional profile, as view() says it."""
    return [view(db, p) for p in PROFILES]


def ready(db, profile):
    """ServiceError unless the machine can take a request for the profile now."""
    _check(profile)
    if not updates.agent():
        raise ServiceError("this machine has no upgrade agent to start containers: run the line shown on it instead")
    d = updates.control_dir()
    if (d / "inbox" / "upgrade").exists():
        raise ServiceError("the machine is busy with another request (an upgrade?): try again in a minute")
    for p in PROFILES:  # one at a time: the agent's status file is about the last request only
        v = view(db, p, probe=False)
        if v["state"] in ACTIVE and not v["error"]:
            what = "it" if p == profile else f"the {v['label'].lower()}"
            raise ServiceError(f"the machine is still changing {what}: wait for that to finish")
    return d


def ask(db, profile, on, by=None, **keep):
    """Ask the machine to start (on) or stop the profile's containers; returns view(). `keep` is kept with the
    request (monitoring's own fields)."""
    d = ready(db, profile)
    rid = secrets.token_hex(8)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    part = d / "inbox" / f".upgrade.{rid}"
    turn = "on" if on else "off"
    part.write_text(f"id={rid}\naction=services\nprofile={profile}\nturn={turn}\nby={by or ''}\nat={now}\n", encoding="utf-8")
    part.replace(d / "inbox" / "upgrade")
    _put(db, profile, {"run": rid, "on": bool(on), "by": by, "asked_at": now, "state": "queued", "error": None, **keep})
    return view(db, profile)
