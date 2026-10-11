"""Turning the monitoring profile (Prometheus, node-exporter, Grafana) on and off from the web app (docs/prometheus.md).

The containers can't start other containers, so the machine's upgrade agent does it, as it installs releases
(updates.py, packaging/host/lens-upgrade.sh). Turning monitoring on makes sure /metrics is on with a scrape token
(generated when none is set), writes that token where the monitoring Prometheus reads it (`<control>/prometheus-token`),
then drops a request in the agent's inbox: the agent adds `monitoring` to COMPOSE_PROFILES in the install's .env and
starts the three containers; off removes it and stops them. How it went is in `<control>/services-status`.

The request goes in the inbox under the same name as an upgrade (`inbox/upgrade`, with `action=services`), because that
file is what every agent installed so far watches for; the agent never installs anything for it.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets

from . import settings, store, updates

ROW = store.R("app_service", "monitoring")
ACTIVE = ("queued", "running")
TOKEN_FILE = "prometheus-token"
STATUS_FILE = "services-status"
GRAFANA_PORT = 3001


class MonitoringError(RuntimeError):
    """Monitoring can't be turned on or off; the message says why, for the admin."""


def _row(db):
    return db.one("SELECT * OMIT id FROM $r", r=ROW) or {}


def _put(db, run):
    db.q("UPSERT $r CONTENT $d", r=ROW, d=run)


def manual():
    """The line to run on the machine when there is no agent."""
    where = os.environ.get("LENS_HOST_DIR") or ""
    cd = f"cd {where} && " if where and updates.REF.match(where.lstrip("/~")) else ""
    return f"{cd}docker compose -f docker-compose.prod.yml --profile monitoring up -d"


def view(db):
    """Whether monitoring is on (as last asked), and how the agent's last run went."""
    run = _row(db)
    out = {
        "on": bool(run.get("on")),
        "state": run.get("state"),
        "error": run.get("error"),
        "asked_at": run.get("asked_at"),
        "finished_at": None,
        "grafana_port": GRAFANA_PORT,
        "agent": updates.agent(),
        "manual": manual(),
    }
    d = updates.control_dir()
    if d and run:
        s = updates._kv(d / STATUS_FILE)
        if s.get("id") == run.get("run"):
            out["state"] = s.get("state") or out["state"]
            out["finished_at"] = s.get("finished_at")
            if s.get("grafana_port", "").isdigit():
                out["grafana_port"] = int(s["grafana_port"])
            if s.get("state") == "failed":
                out["error"] = s.get("error") or f"the machine couldn't change it (exit {s.get('code', '?')})"
        if out["state"] == "queued" and (d / "inbox" / "upgrade").exists():
            try:
                waited = (dt.datetime.now(dt.timezone.utc) - dt.datetime.fromisoformat(str(run.get("asked_at")))).total_seconds()
            except ValueError:
                waited = 0
            if waited > updates.PICKUP_SECONDS:
                out["error"] = "the machine hasn't picked it up yet: is its upgrade agent running?"
    return out


def sync_token(cfg, create=False):
    """Keep the token the monitoring Prometheus scrapes with the same as /metrics's, once monitoring was turned on."""
    d = updates.control_dir()
    token = ((cfg or {}).get("telemetry") or {}).get("prometheus_token")
    if not d or not d.is_dir():
        return
    f = d / TOKEN_FILE
    if not create and not f.exists():
        return
    if not token:
        f.unlink(missing_ok=True)
        return
    part = d / f".{TOKEN_FILE}.{secrets.token_hex(4)}"
    part.write_text(str(token), encoding="utf-8")
    part.chmod(0o644)  # Prometheus runs as its own user; the token reads metrics only
    part.replace(f)


def turn(db, base, cfg, on, by=None):
    """Ask the machine to start (on) or stop the monitoring containers; returns view()."""
    if not updates.agent():
        raise MonitoringError("this machine has no upgrade agent to start containers: run the line shown on it instead")
    d = updates.control_dir()
    if (d / "inbox" / "upgrade").exists():
        raise MonitoringError("the machine is busy with another request (an upgrade?): try again in a minute")
    run, now_ = _row(db), view(db)
    if now_["state"] in ACTIVE and not now_["error"]:
        raise MonitoringError("the machine is still changing it: wait for that to finish")
    t = cfg.get("telemetry") or {}
    locked = settings.locked("telemetry")
    changes, ours = {}, bool(run.get("turned_on_metrics"))
    if on:
        if not t.get("prometheus") and "prometheus" not in locked:
            changes["prometheus"] = True
            ours = True
        if not t.get("prometheus_token") and "prometheus_token" not in locked:
            changes["prometheus_token"] = secrets.token_hex(32)
    elif ours and t.get("prometheus") and "prometheus" not in locked:
        changes["prometheus"] = False  # monitoring turned /metrics on: off again with it
        ours = False
    if changes:
        settings.save(db, base, "telemetry", changes, by)
    if on:
        token = changes.get("prometheus_token") or t.get("prometheus_token")
        sync_token({"telemetry": {"prometheus_token": token}}, create=True)
    rid = secrets.token_hex(8)
    now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    part = d / "inbox" / f".upgrade.{rid}"
    part.write_text(f"id={rid}\naction=services\nmonitoring={'on' if on else 'off'}\nby={by or ''}\nat={now}\n", encoding="utf-8")
    part.replace(d / "inbox" / "upgrade")
    _put(db, {"run": rid, "on": bool(on), "by": by, "asked_at": now, "state": "queued", "error": None, "turned_on_metrics": ours})
    return view(db)
