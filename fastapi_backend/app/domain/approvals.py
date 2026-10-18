"""Approvals (docs/automations.md, Approvals): everything that waits for the person's pick, in one table and one inbox.

The `approval` table holds two kinds, told apart by `origin` and `intent`:

- the assistant's (`chat` set, `tool` and `args`): a change it proposed in a conversation, or that an automation's
  agent proposed while it ran (`origin` set too), carried out by `ai_tools.approve` as they always were;
- an automation's (`origin` {automation, run, node} and `intent`, `tool` "action"): an action node whose intent asks
  first, which the engine hands to `ask`. Approving it performs the intent through `actions.perform`, as the
  automation and for its person, and records the outcome on the run (`automations.update_intent`).

An automation's approval is the person's pick: approving it (or editing its params, or allowing that node from now on)
needs them signed in to Lens, or a yes in the chat it was told in (bridge.decide_in_room). An API key, an app, MCP and
the assistant can list and decline approvals, never approve them. Undecided ones expire after
`automations.approval_days` (default 7) and nothing runs; the assistant's own approvals never expire, as before.

Statuses: pending (waiting), running (being carried out), done, failed, declined, expired.
"""

from __future__ import annotations

import datetime as dt
import importlib
import logging
import time

from . import actions, activity, channels, events, graph_history, store
from . import automation_spec as spec_
from .store import R

log = logging.getLogger("lens.automations")
CAP = 100  # waiting approvals per automation; past that its intents are dropped
SWEEP_SECONDS = 3600
STUCK_SECONDS = 3600  # an approval still running after this was cut short (the server stopped while it ran)
STATUSES = ("pending", "running", "done", "failed", "declined", "expired")
DECISIONS = ("approve", "sample", "decline", "edit", "always")
DROPPED = f"dropped: {CAP} approvals already wait for this automation; decide some in Approvals"
FIELDS = (
    "record::id(id) AS id, chat, account, tool, args, summary, preview, estimate, status, created_at, result, error, origin, "
    "intent, space, expires_at, decided_by, decided_at, told, why, automation"
)
_swept = {"at": 0.0}


def _studio(fn):
    """`fn` of the automations module (update_intent, set_policy) once it is there, else None: until stored
    automations land, the approval itself keeps the outcome."""
    try:
        mod = importlib.import_module(f"{__package__}.automations")
    except ModuleNotFoundError as e:
        if e.name != f"{__package__}.automations":
            raise
        return None
    return getattr(mod, fn, None)


def days(cfg):
    return int((cfg.get("automations") or {}).get("approval_days") or 7)


def _iso(t):
    return t.astimezone(dt.timezone.utc).isoformat(timespec="seconds")


def _record(db, intent, status, result=None):
    """The intent's outcome on its run (M6's update_intent), when runs are stored."""
    update = _studio("update_intent")
    if update is None or intent.get("run") is None:
        return
    try:
        update(db, intent["run"], intent["key"], status, result)
    except Exception:  # noqa: BLE001 - the approval keeps the outcome either way
        log.warning("automation run %s: couldn't record %s as %s", intent.get("run"), intent.get("key"), status, exc_info=True)


def waiting(db, automation):
    """How many approvals wait for one automation."""
    row = db.one("SELECT count() AS n FROM approval WHERE origin.automation = $a AND status = 'pending' GROUP ALL", a=int(automation))
    return int((row or {}).get("n") or 0)


def _event_of(db, cfg, intent):
    """The activity that fired the intent's run: the last of the run's events, when runs are stored."""
    if intent.get("run") is None:
        return None
    try:
        row = db.one("SELECT event, event_ids FROM $r", r=R("automation_run", int(intent["run"]))) or {}
        ids = row.get("event_ids") or ([row["event"]] if row.get("event") else [])
    except Exception:  # noqa: BLE001 - runs aren't stored yet
        return None
    if not ids:
        return None
    last = ids[-1]
    return events.get(db, cfg, last if isinstance(last, str) else (last or {}).get("id"), events.viewer(admin=True))


def _why(event):
    """What the activity that caused it said, kept with the approval (it stays readable if the activity goes)."""
    if not isinstance(event, dict) or not event.get("id"):
        return None
    return store.clean(
        {
            "activity": event["id"],
            "kind": event.get("kind"),
            "summary": event.get("summary"),
            "published": event.get("published"),
            "url": (event.get("object") or {}).get("url"),
        }
    )


def _space(intent, event):
    spaces = intent.get("spaces") or []
    s = (event or {}).get("space")
    if s is not None and (not spaces or s in spaces):
        return s
    return spaces[0] if spaces else None


def link(cfg, db, aid):
    """Where the person decides: the approval in the inbox, on the address they open the web app at."""
    from app.email import app_url

    return f"{app_url(cfg, db)}/approvals?id={aid}"


def message(name, what, url, yes=False):
    """What the person is told when an automation's action waits for them; `yes`: a reply there decides it."""
    said = what[:1].lower() + what[1:] if what else "do something"
    if yes:
        return f"{name} wants to {said}. Reply “yes” to do it or “no” not to, or open {url} to decide."
    return f"{name} wants to {said}. Open {url} to decide."


def yes_counts(db, v, r, aid, effect):
    """Whether a yes in the chat of chat_route `r` (channel view `v`) would decide approval `aid`: only for an action
    that changes something inside Lens (one that reaches outside is decided on its card), when the channel lets the
    person in that chat approve (bridge.room_approver: approving on, and they're on its list of people), and when
    nothing else waits in that chat (a bare yes would be ambiguous)."""
    from . import bridge

    if effect != "lens" or r.get("conversation") is None or not bridge.room_approver(v, r.get("target") or {}):
        return False
    return not [a for a in bridge.pending(db, r["conversation"], r.get("account")) if a["id"] != aid]


def ask(db, cfg, intent, event=None):
    """An approval for an intent that asks first (the engine's hand-off): the row, for the automation's person, with
    what it would do, the activity that caused it (`event`, else the run's last) and when it expires; then the person
    is told once in their own chat app, when they have one linked (channels.tell). Its id; the same id when the intent
    was handed over before; None when CAP approvals already wait for the automation (the intent is dropped, said on
    the run). ValueError for an intent that doesn't check or acts for nobody."""
    i = spec_.check_intent(intent)
    if i["automation"] is None or i["run"] is None:
        raise ValueError("only a run of a saved automation asks: a dry run never does")
    person = actions.person(db, i)
    same = db.one(
        "SELECT VALUE record::id(id) FROM approval WHERE origin.automation = $a AND origin.run = $r AND intent.key = $k LIMIT 1",
        a=i["automation"],
        r=i["run"],
        k=i["key"],
    )
    if same is not None:
        return same
    if waiting(db, i["automation"]) >= CAP:
        _record(db, i, "failed", {"error": DROPPED})
        log.warning("automation %s: %s", i["automation"], DROPPED)
        return None
    event = event if event is not None else _event_of(db, cfg, i)
    name = actions.actor(db, i)["name"]
    what = actions.describe(i["action"], i["params"])
    now = dt.datetime.now(dt.timezone.utc)
    aid = db.next_id("approval")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("approval", aid),
        d=store.clean(
            {
                "account": person["id"],
                "origin": {"automation": i["automation"], "run": i["run"], "node": i["node"]},
                "tool": "action",
                "intent": {**i, "status": "waiting"},
                "automation": name,
                "summary": what,
                "why": _why(event),
                "space": _space(i, event),
                "status": "pending",
                "created_at": _iso(now),
                "expires_at": _iso(now + dt.timedelta(days=days(cfg))),
            }
        ),
    )
    told = tell(db, cfg, person, aid, name, what, i["effect"])
    if told:
        db.q("UPDATE $r SET told = $t", r=R("approval", aid), t=told)
    return aid


def tell(db, cfg, person, aid, name, what, effect=None):
    """Tell the person once, in their own chat apps, that something waits, saying a reply decides it where it would
    (yes_counts): the chats it went to."""
    url = link(cfg, db, aid)

    def text(ch, v, r):
        return message(name, what, url, yes_counts(db, v, r, aid, effect))

    try:
        got = channels.tell(db, cfg, person, text, links=(url,))
    except Exception:  # noqa: BLE001 - the approval waits in the inbox either way
        log.warning("approval %s: couldn't tell %s", aid, person.get("email"), exc_info=True)
        return []
    return [store.clean({k: s.get(k) for k in ("channel", "label", "chat", "conversation")}) for s in got.get("sent") or []]


# ---------- deciding ----------
def get(db, aid):
    return db.one(f"SELECT {FIELDS} FROM $r", r=R("approval", int(aid)))


def _secret_headers(action, params):
    if action != "http_request" or not isinstance(params.get("headers"), dict):
        return set()
    from . import actions_web

    return {k for k in params["headers"] if actions_web.secret(k)}


def shown(intent):
    """An intent as the inbox shows it: credential headers hidden (actions_web.HIDDEN), as the action describes them."""
    if not isinstance(intent, dict):
        return None
    params = dict(intent.get("params") or {})
    hide = _secret_headers(intent.get("action"), params)
    if hide:
        from . import actions_web

        params["headers"] = {k: actions_web.HIDDEN if k in hide else v for k, v in params["headers"].items()}
    return {k: intent.get(k) for k in ("action", "effect", "risk", "untrusted", "node", "status", "spaces")} | {"params": params}


def edited(intent, new):
    """The intent's params with the person's edits on top (a param given as null is left out), checked against the
    action's schema. A hidden credential header given back as shown keeps its sealed value. ValueError naming the
    problem."""
    if not isinstance(new, dict):
        raise ValueError("give the params to change as an object")
    spec = actions.get(intent["action"])
    old = dict(intent.get("params") or {})
    params = {k: v for k, v in {**old, **new}.items() if v is not None}
    hide = _secret_headers(intent["action"], old)
    if hide and isinstance(params.get("headers"), dict):
        from . import actions_web

        params["headers"] = {k: old["headers"][k] if k in hide and v == actions_web.HIDDEN else v for k, v in params["headers"].items()}
    out = actions.check_params(spec, params)
    props = spec.params.get("properties") or {}
    for k, v in out.items():  # a template isn't filled in any more: it stands for itself
        want = (props.get(k) or {}).get("type")
        if want and want != "string" and isinstance(v, str):
            raise ValueError(f"{spec.name}: {k} is {'an' if want[0] in 'aeiou' else 'a'} {want}")
    return out


def can(row, via="web", cfg=None):
    """What the person can do with a waiting approval from where they are (`via`: web for a signed-in session, chat for
    their own chat app, token or oauth for an API key or an app, agent for MCP and the assistant, which only decline).
    A research plan is approved by the person only (edited in the web app), unless research.agents_skip_review is on
    in `cfg`."""
    pending = row.get("status") == "pending"
    intent = row.get("intent")
    if row.get("tool") == "research":
        skip = bool(((cfg or {}).get("research") or {}).get("agents_skip_review"))
        return {
            "approve": pending and (via in ("web", "chat") or skip),
            "decline": pending,
            "edit": pending and via == "web",
            "always": False,
        }
    if not intent:
        return {"approve": pending and via != "agent", "decline": pending, "edit": False, "always": False}
    person = via in ("web", "chat")
    return {
        "approve": pending and person,
        "decline": pending,
        "edit": pending and via == "web",
        "always": pending and via == "web" and intent.get("effect") == "out" and _studio("set_policy") is not None,
    }


def _take(db, aid, status, user):
    """Claim a waiting approval for one decision (two at once can't both carry it out): True when this one won."""
    return bool(
        db.rows(
            "UPDATE $r SET status = $s, decided_at = $t, decided_by = $u WHERE status = 'pending' RETURN id",
            r=R("approval", int(aid)),
            s=status,
            t=store.now(),
            u=user.get("email"),
        )
    )


def _depth(db, intent):
    try:
        d = (db.one("SELECT cause_depth FROM $r", r=R("automation_run", int(intent["run"]))) or {}).get("cause_depth")
    except Exception:  # noqa: BLE001 - runs aren't stored yet
        d = None
    return (d if isinstance(d, int) else 0) + 1


def decide(db, cfg, row, user, decision="approve", params=None, via="web"):
    """Decide an automation's approval: approve (perform its intent), decline, edit (new params, then perform) or
    always (perform, then let that node run on its own from now on). `user` is who decided; the intent acts for its
    person, as the automation. {status, result or error}; PermissionError when `via` can't make that pick, ValueError
    when it isn't waiting or the edit doesn't check."""
    if decision not in DECISIONS or decision == "sample":
        raise ValueError("an automation's action is approved, declined, edited or allowed from now on")
    allowed = can(row, via)
    if row.get("status") != "pending":
        raise ValueError("nothing to approve")
    if decision == "decline":
        if not _take(db, row["id"], "declined", user):
            raise ValueError("nothing to approve")
        db.q("UPDATE $r SET intent.status = 'declined'", r=R("approval", row["id"]))
        _record(db, row["intent"], "declined")
        return {"status": "declined"}
    if not allowed[decision]:
        if decision == "always" and row["intent"].get("effect") != "out":
            raise ValueError("only an action that reaches outside Lens or other people can be allowed from now on")
        if decision == "always" and _studio("set_policy") is None:
            raise ValueError("allowing an action from now on comes with saved automations: approve it this once")
        raise PermissionError(
            "approve an automation's action yourself: signed in to Lens, or with a yes in the chat it told you in"
            if via not in ("web", "chat")
            else "edit or allow an action from now on in Lens, on its card"
        )
    intent = dict(row["intent"])
    if decision == "edit":
        if params is None:
            raise ValueError("give the params to change")
        intent["params"] = edited(intent, params)
        intent["effect"] = actions.effect_of(actions.get(intent["action"]), intent["params"])
    person = actions.person(db, intent)
    if not _take(db, row["id"], "running", user):
        raise ValueError("nothing to approve")
    result, error = None, None
    try:
        who = actions.actor(db, intent)
        cause = {"automation": intent["automation"], "run": intent["run"], "depth": _depth(db, intent)}
        with (
            activity.scope(db, f"automation:{intent['automation']}", f"automation_run:{intent['run']}", cfg=cfg),
            events.caused_by(cause),
            graph_history.acting(
                actor=who["email"], via=who["id"], why=None if via == "chat" else f"approved by {user.get('email')}", approval=row["id"]
            ),
        ):
            result = actions.perform(db, cfg, {**intent, "status": "waiting"}, person)
    except Exception as e:  # noqa: BLE001 - recorded on the approval and the run
        error = f"{type(e).__name__}: {e}"[:500] if not isinstance(e, ValueError) else str(e)[:500]
    status = "failed" if error else "done"
    db.q(  # decided_at again: the approval.decided activity is read by it, once the outcome is known
        "UPDATE $r SET status = $s, result = $res, error = $e, intent = $i, decided_at = $t",
        r=R("approval", row["id"]),
        s=status,
        res=result,
        e=error,
        i={**intent, "status": status},
        t=store.now(),
    )
    _record(db, intent, status, result if not error else {"error": error})
    out = {"status": status, **({"error": error} if error else {"result": result})}
    if decision == "always" and not error:
        o = row.get("origin") or {}
        try:
            _studio("set_policy")(db, o["automation"], o["node"], "auto", user.get("email"))
            out["always"] = True
        except Exception as e:  # noqa: BLE001 - it was carried out; the node keeps asking
            out["always"], out["error"] = False, f"carried out, but it will still ask next time: {e}"[:500]
    return out


# ---------- the inbox ----------
def listing(db, account, status=None, origin=None, automation=None, chat=None, before=None, limit=50, space=None):
    """The person's approvals, newest first, `limit` at a time before the id `before`: those with `status`, from
    automations or chats (`origin`), from one automation, asked in one chat, or about one namespace (`space`)."""
    where, v = ["account = $a"], {"a": account, "limit": max(1, min(int(limit or 50), 200))}
    if space is not None:
        where.append("space = $sp")
        v["sp"] = int(space)
    if status:
        where.append("status = $s")
        v["s"] = status
    if origin == "automation":
        where.append("origin != NONE AND origin != NULL")
    elif origin == "chat":
        where.append("(origin = NONE OR origin = NULL)")
    if automation is not None:
        where.append("origin.automation = $au")
        v["au"] = int(automation)
    if chat is not None:
        where.append("chat = $c")
        v["c"] = int(chat)
    if before is not None:
        where.append("id < $b")
        v["b"] = R("approval", int(before))
    return db.rows(f"SELECT {FIELDS} FROM approval WHERE {' AND '.join(where)} ORDER BY id DESC LIMIT $limit", **v)


def brief(a, web=""):
    """An approval as an agent reads it (MCP's and the assistant's `approvals`): what it would do and why, where the
    person decides it (`url`, on the web app's address `web`), and what the agent may do (decline it). The assistant's
    own proposals keep their args out; an intent's credential headers show as ••••."""
    intent, why, o = shown(a.get("intent")) or {}, a.get("why") or {}, a.get("origin") or {}
    out = {
        "id": a["id"],
        "status": a.get("status"),
        "summary": a.get("summary"),
        "preview": a.get("preview"),
        "from": "automation" if o else "chat",
        "automation": o.get("automation"),
        "automation_name": a.get("automation"),
        "run": o.get("run"),
        "node": o.get("node"),
        "chat": a.get("chat"),
        "action": intent.get("action"),
        "effect": intent.get("effect"),
        "params": intent.get("params"),
        "why": {k: why[k] for k in ("activity", "kind", "summary") if why.get(k)} or None,
        "created_at": a.get("created_at"),
        "expires_at": a.get("expires_at"),
        "decided_by": a.get("decided_by"),
        "decided_at": a.get("decided_at"),
        "error": a.get("error"),
        "url": f"{web}/approvals?id={a['id']}",
        "can": can(a, "agent"),
    }
    return {k: v for k, v in out.items() if v is not None}


def count(db, account):
    """How many approvals wait for the person: all of them, and those from automations."""
    rows = db.rows(
        "SELECT (origin != NONE AND origin != NULL) AS auto, count() AS n FROM approval "
        "WHERE account = $a AND status = 'pending' GROUP BY auto",
        a=account,
    )
    auto = sum(int(r["n"]) for r in rows if r.get("auto"))
    return {"waiting": sum(int(r["n"]) for r in rows), "automations": auto}


# ---------- expiry ----------
def sweep(db, cfg, now=None):
    """Automation approvals past their expires_at become expired (nothing runs; the run says so), and one still running
    an hour on failed; at most once an hour per process (routines.run_due calls it every 30 seconds). How many expired,
    or None when it didn't look."""
    t = time.monotonic()
    if now is None and _swept["at"] and t - _swept["at"] < SWEEP_SECONDS:
        return None
    _swept["at"] = t
    now = now or dt.datetime.now(dt.timezone.utc)
    gone = db.rows(
        "UPDATE approval SET status = 'expired', decided_at = $n, intent.status = 'expired' "
        "WHERE status = 'pending' AND expires_at != NONE AND expires_at < $n RETURN record::id(id) AS id, intent",
        n=_iso(now),
    )
    for a in gone:
        if a.get("intent"):
            _record(db, a["intent"], "expired")
    stuck = db.rows(
        "UPDATE approval SET status = 'failed', error = 'the server stopped while it was carried out', decided_at = $n "
        "WHERE status = 'running' AND decided_at < $c RETURN record::id(id) AS id, intent",
        n=_iso(now),
        c=_iso(now - dt.timedelta(seconds=STUCK_SECONDS)),
    )
    for a in stuck:
        if a.get("intent"):
            _record(db, a["intent"], "failed", {"error": "the server stopped while it was carried out"})
    return len(gone)
