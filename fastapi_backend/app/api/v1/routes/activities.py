"""The stream of activities (docs/automations.md, Contract): what happened in Lens, newest first, from every source,
as Activity Streams 2.0 activities; one activity by its id; and the kinds of activity there are. People see what their
namespaces and each activity's audience allow, and mark what they see reviewed, important or hidden for themselves
(docs/timeline.md)."""

from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Access, Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.api.v1.routes.comments import _comments_out
from app.domain import auth, comment_agent, comments, event_marks, event_mutes, event_reactions, events, events_content, for_you, store
from app.schemas.activities import (
    ActivityKind,
    ActivityMark,
    ActivityPage,
    MarkIn,
    MarksIn,
    MarksOut,
    Mute,
    MuteIn,
    MutesOut,
    ReactIn,
    ReactionsOut,
    StreamActivity,
)
from app.schemas.comments import ActivityCommentCreate, Comment, CommentUpdate
from app.schemas.common import Ok

router = APIRouter(prefix="/activities", tags=["activities"])
# activities come back as the stream has them: fields a source leaves out stay out, rather than null


def viewer(db: Any, user: Principal, acl: Access) -> dict[str, Any]:
    """Who reads the stream: an admin signed in or with their own key reads everything; an app an admin gave access to
    reads every namespace but not what is for admins alone; anyone else reads their namespaces."""
    if user.admin and user.via != "oauth":
        return events.viewer(user.id, None, admin=True)
    spaces = acl.readable()
    return events.viewer(user.id, set(store.space_names(db)) if spaces is None else spaces)


def _many(values: list[str] | None) -> list[str] | None:
    """Repeated or comma-separated query values."""
    out = [v.strip() for x in values or [] for v in x.split(",") if v.strip()]
    return out or None


def filters(
    acl: Access,
    kinds: list[str] | None = None,
    actor: list[str] | None = None,
    namespace: list[str] | None = None,
    tag: str | None = None,
    since: str | None = None,
    until: str | None = None,
    q: str | None = None,
    where: Any = None,
) -> dict[str, Any]:
    """The timeline filters for events.page: namespaces by name (404 for one that isn't there), `where` as a match
    tree or its JSON."""
    if isinstance(where, str) and where.strip():
        try:
            where = json.loads(where)
        except ValueError:
            raise HTTPException(
                400, 'where is a match tree as JSON, like {"all": [{"path": "kind", "op": "equals", "value": "asset.added"}]}'
            ) from None
    names = _many(namespace)
    return {
        k: v
        for k, v in {
            "kinds": _many(kinds),
            "actor": _many(actor),
            "spaces": [acl.nsid(n) for n in names] if names else None,
            "tag": tag,
            "since": since,
            "until": until,
            "q": q,
            "where": where or None,
        }.items()
        if v not in (None, "")
    }


def timeline(
    db: Any,
    cfg: dict[str, Any],
    who: dict[str, Any],
    flt: dict[str, Any],
    before: str | None,
    limit: int,
    marked: dict[str, bool] | None = None,
    order: str = "latest",
) -> dict[str, Any]:
    """A page of the timeline with the namespaces it names, the viewer's own marks on it, everyone's reactions and
    how many comments each item has; `marked` ({reviewed, important, hidden}: true or false) keeps the activities
    whose marks are so, a muted actor's or kind's counting as hidden (event_mutes.py). `order` for_you reorders the
    newest items by the viewer's own signals (for_you.py), with `why` by id."""
    if order not in for_you.ORDERS:
        raise HTTPException(422, f"order is {' or '.join(for_you.ORDERS)}")
    with domain_errors():
        keep = event_mutes.keeper(db, who["account"], marked)
        if order == "for_you":
            page = for_you.page(db, cfg, who, flt, before, limit, keep)
        else:
            page = events.page(db, cfg, who, before, flt, limit, keep=keep)
    names = store.space_names(db)
    used = {s for a in page["items"] for s in events.spaces_of(a)}
    ids = [a["id"] for a in page["items"]]
    return {
        **page,
        "namespaces": {str(s): names[s] for s in sorted(used) if s in names},
        "marks": event_marks.of(db, who["account"], ids),
        "reactions": event_reactions.of(db, who["account"], ids),
        "comments": comments.counts(db, ids),
    }


@router.get("", response_model_exclude_unset=True)
def list_activities(
    user: CurrentUser,
    acl: Acl,
    db: Db,
    cfg: Cfg,
    kinds: list[str] | None = Query(None, description="kinds of activity, like asset.added (the kinds under them count too)"),
    actor: list[str] | None = Query(None, description="who did it, by the actor's id, like lens:account/1"),
    namespace: list[str] | None = Query(None, description="only these namespaces, by name"),
    tag: str | None = Query(None, description="tagged with this entity, by its id (lens:entity/9) or name"),
    since: str | None = Query(None, description="published after this time (ISO 8601)"),
    until: str | None = Query(None, description="published at or before this time (ISO 8601)"),
    q: str | None = Query(None, max_length=200, description="words in the summary"),
    where: str | None = Query(None, max_length=20000, description="a match tree on the activity, as JSON"),
    reviewed: bool | None = Query(None, description="only those you marked reviewed (true) or haven't (false)"),
    important: bool | None = Query(None, description="only those you marked important (true) or haven't (false)"),
    hidden: bool | None = Query(None, description="only those you hid (true) or haven't (false)"),
    before: str | None = Query(None, max_length=4000, description="the next of the page before"),
    limit: int = Query(50, ge=1, le=200),
    order: Literal["latest", "for_you"] = Query(
        "latest",
        description="latest: newest first; for_you: the 200 newest from `before` on, the `limit` of them that your own "
        "reactions, comments and marks say you care for most first, each with `why`; `next` moves past all 200",
    ),
) -> ActivityPage:
    """What happened in Lens, newest first: new assets and everything else the stream holds, each an Activity Streams
    2.0 activity with a Lens kind. Give `next` as `before` for the page after. Noisy kinds (like sensor readings) are
    left out unless named in `kinds`. Text from outside Lens is listed in each activity's `lens.untrusted`. `marks`
    has your own marks on the page's activities; `reviewed`, `important` and `hidden` filter by them; what you muted
    (`/activities/mutes`) is left out unless `hidden=true`. `order=for_you` puts what you react to, comment on and
    mark important first, and what you hid last (docs/timeline.md, For you)."""
    flt = filters(acl, kinds, actor, namespace, tag, since, until, q, where)
    marked = event_marks.want(reviewed, important, hidden)
    return ActivityPage(**timeline(db, cfg, viewer(db, user, acl), flt, before, limit, marked, order))


@router.get("/kinds", response_model_exclude_unset=True)
def list_activity_kinds(user: CurrentUser) -> list[ActivityKind]:
    """Every kind of activity: its label, the source that makes it, its parent and children (a trigger on a kind
    matches the kinds under it), a JSON Schema of its activities with each field's title, the fields holding text
    from outside Lens, and a sample."""
    return [ActivityKind(**k) for k in events.kinds()]


def mark(db: Any, cfg: dict[str, Any], who: dict[str, Any], ids: list[str], flags: dict[str, Any]) -> dict[str, Any]:
    """Set the viewer's own marks on the activities among `ids` they can see: {marks, unknown}."""
    with domain_errors():
        flags = event_marks.check(flags)
        if len(ids) > event_marks.MAX_IDS:
            raise ValueError(f"mark at most {event_marks.MAX_IDS} activities at once")
        seen = events.get_many(db, cfg, ids, who)
        marks = event_marks.mark(db, who["account"], [i for i in ids if i in seen], flags)
    return {"marks": marks, "unknown": [i for i in dict.fromkeys(ids) if i not in seen]}


@router.post("/mark", response_model_exclude_unset=True)
def mark_activities(body: MarksIn, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> MarksOut:
    """Mark activities reviewed, important or hidden, or clear those marks, for yourself alone: each mark given true or
    false, one left out stays as it is. Up to 500 at once; `unknown` lists the ids there are no activities of, or
    that you can't see."""
    return MarksOut(**mark(db, cfg, viewer(db, user, acl), body.ids, body.model_dump(exclude={"ids"})))


@router.get("/mutes")
def list_activity_mutes(user: CurrentUser, db: Db) -> MutesOut:
    """Your mutes: the sources, people and kinds whose activities the timeline leaves out for you, each for good or
    until a time (docs/timeline.md, Muting). Nobody else's timeline changes."""
    return MutesOut(mutes=[Mute(**m) for m in event_mutes.of(db, user.id)])


@router.post("/mute")
def mute_activities(body: MuteIn, user: Writer, db: Db) -> MutesOut:
    """Mute a source or person (`actor`, by id) or a kind of activity (`kind`, the kinds under it too) for yourself,
    for good or `until` a time (snooze it for a day), or lift the mute with `on` false. Muting hides, it never
    deletes: the activities stay, still trigger automations and still show to everyone else, and `hidden=true` on
    the timeline shows them to you. The answer is your mutes now."""
    with domain_errors():
        if body.on:
            mutes = event_mutes.mute(db, user.id, body.actor, body.kind, body.until, body.name)
        else:
            mutes = event_mutes.unmute(db, user.id, body.actor, body.kind)
    return MutesOut(mutes=[Mute(**m) for m in mutes])


@router.get("/{activity_id}", response_model_exclude_unset=True)
def get_activity(activity_id: str, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> StreamActivity:
    """One activity by its id, like create-recording-7."""
    a = events.get(db, cfg, activity_id, viewer(db, user, acl))
    if not a:
        raise HTTPException(404, "not found")
    return StreamActivity(**a)


@router.post("/{activity_id}/mark")
def mark_activity(activity_id: str, body: MarkIn, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> ActivityMark:
    """Mark one activity reviewed, important or hidden, or clear a mark, for yourself alone: each mark given true or
    false, one left out stays as it is. The answer is your marks on it now."""
    got = mark(db, cfg, viewer(db, user, acl), [activity_id], body.model_dump())
    if got["unknown"]:
        raise HTTPException(404, "not found")
    return ActivityMark(**got["marks"][activity_id])


# ---------- reactions and comments: a feed everyone who sees an activity talks on (docs/timeline.md) ----------
def _seen(db: Any, cfg: dict[str, Any], activity_id: str, who: dict[str, Any]) -> dict[str, Any]:
    a = events.get(db, cfg, activity_id, who)
    if not a:
        raise HTTPException(404, "not found")
    return a


def _person(db: Any, user: Principal, space: int | None) -> dict[str, Any]:
    """The person as an actor, as the stream names people."""
    return events_content.actors(db, [(user.id, space)])[(user.id, space)]


def react(
    db: Any, cfg: dict[str, Any], who: dict[str, Any], user: Principal, activity_id: str, emoji: str, on: bool = True
) -> list[dict[str, Any]]:
    """Give `emoji` to the activity (or take it back) for `user`, logging reaction.added when it is new; the reactions
    on it now."""
    a = _seen(db, cfg, activity_id, who)
    with domain_errors():
        reactions, changed = event_reactions.react(db, user.id, a["id"], emoji, on)
    if changed and on:
        event_reactions.log(db, a, _person(db, user, a.get("space")), emoji)
    return reactions


@router.post("/{activity_id}/react")
def react_to_activity(activity_id: str, body: ReactIn, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> ReactionsOut:
    """React to an activity with an emoji from the palette (👍 ❤️ 🎉 👀 😂 😮 😢 🙏), or with `on` false take yours
    back. Everyone who sees the activity sees the reactions; a new one is a `reaction.added` activity automations
    can act on. The answer is the reactions on it now."""
    return ReactionsOut(reactions=react(db, cfg, viewer(db, user, acl), user, activity_id, body.emoji, body.on))


def _rank(acl: Access, user: Principal, a: dict[str, Any]) -> int:
    """What the person may do with comments on the activity: an owner for admins, else their role in its namespace
    (at least a viewer, since they see it)."""
    if user.admin:
        return auth.ROLES["owner"]
    return max(acl.rank_in(a["space"], None) if a.get("space") is not None else 0, auth.ROLES["viewer"])


def _comment(db: Any, activity_id: str, cid: int) -> dict[str, Any]:
    try:
        c = comments.get(db, cid)
    except KeyError:
        raise HTTPException(404, "not found") from None
    rid = comments.recording_of(activity_id)
    if (c.get("recording") != rid) if rid is not None else (c.get("activity") != activity_id):
        raise HTTPException(404, "not found")
    return c


def comments_on(db: Any, cfg: dict[str, Any], who: dict[str, Any], user: Principal, acl: Access, activity_id: str) -> list[Comment]:
    a = _seen(db, cfg, activity_id, who)
    return _comments_out(db, comments.on_activity(db, a["id"]), _rank(acl, user, a), user)


@router.get("/{activity_id}/comments")
def list_activity_comments(activity_id: str, user: CurrentUser, acl: Acl, db: Db, cfg: Cfg) -> list[Comment]:
    """The activity's comments, threaded: each thread followed by its replies, the earliest first. On an asset's
    arrival (`create-recording-<id>`) they are the asset's own comments, the same ones its page shows."""
    return comments_on(db, cfg, viewer(db, user, acl), user, acl, activity_id)


def comment(
    db: Any, cfg: dict[str, Any], who: dict[str, Any], user: Principal, acl: Access, activity_id: str, text: str, parent: int | None
) -> Comment:
    """Comment on the activity, or with `parent` reply on a thread; the comment."""
    a = _seen(db, cfg, activity_id, who)
    lens = a.get("lens") or {}
    with domain_errors():
        cid = comments.create(
            db,
            comments.recording_of(a["id"]),
            a.get("space"),
            user.id,
            text,
            parent=parent,
            activity=a["id"],
            about=a.get("summary"),
            audience=lens.get("audience"),
            owner=lens.get("owner"),
        )
    comment_agent.wake(db, cfg, cid, text)
    return _comments_out(db, [comments.get(db, cid)], _rank(acl, user, a), user)[0]


@router.post("/{activity_id}/comments")
def comment_on_activity(activity_id: str, body: ActivityCommentCreate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Comment:
    """Comment on an activity, or with `parent` reply on a thread (a reply to a reply goes on the thread too). Anyone
    who sees the activity can, up to 1,000 each; everyone who sees it reads them, and each one is a `comment.added`
    activity. On an asset's arrival the comment goes on the asset itself. A comment that tags @lens gets the agent's
    answer on the thread."""
    return comment(db, cfg, viewer(db, user, acl), user, acl, activity_id, body.text, body.parent)


@router.patch("/{activity_id}/comments/{cid}")
def update_activity_comment(activity_id: str, cid: int, body: CommentUpdate, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Comment:
    """Change its text (its writer only), or resolve or reopen its thread (`resolved`, on the thread's first comment:
    its writer, or an editor of the namespace)."""
    a = _seen(db, cfg, activity_id, viewer(db, user, acl))
    c = _comment(db, activity_id, cid)
    rank = _rank(acl, user, a)
    if body.text is None and body.resolved is None:
        raise HTTPException(400, "change the text, or resolve or reopen the thread")
    if body.text is not None and (c["account"] != user.id or c.get("author") == "assistant"):
        raise HTTPException(403, "only its writer can change a comment")
    if body.resolved is not None and c["account"] != user.id and rank < auth.ROLES["editor"]:
        raise HTTPException(403, "only its writer or an editor of the namespace can resolve a thread")
    with domain_errors():
        if body.text is not None:
            comments.update(db, cid, body.text)
        if body.resolved is not None and body.resolved != bool(c.get("resolved")):
            comments.resolve(db, cid, user.id, body.resolved)
            auth.audit(
                db, user.as_audit(), "comment.resolve" if body.resolved else "comment.reopen", f"activity:{a['id']}", {"comment": cid}
            )
    return _comments_out(db, [comments.get(db, cid)], rank, user)[0]


@router.delete("/{activity_id}/comments/{cid}")
def delete_activity_comment(activity_id: str, cid: int, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Ok:
    """Delete it: its writer, or an owner of the namespace. A thread's first comment takes its replies with it.
    Audited (`comment.delete`)."""
    a = _seen(db, cfg, activity_id, viewer(db, user, acl))
    c = _comment(db, activity_id, cid)
    if c["account"] != user.id and _rank(acl, user, a) < auth.ROLES["owner"]:
        raise HTTPException(403, "only its writer or an owner of the namespace can delete it")
    replies = comments.delete(db, cid)
    auth.audit(
        db,
        user.as_audit(),
        "comment.delete",
        f"activity:{a['id']}",
        {"comment": cid, "writer": c["account"] == user.id, "replies": replies},
    )
    return Ok()
