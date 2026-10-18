"""Turning the monitoring profile (Prometheus, node-exporter, Grafana) on and off from the web app (docs/prometheus.md).

The containers can't start other containers, so the machine's upgrade agent does it, as it installs releases
(updates.py, packaging/host/lens-upgrade.sh). Turning monitoring on makes sure /metrics is on with a scrape token
(generated when none is set), writes that token where the monitoring Prometheus reads it (`<control>/prometheus-token`),
then asks the agent to turn the monitoring profile on (services.py): it adds `monitoring` to COMPOSE_PROFILES in the
install's .env and starts the three containers; off removes it and stops them.
"""

from __future__ import annotations

import secrets

from . import services, settings, store, updates

ROW = store.R("app_service", "monitoring")
TOKEN_FILE = "prometheus-token"
GRAFANA_PORT = 3001
# Monitoring can't be turned on or off; the message says why, for the admin
MonitoringError = services.ServiceError


def _row(db):
    return db.one("SELECT * OMIT id FROM $r", r=ROW) or {}


def manual():
    """The line to run on the machine when there is no agent."""
    return services.manual("monitoring")


def view(db):
    """Whether monitoring is on (as last asked), and how the agent's last run went."""
    v = services.view(db, "monitoring", probe=False)
    port = services.reported(db, "monitoring").get("grafana_port", "")
    keys = ("on", "state", "error", "asked_at", "finished_at", "agent", "manual")
    return {k: v[k] for k in keys} | {"grafana_port": int(port) if port.isdigit() else GRAFANA_PORT}


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
    services.ready(db, "monitoring")
    run = _row(db)
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
    services.ask(db, "monitoring", on, by, turned_on_metrics=ours)
    return view(db)
