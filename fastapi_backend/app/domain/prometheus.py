"""A Prometheus scrape endpoint, /metrics (docs/prometheus.md): what Lens is doing and what it costs, for Grafana.

Off by default (telemetry.prometheus). Turned on, GET /metrics answers only a scraper that sends the token set in
telemetry.prometheus_token (`Authorization: Bearer <token>`), and is served where the web app is, so it follows
LENS_BIND: this machine only unless Lens was opened to the network.

Everything is read when Prometheus scrapes, from the tables Lens already keeps: jobs by status and pipeline, routine
runs, resources by status and imports, the activity ledger (calls, tokens, cost and failures by kind and model over
the last hour and day), budgets, workers and the disk. Like telemetry, it carries no personal content: no titles,
file names, namespace names, prompts or user ids; only statuses, ids, model names, counts and sizes.
"""

from __future__ import annotations

import datetime as dt
import hmac
import logging
import os
import shutil
import threading
import time

from . import __version__, activity, budgets, jobs, library

log = logging.getLogger("lens")
WINDOWS = (("1h", dt.timedelta(hours=1)), ("24h", dt.timedelta(hours=24)))
SIZE_SECONDS = 600  # how long a measured data folder size is reused: walking a big archive isn't free
WORKER_FRESH = 120  # seconds since a worker's last heartbeat for it to count as up

_size = {"at": 0.0, "bytes": None, "path": None, "busy": False}
_size_lock = threading.Lock()


def _section(cfg):
    return (cfg or {}).get("telemetry") or {}


def enabled(cfg):
    return bool(_section(cfg).get("prometheus"))


def allowed(cfg, authorization):
    """Whether an Authorization header carries the scrape token. No token set: nobody may scrape."""
    token = _section(cfg).get("prometheus_token")
    if not token or not authorization:
        return False
    scheme, _, given = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(given.strip().encode(), str(token).encode())


# ---------- the text format ----------
def _esc(v):
    return str(v).replace("\\", "\\\\").replace("\n", "\\n").replace('"', '\\"')


class Out:
    """Metrics in Prometheus' text exposition format, each family's HELP and TYPE once, before its samples."""

    def __init__(self):
        self.families = {}

    def add(self, name, value, labels=None, help="", kind="gauge"):
        fam = self.families.setdefault(name, {"help": help, "kind": kind, "samples": []})
        if value is None:
            return
        fam["samples"].append((labels or {}, float(value)))

    def text(self):
        lines = []
        for name, f in self.families.items():
            lines.append(f"# HELP {name} {f['help']}")
            lines.append(f"# TYPE {name} {f['kind']}")
            for labels, v in f["samples"]:
                lab = ",".join(f'{k}="{_esc(x)}"' for k, x in labels.items())
                num = repr(v) if v != int(v) else str(int(v))
                lines.append(f"{name}{{{lab}}} {num}" if lab else f"{name} {num}")
        return "\n".join(lines) + "\n"


# ---------- what's measured ----------
def _jobs(db, out, now):
    for status, n in sorted(jobs.counts(db).items()):
        out.add("lens_jobs", n, {"status": status}, "Jobs by status (queued is the queue's depth).")
    for r in db.rows("SELECT pipeline, status, count() AS n FROM job WHERE pipeline != NONE GROUP BY pipeline, status"):
        out.add("lens_pipeline_jobs", r["n"], {"pipeline": r.get("pipeline"), "status": r.get("status")}, "Jobs by pipeline and status.")
    out.families.setdefault("lens_pipeline_jobs", {"help": "Jobs by pipeline and status.", "kind": "gauge", "samples": []})


def _routines(db, out, now):
    for r in db.rows("SELECT status, count() AS n FROM routine_run GROUP BY status"):
        out.add("lens_routine_runs", r["n"], {"status": r.get("status") or "?"}, "Routine runs kept, by status.")
    out.families.setdefault("lens_routine_runs", {"help": "Routine runs kept, by status.", "kind": "gauge", "samples": []})


def _resources(db, out, now):
    got = {r.get("status"): r["n"] for r in db.rows("SELECT status, count() AS n FROM recording GROUP BY status")}
    for s in library.STATUSES:
        out.add(
            "lens_resources",
            got.get(s, 0),
            {"status": s},
            "Assets (recordings, documents and more) by status; error is failed imports or processing.",
        )
    for name, span in WINDOWS:
        since = (now - span).isoformat(timespec="seconds")
        n = db.one("SELECT count() AS n FROM recording WHERE created_at >= $s GROUP ALL", s=since) or {}
        out.add("lens_imports", n.get("n") or 0, {"window": name}, "Resources imported in the window.")


def _ledger(db, out, now):
    help_ = {
        "lens_activity_calls": "Calls in the activity ledger over the window, by kind (in, out, run) and model.",
        "lens_activity_failed": "Calls that failed over the window, by kind and model.",
        "lens_activity_tokens": "Tokens over the window, by model and direction (in, out).",
        "lens_activity_cost_usd": "Estimated cost in USD over the window, by model (from the prices in Settings).",
        "lens_activity_unpriced": "Priced calls whose cost is missing (no token counts) over the window: the cost is a floor.",
        "lens_activity_seconds": "Time spent in calls over the window, by kind and model.",
    }
    for name in help_:
        out.families.setdefault(name, {"help": help_[name], "kind": "gauge", "samples": []})
    for window, span in WINDOWS:
        since = (now - span).isoformat(timespec="seconds")
        rows = db.rows(
            "SELECT kind, model, count() AS calls, count(ok = false) AS failed, math::sum(tokens_in ?? 0) AS tokens_in, "
            "math::sum(tokens_out ?? 0) AS tokens_out, math::sum(cost_usd ?? 0) AS cost_usd, math::sum(ms ?? 0) AS ms, "
            f"count({activity._unpriced_sql()}) AS unpriced FROM activity WHERE at >= $s GROUP BY kind, model",
            s=since,
        )
        for r in rows:
            kind, model = r.get("kind") or "?", r.get("model") or ""
            lab = {"window": window, "kind": kind, "model": model}
            out.add("lens_activity_calls", r.get("calls") or 0, lab)
            out.add("lens_activity_failed", r.get("failed") or 0, lab)
            out.add("lens_activity_seconds", (r.get("ms") or 0) / 1000, lab)
            if kind == "run":  # a run row repeats what its calls cost: counted once, on the calls
                continue
            if r.get("tokens_in") or r.get("tokens_out") or model:
                out.add("lens_activity_tokens", r.get("tokens_in") or 0, {**lab, "direction": "in"})
                out.add("lens_activity_tokens", r.get("tokens_out") or 0, {**lab, "direction": "out"})
            out.add("lens_activity_cost_usd", round(float(r.get("cost_usd") or 0), 6), lab)
            out.add("lens_activity_unpriced", r.get("unpriced") or 0, lab)


def _budgets(db, out, now):
    help_ = {
        "lens_budget_limit_usd": "A budget's cap in USD, by resource and period.",
        "lens_budget_limit_tokens": "A budget's cap in tokens, by resource and period.",
        "lens_budget_spent_usd": "What a budgeted resource spent this period, in USD.",
        "lens_budget_spent_tokens": "Tokens a budgeted resource used this period.",
        "lens_budget_share": "The largest share of a budget's caps spent this period (1 is the cap).",
        "lens_budget_over": "1 when a budget is over its cap, else 0.",
    }
    for name in help_:
        out.families.setdefault(name, {"help": help_[name], "kind": "gauge", "samples": []})
    for b in budgets.all_budgets(db):
        st = budgets.status(db, b["resource"], b, now)
        lab = {"resource": b["resource"], "period": b.get("period") or "month"}
        out.add("lens_budget_limit_usd", b.get("usd"), lab)
        out.add("lens_budget_limit_tokens", b.get("tokens"), lab)
        spent = st.get("spent") or {}
        out.add("lens_budget_spent_usd", spent.get("usd"), lab)
        out.add("lens_budget_spent_tokens", spent.get("tokens"), lab)
        out.add("lens_budget_share", st.get("share"), lab)
        out.add("lens_budget_over", 1 if st.get("state") == "over" else 0, lab)


def _workers(db, out, now):
    rows = db.rows("SELECT record::id(id) AS name, heartbeat_at, paused FROM worker")
    up = 0
    for w in rows:
        try:
            beat = dt.datetime.fromisoformat(str(w.get("heartbeat_at")))
            if beat.tzinfo is None:
                beat = beat.replace(tzinfo=dt.timezone.utc)
            fresh = (now - beat).total_seconds() <= WORKER_FRESH
        except ValueError:
            fresh = False
        up += 1 if fresh and not w.get("paused") else 0
    out.add("lens_workers", len(rows), None, "Workers that have checked in.")
    out.add("lens_workers_up", up, None, f"Workers that checked in within {WORKER_FRESH} seconds and aren't paused.")


def _measure(path):
    total = 0
    for root, _dirs, files in os.walk(path, onerror=lambda e: None):
        for f in files:
            try:
                total += os.lstat(os.path.join(root, f)).st_size
            except OSError:
                pass
    with _size_lock:
        _size.update(at=time.monotonic(), bytes=total, path=path, busy=False)


def data_size(path):
    """The data folder's size in bytes, measured in the background at most every SIZE_SECONDS; None until first measured."""
    with _size_lock:
        stale = _size["path"] != path or time.monotonic() - _size["at"] > SIZE_SECONDS or _size["bytes"] is None
        if stale and not _size["busy"]:
            _size["busy"] = True
            threading.Thread(target=_measure, args=(path,), name="lens-data-size", daemon=True).start()
        return _size["bytes"] if _size["path"] == path else None


def _storage(db, out, now):
    path = str(db.data_dir or "")
    if not path or not os.path.isdir(path):
        return
    try:
        du = shutil.disk_usage(path)
    except OSError:
        du = None
    if du:
        out.add("lens_disk_total_bytes", du.total, None, "Size of the disk the data folder is on.")
        out.add("lens_disk_free_bytes", du.free, None, "Free space on the disk the data folder is on.")
    out.add(
        "lens_data_bytes",
        data_size(path),
        None,
        f"Size of the data folder (database and files), measured at most every {SIZE_SECONDS} seconds.",
    )


def render(db, now=None):
    """Every metric, as Prometheus' text format. A part that fails is skipped and counted in lens_scrape_errors."""
    t = time.monotonic()
    now = now or dt.datetime.now(dt.timezone.utc)
    out = Out()
    out.add("lens_build_info", 1, {"version": __version__}, "The version of Lens running.")
    errors = 0
    parts = (_jobs, _routines, _resources, _ledger, _budgets, _workers, _storage)
    for part in parts:
        try:
            part(db, out, now)
        except Exception:  # noqa: BLE001 - one broken query mustn't lose the rest of the scrape
            log.exception("prometheus: %s failed", part.__name__)
            errors += 1
    out.add("lens_scrape_errors", errors, None, "Parts of this scrape that failed (see the API's log).")
    out.add("lens_scrape_seconds", round(time.monotonic() - t, 4), None, "How long this scrape took.")
    return out.text()
