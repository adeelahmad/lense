"""Activity: what happened to each resource, and what it cost (docs/activity.md).

One ledger, the `activity` table, a row for each

* call in: an API request that changes something (POST, PUT, PATCH, DELETE; reads too when `activity.reads` is on),
  counted for the resources in its path, what it made (a POST's reply {"id"}) and who called;
* call out: a model call, embeddings, the decision model, text to speech, a webhook, a web tool;
* run: a job or a routine run ending, with what it cost in all.

A row names every resource it touched as `table:id` strings (`routine:3`, `routine_run:12`, `job:40`, `recording:7`,
`pipeline:2`, `workflow:4`, `chat:9`, `space:1`, `account:1`), so a resource's history is the rows that name it, next
to its audit log entries. It carries tokens, the estimated cost in USD (from the prices in Settings), how long it took
and how it ended; never prompt, reply or file content.

Who a call is for comes from the work it runs in: `with activity.scope(db, "job:40", "recording:7"):` around a job, a
routine run or a workflow, and the API request's own resources (from its path) and caller. Scopes nest, so a model
call inside a workflow step of a job queued by a routine counts for all of them.

On by default (activity.enabled) and cheap: one small row per call. Kept activity.keep_days (365), then dropped by the
routines scheduler. Writing a row never breaks the work it describes.
"""

from __future__ import annotations

import contextlib
import contextvars
import datetime as dt
import json
import logging
import re
import time

from . import store

log = logging.getLogger("lens")
R = store.R

KINDS = ("in", "out", "run")
TABLES = {  # the API's path segments, and the table a number after one names
    "recordings": "recording",
    "resources": "recording",
    "routines": "routine",
    "runs": "routine_run",
    "pipelines": "pipeline",
    "workflows": "workflow",
    "custom-nodes": "custom_node",
    "chats": "chat",
    "jobs": "job",
    "namespaces": "space",
    "spaces": "space",
    "collections": "collection",
    "extensions": "extension",
    "sources": "watch_path",
    "watches": "watch_path",
    "sensors": "sensor",
    "templates": "template",
    "notes": "note",
    "comments": "comment",
    "entities": "entity",
    "speakers": "speaker",
    "users": "account",
    "batches": "batch",
    "budgets": "budget",
}
REF = re.compile(r"^[a-z_]{1,40}:[A-Za-z0-9_.-]{1,80}$")
CHANGING = {"POST", "PUT", "PATCH", "DELETE"}
SKIP_PATHS = ("/api/v1/auth/", "/api/v1/passkeys/")  # sign-in traffic is in the audit log; its bodies are secrets
SWEEP_SECONDS = 3600
CREATED_BYTES = 2048  # how much of a POST's reply is read for the id of what it made
FIELDS = "record::id(id) AS id, at, kind, action, resources, actor, model, tokens_in, tokens_out, cost_usd, ms, ok, error, detail"

_scope: contextvars.ContextVar[dict | None] = contextvars.ContextVar("lens_activity", default=None)
_default = {"db": None}
_swept = {"at": 0.0}


def bind(db):
    """The process's database, for calls made outside any scope (the API, `lens worker`)."""
    _default["db"] = db


def _section(cfg):
    return (cfg or {}).get("activity") or {}


def enabled(cfg):
    return _section(cfg).get("enabled", True) is not False


def ref(table, key):
    return f"{table}:{key}"


@contextlib.contextmanager
def scope(db, *refs, cfg=None):
    """Calls made inside count for these resources too (and for the scopes around this one)."""
    outer = _scope.get()
    mine = [r for r in refs if r]
    token = _scope.set(
        {
            "db": db if db is not None else (outer or {}).get("db"),
            "refs": [*((outer or {}).get("refs") or []), *mine],
            "cfg": cfg if cfg is not None else (outer or {}).get("cfg"),
            "asgi": (outer or {}).get("asgi"),
        }
    )
    try:
        yield
    finally:
        _scope.reset(token)


def current():
    """The resources calls made here count for, in order, without repeats."""
    s = _scope.get() or {}
    out = list(s.get("refs") or [])
    asgi = s.get("asgi")
    if asgi is not None:
        out += request_refs(asgi)
    return list(dict.fromkeys(r for r in out if REF.match(r)))


def request_refs(asgi):
    """An API request's resources: each number (or path parameter) after a known path segment, and who called."""
    out = []
    params = {str(v) for v in (asgi.get("path_params") or {}).values()}
    parts = [p for p in (asgi.get("path") or "").split("/") if p]
    for a, b in zip(parts, parts[1:]):
        table = TABLES.get(a)
        if table and (b.isdigit() or b in params) and b not in TABLES:
            out.append(ref(table, b))
    p = (asgi.get("state") or {}).get("principal")
    if p is not None and getattr(p, "id", None) is not None:
        out.append(ref("account", p.id))
    return out


def _actor(asgi):
    p = ((asgi or {}).get("state") or {}).get("principal")
    return getattr(p, "id", None)


def record(kind, action, refs=(), cfg=None, db=None, **fields):
    """Write one row. `fields`: model, tokens_in, tokens_out, cost_usd, ms, ok, error (a type name), detail (a small
    dict of counts and ids). Returns the row's id, or None when it wasn't written."""
    s = _scope.get() or {}
    cfg = cfg if cfg is not None else s.get("cfg")
    if cfg is not None and not enabled(cfg):
        return None
    db = db or s.get("db") or _default["db"]
    if db is None:
        return None
    resources = list(dict.fromkeys([*current(), *(r for r in refs if r and REF.match(r))]))
    row = store.clean(
        {
            "at": dt.datetime.now(dt.timezone.utc).isoformat(timespec="milliseconds"),  # in order within a second
            "kind": kind,
            "action": action[:200],
            "resources": resources,
            "actor": fields.pop("actor", None) or _actor(s.get("asgi")),
            **{k: v for k, v in fields.items() if k in ("model", "tokens_in", "tokens_out", "cost_usd", "ms", "ok", "error", "detail")},
        }
    )
    row.setdefault("ok", True)
    try:
        got = db.values("CREATE activity CONTENT $d RETURN VALUE record::id(id)", d=row)
        return got[0] if got else None
    except Exception as e:  # noqa: BLE001 - the ledger never breaks the work it describes
        log.debug("activity: couldn't write a row: %s", e)
        return None


class Call:
    """A call out, timed: `with activity.call("embeddings", model=m) as c: ...; c.usage(tin, tout)`. An exception that
    leaves the block marks the row failed with its type."""

    def __init__(self, action, cfg=None, model=None, refs=(), detail=None, price=None):
        self.action, self.cfg, self.model, self.refs, self.detail, self.price = action, cfg, model, refs, detail, price
        self.tin = self.tout = self.cost = None
        self.t0 = time.monotonic()
        self.done = False

    def usage(self, tokens_in=None, tokens_out=None, cost_usd=None):
        if isinstance(tokens_in, int):
            self.tin = tokens_in
        if isinstance(tokens_out, int):
            self.tout = tokens_out
        if cost_usd is not None:
            self.cost = cost_usd
        return self

    def end(self, error=None):
        if self.done:
            return
        self.done = True
        cost = self.cost
        if cost is None and self.price is not None:
            cost = self.price(self.model, self.tin, self.tout)
        record(
            "out",
            self.action,
            self.refs,
            self.cfg,
            model=self.model,
            tokens_in=self.tin,
            tokens_out=self.tout,
            cost_usd=cost,
            ms=int((time.monotonic() - self.t0) * 1000),
            ok=error is None,
            error=type(error).__name__ if error is not None else None,
            detail=self.detail,
        )

    def __enter__(self):
        return self

    def __exit__(self, et, e, tb):
        self.end(e)
        return False


def call(action, cfg=None, model=None, refs=(), detail=None, price=None):
    return Call(action, cfg, model, refs, detail, price)


def token_cost(cfg):
    """A price function for Call: USD from the per-million-token prices in Settings (telemetry.prices)."""
    from . import telemetry

    prices = telemetry.prices(cfg)

    def price(model, tin, tout):
        if tin is None and tout is None:
            return None
        return telemetry.cost(model, tin or 0, tout or 0, prices)

    return price


# ---------- the API ----------
class Middleware:
    """A row for each API request that changes something (or every request, with activity.reads on), naming the
    resources in its path and who called; calls the request makes count for those resources too."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope_, receive, send):
        if scope_["type"] != "http" or not scope_.get("path", "").startswith("/api/"):
            await self.app(scope_, receive, send)
            return
        app = scope_.get("app")
        db = getattr(getattr(app, "state", None), "db", None)
        token = _scope.set({"db": db, "refs": [], "cfg": None, "asgi": scope_})
        status, head = [None], [b""]

        async def sending(message):
            if message["type"] == "http.response.start":
                status[0] = message["status"]
            elif message["type"] == "http.response.body" and len(head[0]) < CREATED_BYTES and scope_.get("method") == "POST":
                head[0] += message.get("body", b"")[:CREATED_BYTES]
            await send(message)

        t0, error = time.monotonic(), None
        try:
            await self.app(scope_, receive, sending)
        except Exception as e:
            error = e
            raise
        finally:
            try:
                self._log(scope_, app, db, status[0], error, t0, head[0])
            finally:
                _scope.reset(token)

    def _log(self, scope_, app, db, status, error, t0, head=b""):
        method = scope_.get("method") or "GET"
        path = scope_.get("path") or ""
        if db is None or path.startswith(SKIP_PATHS):
            return
        try:
            cfg = app.state.settings.current()
        except Exception:  # noqa: BLE001
            return
        if not enabled(cfg) or (method not in CHANGING and not _section(cfg).get("reads")):
            return
        from . import telemetry

        code = status or 500
        record(
            "in",
            f"{method} {telemetry._route_template(scope_)}",
            [created(path, head)] if method == "POST" and 200 <= code < 300 else (),
            cfg=cfg,
            db=db,
            ms=int((time.monotonic() - t0) * 1000),
            ok=error is None and code < 400,
            error=type(error).__name__ if error is not None else (str(code) if code >= 400 else None),
            detail={"status": code},
        )


def created(path, head):
    """What a POST to a collection made, from its reply ({"id": n}): `routine:7` for POST /api/v1/routines."""
    table = TABLES.get(path.rstrip("/").rsplit("/", 1)[-1])
    if not table or not head:
        return None
    try:
        got = json.loads(head)
    except ValueError:
        return None
    key = got.get("id") if isinstance(got, dict) else None
    return ref(table, key) if isinstance(key, (int, str)) and not isinstance(key, bool) else None


# ---------- reading it ----------
def _since(period, now=None):
    """The start of the period now is in (UTC): day, week (from Monday), month; None for all time."""
    now = now or dt.datetime.now(dt.timezone.utc)
    if period == "day":
        start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "week":
        start = (now - dt.timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    elif period == "month":
        start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:
        return None
    return start.isoformat(timespec="seconds")


def _when(at):
    """A row's time, for ordering ledger rows (milliseconds) and audit entries (seconds) together."""
    try:
        t = dt.datetime.fromisoformat(str(at))
        return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)
    except ValueError:
        return dt.datetime.min.replace(tzinfo=dt.timezone.utc)


def history(db, resource, limit=100, before=None, kind=None):
    """A resource's activity, newest first: its ledger rows and its audit log entries, merged."""
    where, p = ["resources CONTAINS $res"], {"res": resource, "n": limit}
    if before:
        where.append("at < $before")
        p["before"] = before
    if kind:
        where.append("kind = $kind")
        p["kind"] = kind
    rows = db.rows(f"SELECT {FIELDS} FROM activity WHERE {' AND '.join(where)} ORDER BY at DESC LIMIT $n", **p)
    if kind in (None, "change"):
        audit = db.rows(
            "SELECT at, account, email, action, target, detail FROM audit_log WHERE target = $res"
            + (" AND at < $before" if before else "")
            + " ORDER BY at DESC LIMIT $n",
            **p,
        )
        rows += [
            {
                "id": None,
                "at": a["at"],
                "kind": "change",
                "action": a["action"],
                "resources": [resource],
                "actor": a.get("account"),
                "email": a.get("email"),
                "ok": True,
                "detail": a.get("detail") if isinstance(a.get("detail"), dict) else ({"items": a["detail"]} if a.get("detail") else None),
            }
            for a in audit
        ]
        rows.sort(key=lambda r: _when(r.get("at")), reverse=True)
        rows = rows[:limit]
    return rows


def totals(db, resource=None, since=None, until=None):
    """What a resource (or everything) cost: calls, tokens, USD and time, by kind of call."""
    where, p = [], {}
    if resource:
        where.append("resources CONTAINS $res")
        p["res"] = resource
    if since:
        where.append("at >= $since")
        p["since"] = since
    if until:
        where.append("at < $until")
        p["until"] = until
    rows = db.rows(
        "SELECT kind, count() AS calls, math::sum(tokens_in ?? 0) AS tokens_in, math::sum(tokens_out ?? 0) AS tokens_out, "
        "math::sum(cost_usd ?? 0) AS cost_usd, math::sum(ms ?? 0) AS ms, count(ok = false) AS failed FROM activity"
        + (f" WHERE {' AND '.join(where)}" if where else "")
        + " GROUP BY kind",
        **p,
    )
    out = {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0, "ms": 0, "failed": 0, "by_kind": {}}
    for r in rows:
        k = r.get("kind") or "?"
        # a run row repeats what its calls cost (its cost is their sum): counted on its own, not twice
        out["by_kind"][k] = {x: r.get(x) or 0 for x in ("calls", "tokens_in", "tokens_out", "cost_usd", "ms", "failed")}
        out["calls"] += r.get("calls") or 0
        out["failed"] += r.get("failed") or 0
        if k != "run":
            for x in ("tokens_in", "tokens_out", "cost_usd", "ms"):
                out[x] += r.get(x) or 0
    out["cost_usd"] = round(out["cost_usd"], 6)
    return out


def spent(db, resource, since=None):
    """(USD, tokens) a resource's calls cost since a time (not counting run rows, which repeat them)."""
    p = {"res": resource}
    q = "SELECT math::sum(cost_usd ?? 0) AS usd, math::sum((tokens_in ?? 0) + (tokens_out ?? 0)) AS tokens FROM activity WHERE resources CONTAINS $res AND kind != 'run'"
    if since:
        q += " AND at >= $since"
        p["since"] = since
    row = db.one(q + " GROUP ALL", **p) or {}
    return round(float(row.get("usd") or 0), 6), int(row.get("tokens") or 0)


def top(db, prefix=None, since=None, limit=20):
    """The resources that cost most since a time, of one table (`routine`) or any: [{resource, cost_usd, tokens, calls}]."""
    p = {"n": limit, "p": f"{prefix}:" if prefix else ""}
    inner = "kind != 'run' AND array::len(resources ?? []) > 0"
    if since:
        inner += " AND at >= $since"
        p["since"] = since
    rows = db.rows(
        "SELECT res AS resource, math::sum(cost_usd ?? 0) AS cost_usd, math::sum((tokens_in ?? 0) + (tokens_out ?? 0)) AS tokens, "
        f"count() AS calls FROM (SELECT resources AS res, cost_usd, tokens_in, tokens_out FROM activity WHERE {inner} SPLIT res) "
        "WHERE string::starts_with(res, $p) GROUP BY res ORDER BY cost_usd DESC, tokens DESC LIMIT $n",
        **p,
    )
    for r in rows:
        r["cost_usd"] = round(float(r.get("cost_usd") or 0), 6)
    return rows


def run_total(db, resource):
    """What one run's calls cost: (USD or None, tokens or None), for its run row."""
    usd, tokens = spent(db, resource)
    return (usd or None), (tokens or None)


def tidy(db, cfg, now=None):
    """Drop rows older than activity.keep_days, at most once an hour per process; the cut-off, or 0 when it didn't look."""
    t = time.monotonic()
    if now is None and t - _swept["at"] < SWEEP_SECONDS and _swept["at"]:
        return 0
    _swept["at"] = t
    days = int(_section(cfg).get("keep_days") or 365)
    cut = ((now or dt.datetime.now(dt.timezone.utc)) - dt.timedelta(days=days)).isoformat(timespec="seconds")
    db.q("DELETE activity WHERE at < $cut", cut=cut)
    return cut
