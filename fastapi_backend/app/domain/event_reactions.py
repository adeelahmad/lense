"""Reactions on the timeline (docs/timeline.md, Reactions and comments): each person puts an emoji on an activity, as
on a post in a feed, and everyone who sees the activity sees the reactions on it, with how many gave each.

One row per person and activity: its id is `event_reaction:<hash of account and activity>` (the pair stays unique
that way, as with event_mark), holding the emojis they gave and when they last changed them, with an index on
`activity` to count a page's reactions in one query and on `account` to drop a person's when they go. A row with no
emojis left is deleted. The emojis are a fixed palette, so the page shows a picker rather than a keyboard, and every
client can draw them.

Giving a reaction logs a `reaction.added` activity (noisy, so the timeline leaves it out unless asked), with the
activity it is on as its object: an automation or an agent can act on it ("when someone 👍s a photo, share it").
"""

from __future__ import annotations

import hashlib

from . import events, store

R = store.R
EMOJIS = ("👍", "❤️", "🎉", "👀", "😂", "😮", "😢", "🙏")
LABELS = {
    "👍": "thumbs up",
    "❤️": "heart",
    "🎉": "party",
    "👀": "eyes",
    "😂": "laughing",
    "😮": "surprised",
    "😢": "sad",
    "🙏": "thanks",
}

events.kind(
    "reaction.added",
    label="Reaction added",
    source="log",
    as_type="Like",
    object_type="Activity",
    fields={
        "object": {
            "type": "object",
            "properties": {
                "id": {"type": "string", "title": "The activity it is on", "description": "lens:activity/<its id>"},
                "name": {"type": "string", "title": "That activity's summary"},
                "url": {"type": "string", "title": "Link", "description": "the item on the timeline"},
                "emoji": {"type": "string", "title": "The emoji", "enum": list(EMOJIS)},
                "activity": {"type": "string", "title": "The activity's id"},
            },
        }
    },
    noisy=True,
    sample={
        "id": "log-41",
        "type": "Like",
        "kind": "reaction.added",
        "published": "2026-10-18T09:12:00.000Z",
        "actor": {"type": "Person", "id": "lens:account/1", "name": "Adeel"},
        "object": {
            "type": "Activity",
            "id": "lens:activity/create-recording-7",
            "name": "Work Gmail received “Parents' evening”",
            "url": "/timeline?item=create-recording-7",
            "emoji": "👍",
            "activity": "create-recording-7",
        },
        "summary": "Adeel reacted 👍 to “Work Gmail received “Parents' evening””",
        "space": 3,
        "tag": [],
        "lens": {"source": "log", "pos": ["2026-10-18T09:12:00.000Z", 41], "untrusted": ["object.name"], "cause": None},
    },
    untrusted=("object.name",),
)


def _ref(account, activity):
    return R("event_reaction", hashlib.sha1(f"{account}|{activity}".encode()).hexdigest())


def check(emoji):
    """The emoji, from the palette; ValueError for one that isn't in it."""
    emoji = str(emoji or "").strip()
    if emoji not in EMOJIS:
        raise ValueError(f"the reactions are {' '.join(EMOJIS)}")
    return emoji


def _tally(rows, account):
    """{activity: [{emoji, count, mine}]} from rows of (activity, account, emojis), each list in the palette's order."""
    counts: dict[str, dict[str, int]] = {}
    mine: dict[str, set[str]] = {}
    for r in rows:
        for e in r.get("emojis") or []:
            if e not in EMOJIS:
                continue
            counts.setdefault(r["activity"], {})[e] = counts.get(r["activity"], {}).get(e, 0) + 1
            if account is not None and r.get("account") == account:
                mine.setdefault(r["activity"], set()).add(e)
    return {aid: [{"emoji": e, "count": n[e], "mine": e in mine.get(aid, set())} for e in EMOJIS if e in n] for aid, n in counts.items()}


def of(db, account, ids):
    """{activity id: [{emoji, count, mine}]} of everyone's reactions on these activities, in one query; an activity
    with none isn't in it. `mine`: the person asking gave that one."""
    ids = sorted({i for i in ids if isinstance(i, str)})
    if not ids:
        return {}
    rows = db.rows("SELECT activity, account, emojis FROM event_reaction WHERE activity IN $ids", ids=ids)
    return _tally(rows, account)


def react(db, account, activity, emoji, on=True):
    """Give the emoji to the activity (or take it back with `on` false), for `account`; the reactions on it after,
    [{emoji, count, mine}]. Whether anything changed is the second value, so the caller logs it only then."""
    emoji = check(emoji)
    row = db.one("SELECT emojis FROM $r", r=_ref(account, activity))
    have = [e for e in (row or {}).get("emojis") or [] if e in EMOJIS]
    want = [e for e in EMOJIS if (e in have and e != emoji) or (e == emoji and on)]
    changed = want != have
    if changed and want:
        db.q(
            "UPSERT $r SET account = $a, activity = $i, emojis = $e, at = $t",
            r=_ref(account, activity),
            a=account,
            i=activity,
            e=want,
            t=store.now(),
        )
    elif changed:
        db.q("DELETE $r", r=_ref(account, activity))
    return of(db, account, [activity]).get(activity, []), changed


def log(db, a, actor, emoji):
    """Log a reaction.added for `actor`'s emoji on activity `a`, seen by whoever sees `a`."""
    lens = a.get("lens") or {}
    return events.emit(
        db,
        "reaction.added",
        actor=actor,
        obj={
            "type": "Activity",
            "id": f"lens:activity/{a['id']}",
            "name": a.get("summary") or a["id"],
            "url": f"/timeline?item={a['id']}",
            "emoji": emoji,
            "activity": a["id"],
        },
        space=a.get("space"),
        spaces=lens.get("spaces"),
        audience=lens.get("audience"),
        owner=lens.get("owner"),
        summary=f"{actor['name']} reacted {emoji} to “{(a.get('summary') or a['id'])[:200]}”",
    )
