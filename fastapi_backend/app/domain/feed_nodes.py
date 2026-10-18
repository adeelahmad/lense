"""Graph workflow nodes that publish: pick resources or timeline activities from the namespaces a routine runs over,
add them to a published feed (syndication.py), or make a podcast episode about them (podcasts.py). A routine running
such a workflow on a schedule is how a feed fills itself: the new resources every morning, a weekly episode, the
week's timeline.

- `select_assets`: the resources in the run's namespaces (narrowed to `namespaces`), matching `q`, `topics`,
  `entities` and `status`, that came in since `since` (what the routine's last run didn't see, by default), newest
  first, `limit` at most; episodes left out unless `include_episodes`. Passes on a list of {id, title, summary, ...}.
- `select_activities`: timeline activities of `kinds` in those namespaces since `since`, matching `q` and `where`
  (events.check_filter), newest first; only what every reader of the namespace sees, never someone's own.
- `publish_feed`: adds what comes in (resources, activities, or text a template node wrote) to feed `feed`, whose
  namespace the run covers. What's in the feed already isn't added twice; what may not show there is refused and said.
- `podcast_episode`: an episode about the resources that come in (`max_sources` at most, those with text), with
  `title`, `prompt`, `length` and `style`, placed in `namespace` (else the feed's, else where podcasts.target puts it)
  and added to feed `feed`, where it's listed once it's made.

A dry run (Try on the canvas) says what each node would do and keeps nothing.
"""

from __future__ import annotations

from . import store

R = store.R
OWN_NODES = ("select_assets", "select_activities", "publish_feed", "podcast_episode")
TERMINAL = {"publish_feed", "podcast_episode"}
CONFIG = {
    "select_assets": {"namespaces", "q", "topics", "entities", "status", "since", "limit", "include_episodes"},
    "select_activities": {"namespaces", "kinds", "q", "where", "since", "limit"},
    "publish_feed": {"feed", "title"},
    "podcast_episode": {"feed", "namespace", "title", "prompt", "length", "style", "max_sources", "min_sources"},
}
SINCE = ("last_run", "day", "week", "month", "all")
DAYS = {"day": 1, "week": 7, "month": 30}
LIMITS = {"select_assets": (50, 500), "select_activities": (50, 200)}  # (default, most)
MAX_SOURCES = 20
ASSET_FIELDS = "record::id(id) AS id, title, summary, recorded_at, created_at, duration_ms, status, media, space"
SUMMARY_MAX = 1000


# ---------- settings ----------
def _names(db, nid, t, value, what):
    """Namespace names (or ids) that exist, as a list; ValueError otherwise."""
    if not isinstance(value, list) or not all(isinstance(x, (str, int)) and not isinstance(x, bool) for x in value):
        raise ValueError(f"{t} node {nid}: {what} is a list of namespace names")
    if db is not None and value:
        known = store.space_names(db)
        for x in value:
            if (x not in known.values()) if isinstance(x, str) else (x not in known):
                raise ValueError(f"{t} node {nid}: no namespace {x}")
    return value


def _ints(nid, t, value, what):
    if not isinstance(value, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in value):
        raise ValueError(f"{t} node {nid}: {what} is a list of ids")


def _limit(nid, t, cfg):
    _, most = LIMITS[t]
    lim = cfg.get("limit")
    if lim is not None and (not isinstance(lim, int) or isinstance(lim, bool) or not 1 <= lim <= most):
        raise ValueError(f"{t} node {nid}: limit is 1 to {most}")


def _text(nid, t, cfg, key, most):
    v = cfg.get(key)
    if v is not None and (not isinstance(v, str) or len(v) > most):
        raise ValueError(f"{t} node {nid}: {key} is text, {most} characters at most")


def _feed(db, nid, t, cfg, required):
    from . import syndication

    fid = cfg.get("feed")
    if fid is None:
        if required:
            raise ValueError(f"{t} node {nid}: choose a feed")
        return
    if not isinstance(fid, int) or isinstance(fid, bool):
        raise ValueError(f"{t} node {nid}: feed is a feed's id")
    if db is not None:
        try:
            syndication.get(db, fid)
        except KeyError:
            raise ValueError(f"{t} node {nid}: feed {fid} doesn't exist") from None


def check_config(db, nid, t, cfg):
    """ValueError naming what is wrong with a publishing node's settings."""
    from . import events, library, podcasts

    if cfg.get("since") is not None and cfg["since"] not in SINCE:
        raise ValueError(f"{t} node {nid}: since is {', '.join(SINCE)}")
    if t in ("select_assets", "select_activities"):
        if cfg.get("namespaces") is not None:
            _names(db, nid, t, cfg["namespaces"], "namespaces")
        _text(nid, t, cfg, "q", 200)
        _limit(nid, t, cfg)
    if t == "select_assets":
        for k in ("topics", "entities"):
            if cfg.get(k) is not None:
                _ints(nid, t, cfg[k], k)
        st = cfg.get("status")
        if st is not None and (not isinstance(st, list) or any(s not in library.STATUSES for s in st)):
            raise ValueError(f"{t} node {nid}: status is a list of {', '.join(library.STATUSES)}")
        if cfg.get("include_episodes") is not None and not isinstance(cfg["include_episodes"], bool):
            raise ValueError(f"{t} node {nid}: include_episodes is true or false")
    elif t == "select_activities":
        flt = {k: cfg[k] for k in ("kinds", "where") if cfg.get(k) is not None}
        if flt.get("kinds") is not None and not isinstance(flt["kinds"], list):
            raise ValueError(f"{t} node {nid}: kinds is a list of kinds of activity")
        try:
            events.check_filter(flt)
        except ValueError as e:
            raise ValueError(f"{t} node {nid}: {e}") from None
    elif t == "publish_feed":
        _feed(db, nid, t, cfg, required=True)
        _text(nid, t, cfg, "title", 200)
    elif t == "podcast_episode":
        _feed(db, nid, t, cfg, required=False)
        if cfg.get("namespace") is not None:
            _names(db, nid, t, [cfg["namespace"]], "namespace")
        _text(nid, t, cfg, "title", 200)
        _text(nid, t, cfg, "prompt", 2000)
        n = cfg.get("length")
        if n is not None and (not isinstance(n, int) or isinstance(n, bool) or not 1 <= n <= 180):
            raise ValueError(f"{t} node {nid}: length is 1 to 180 minutes")
        if cfg.get("style") is not None and cfg["style"] not in podcasts.STYLES:
            raise ValueError(f"{t} node {nid}: style is one of {', '.join(podcasts.STYLES)}")
        most = cfg.get("max_sources")
        if most is not None and (not isinstance(most, int) or isinstance(most, bool) or not 1 <= most <= MAX_SOURCES):
            raise ValueError(f"{t} node {nid}: max_sources is 1 to {MAX_SOURCES}")
        least = cfg.get("min_sources")
        if least is not None and (not isinstance(least, int) or isinstance(least, bool) or not 1 <= least <= (most or MAX_SOURCES)):
            raise ValueError(f"{t} node {nid}: min_sources is 1 to max_sources")


# ---------- running ----------
def _by(r):
    rid = r.origin.get("routine")
    return f"routine:{rid}" if rid else r.user


def _spaces(r, c, say, name):
    """The run's namespaces, narrowed to the node's `namespaces` (one the run doesn't cover is said and skipped)."""
    run = {int(s) for s in r.extra.get("spaces") or ()}
    want = c.get("namespaces")
    if not want:
        return sorted(run)
    names = store.space_names(r.db)
    by_name = {v: k for k, v in names.items()}
    out = set()
    for n in want:
        sid = by_name.get(n) if isinstance(n, str) else n
        if sid in run:
            out.add(sid)
        else:
            say(f"{name}: namespace {n} isn't one this run covers; skipped")
    return sorted(out)


def _since(r, c):
    """The time after which things count, as store.now() writes times; None for everything. `last_run` is when the
    routine's last run started (on its first run, or with no routine, the past week)."""
    from . import events

    s = c.get("since") or "last_run"
    if s == "all":
        return None
    if s == "last_run":
        rid = r.origin.get("routine")
        if rid:
            prev = r.db.values(
                "SELECT VALUE started_at FROM routine_run WHERE routine = $r AND status != 'running' ORDER BY started_at DESC LIMIT 1",
                r=int(rid),
            )
            if prev and prev[0]:
                return prev[0]
        s = "week"
    return events.ago(DAYS[s] * 86400)


def select_assets(r, c, say, name):
    from . import library

    spaces = _spaces(r, c, say, name)
    since = _since(r, c)
    if since:
        say(f"{name}: what came in since {since}")
    if not spaces:
        return []
    only = None
    if since:
        only = r.db.values("SELECT VALUE record::id(id) FROM recording WHERE space IN $s AND created_at > $t", s=spaces, t=since)
        if not only:
            return []
    cond, p = library.where(
        r.db, spaces, q=c.get("q"), status=c.get("status"), topics=c.get("topics"), entities=c.get("entities"), only=only, cfg=r.cfg
    )
    if not c.get("include_episodes"):
        cond += " AND engine != 'podcast'"
    limit = c.get("limit") or LIMITS["select_assets"][0]
    rows = r.db.rows(f"SELECT {ASSET_FIELDS} FROM recording WHERE {cond} ORDER BY created_at DESC, id DESC LIMIT {int(limit)}", **p)
    names = store.space_names(r.db)
    out = []
    for row in rows:
        summary = row.get("summary")
        out.append(
            store.clean(
                {
                    **row,
                    "summary": summary[:SUMMARY_MAX] if isinstance(summary, str) else None,
                    "namespace": names.get(row["space"]),
                    "path": f"/resources/{row['id']}",
                }
            )
        )
    return out


def select_activities(r, c, say, name):
    from . import events

    spaces = _spaces(r, c, say, name)
    if not spaces:
        return []
    flt = store.clean({"kinds": c.get("kinds") or None, "spaces": spaces, "since": _since(r, c), "q": c.get("q"), "where": c.get("where")})
    who = events.viewer(None, spaces, False)
    return events.page(r.db, r.cfg, who, flt=flt, limit=c.get("limit") or LIMITS["select_activities"][0])["items"]


def _entries(value, title=None):
    """What came in, as things to publish (syndication.entry_from), and how many weren't anything a feed lists."""
    from . import syndication

    items = value if isinstance(value, list) else [value]
    out, unusable = [], 0
    for v in items:
        if isinstance(v, str) and v.strip():
            v = {"title": title, "summary": v}
        e = syndication.entry_from(v)
        if e is None:
            unusable += 1
        else:
            out.append(e)
    return out, unusable


def _feed_covered(r, fid, name):
    from . import syndication

    try:
        feed = syndication.get(r.db, int(fid))
    except KeyError:
        raise ValueError(f"{name}: feed {fid} is gone; choose another") from None
    if feed["space"] not in {int(s) for s in r.extra.get("spaces") or ()}:
        raise ValueError(f"{name}: the feed {feed['title']!r} is in a namespace this run doesn't cover")
    return feed


def _publish(r, feed, entries, node, say, name):
    from . import syndication

    got = syndication.publish(r.db, r.cfg, feed["id"], entries, origin={**r.origin, "node": node["id"]}, by=_by(r))
    r.totals["published"] = r.totals.get("published", 0) + len(got["added"])
    say(f"{name}: {len(got['added'])} added to {feed['title']!r}, {got['already']} there already, {len(got['refused'])} refused")
    for x in got["refused"][:5]:
        say(f"{name}: {x['what']}: {x['why']}")
    return got


def publish_feed(r, node, value, say, name):
    c = node["config"]
    feed = _feed_covered(r, c["feed"], name)
    entries, unusable = _entries(value, c.get("title"))
    if unusable:
        say(f"{name}: {unusable} of what came in isn't a resource, an activity or text; skipped")
    if r.dry:
        say(f"{name}: would add {len(entries)} item(s) to {feed['title']!r}")
        return {}
    _publish(r, feed, entries, node, say, name)
    return {}


def podcast_episode(r, node, value, say, name):
    from . import podcasts

    c = node["config"]
    run_spaces = {int(s) for s in r.extra.get("spaces") or ()}
    feed = _feed_covered(r, c["feed"], name) if c.get("feed") is not None else None
    space = None
    if c.get("namespace") is not None:
        names = store.space_names(r.db)
        space = c["namespace"] if isinstance(c["namespace"], int) else next((k for k, v in names.items() if v == c["namespace"]), None)
        if space is None or space not in run_spaces:
            raise ValueError(f"{name}: namespace {c['namespace']} isn't one this run covers")
    elif feed is not None:
        space = feed["space"]
    entries, _ = _entries(value)
    wanted = list(dict.fromkeys(int(e["recording"]) for e in entries if e.get("recording") is not None))
    rows = r.db.rows("SELECT record::id(id) AS id, space, status FROM recording WHERE id IN $ids", ids=[R("recording", i) for i in wanted])
    by_id = {x["id"]: x for x in rows}
    fits, ids = {}, []
    for i in wanted:
        row = by_id.get(i)
        if not row or row.get("status") in (None, "new", "error"):
            continue  # nothing to read yet
        if space is not None:
            if row["space"] not in fits:
                fits[row["space"]] = podcasts.fits(r.db, space, {row["space"]})
            if not fits[row["space"]]:
                say(f"{name}: resource {i} is in a namespace the episode's readers couldn't read; left out")
                continue
        ids.append(i)
    ids = ids[: c.get("max_sources") or 10]
    if len(ids) < (c.get("min_sources") or 1):
        say(f"{name}: {len(ids)} resource(s) with text came in; no episode")
        return {}
    length = c.get("length") or 10
    where = f" in {c['namespace']}" if c.get("namespace") else " in the feed's namespace" if feed else ""
    if r.dry:
        say(
            f"{name}: would make a {length}-minute episode about {len(ids)} resource(s){where}"
            + (f" and add it to {feed['title']!r}" if feed else "")
        )
        return {}
    made = podcasts.create(
        r.db,
        r.cfg,
        {"recordings": ids},
        by=_by(r),
        admin=True,
        prompt=c.get("prompt"),
        length=length,
        style=c.get("style"),
        title=c.get("title"),
        space=space,
    )
    r.totals["episodes"] = r.totals.get("episodes", 0) + 1
    say(f"{name}: episode {made['episode']} about {len(ids)} resource(s) queued (job {made['job']})")
    if feed is not None:
        _publish(r, feed, [{"recording": made["episode"]}], node, say, name)
    return {}


def run_node(r, f, node, value, vals):
    """What a publishing node does: {output port: value}."""
    t, c, name = node["type"], node["config"], node.get("label") or f"{node['type']} {node['id']}"
    if t == "select_assets":
        out = select_assets(r, c, r.say, name)
        r.say(f"{name}: {len(out)} resource(s)")
        return {"out": out}
    if t == "select_activities":
        out = select_activities(r, c, r.say, name)
        r.say(f"{name}: {len(out)} activit{'y' if len(out) == 1 else 'ies'}")
        return {"out": out}
    if t == "publish_feed":
        return publish_feed(r, node, value, r.say, name)
    if t == "podcast_episode":
        return podcast_episode(r, node, value, r.say, name)
    return {}
