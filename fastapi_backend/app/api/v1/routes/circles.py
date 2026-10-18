"""Circles of trust (app/domain/circles.py, docs/circles.md): the people someone shares with on this Lens, and sharing
an activity or a resource with them. A person's circles and shares are their own: nobody else reads them."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import Acl, Cfg, CurrentUser, Db, Principal, Writer, domain_errors
from app.api.v1.routes import activities as activity_routes
from app.domain import auth, circles, circles_ask
from app.schemas.circles import (
    AgentAnswer,
    AskIn,
    Circle,
    CircleCreate,
    CircleList,
    CircleShare,
    CircleUpdate,
    Direction,
    Person,
    Shared,
    ShareIn,
    ShareUndone,
    WhenFree,
    WhenFreeIn,
)
from app.schemas.common import Ok

router = APIRouter(prefix="/circles", tags=["circles"])


def me(user: Principal) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "admin": user.admin}


@router.get("")
def list_circles(user: CurrentUser, db: Db) -> CircleList:
    """Your circles, each with its members, and the usual circles you haven't made yet."""
    mine = circles.circles(db, user.id)
    have = {c["name"].lower() for c in mine}
    return CircleList(circles=[Circle.model_validate(c) for c in mine], suggested=[n for n in circles.SUGGESTED if n.lower() not in have])


@router.get("/people")
def list_circle_people(user: CurrentUser, db: Db) -> list[Person]:
    """The people you can put in a circle: those you share a namespace with, and those who have you in a circle of
    theirs (everyone, for an admin)."""
    return [Person.model_validate(p) for p in circles.people(db, me(user))]


@router.post("", status_code=201)
def create_circle(body: CircleCreate, user: Writer, db: Db) -> Circle:
    """A new circle of yours. Audited as `circle.create`."""
    with domain_errors():
        cid = circles.create(db, me(user), body.name, body.members, body.lets, body.about)
    auth.audit(db, user.as_audit(), "circle.create", f"circle:{cid}", {"members": len(body.members)})
    return Circle.model_validate(circles.get(db, cid, user.id))


@router.get("/shares")
def list_circle_shares(
    user: CurrentUser, db: Db, direction: Direction = "received", limit: int = Query(50, ge=1, le=200)
) -> list[CircleShare]:
    """What was shared with you (received), or what you shared (sent), newest first."""
    return [CircleShare.model_validate(s) for s in circles.shares(db, me(user), direction, limit)]


@router.post("/shares", status_code=201)
def share_with_circles(body: ShareIn, user: Writer, acl: Acl, db: Db, cfg: Cfg) -> Shared:
    """Share an activity or a resource you can see with your circles, or people in them, and a note: each of them
    sees it in their own timeline (share.received), and you see it in yours (share.sent). `with_item` also gives those
    who can't open the resource permission on it. Audited as `circle.share`."""
    with domain_errors():
        try:
            out = circles.share(
                db,
                cfg,
                me(user),
                activity=body.activity,
                recording=body.recording,
                circles_=body.circles,
                people_=body.people,
                note=body.note,
                with_item=body.with_item,
                via="web" if user.via == "access" else user.via,
                who=activity_routes.viewer(db, user, acl),
            )
        except PermissionError as e:
            raise HTTPException(403, str(e)) from None
    auth.audit(
        db,
        user.as_audit(),
        "circle.share",
        f"circle_share:{out['id']}",
        {"to": len(out["people"]), "with_item": out["with_item"], "what": body.activity or f"recording:{body.recording}"},
    )
    return Shared.model_validate(out)


@router.delete("/shares/{sid}")
def undo_circle_share(sid: int, user: Writer, db: Db) -> ShareUndone:
    """Take back a share of yours: it leaves everyone's timeline, and the permissions it gave are taken back."""
    with domain_errors():
        out = circles.undo(db, me(user), sid)
    auth.audit(db, user.as_audit(), "circle.unshare", f"circle_share:{sid}", {"taken_back": out["taken_back"]})
    return ShareUndone.model_validate(out)


@router.post("/ask")
def ask_their_agent(body: AskIn, user: CurrentUser, db: Db, cfg: Cfg) -> AgentAnswer:
    """Ask someone's agent a question, answered only from the namespaces they let a circle of theirs with you ask about
    (`lets: questions` and `about`), citing what it used. They see the question and the answer in their own timeline
    (agent.asked)."""
    with domain_errors():
        out = circles_ask.ask(db, cfg, me(user), body.person, body.question, via="web" if user.via == "access" else user.via)
    return AgentAnswer.model_validate(out)


@router.post("/when-free")
def when_free(body: WhenFreeIn, user: CurrentUser, db: Db) -> WhenFree:
    """When people in your circles are busy in a window, for those who let you see it (a circle of theirs with
    `lets: free_busy`), and when you and all of them are free: only when, never what. Each person asked sees it in
    their own timeline (agent.asked)."""
    with domain_errors():
        out = circles_ask.find_time(
            db,
            me(user),
            body.people,
            body.start,
            body.end,
            minutes=body.minutes,
            tz=body.tz,
            day_start=body.day_start,
            day_end=body.day_end,
            via="web" if user.via == "access" else user.via,
        )
    return WhenFree.model_validate(out)


@router.get("/{cid}")
def get_circle(cid: int, user: CurrentUser, db: Db) -> Circle:
    with domain_errors():
        return Circle.model_validate(circles.get(db, cid, user.id))


@router.patch("/{cid}")
def update_circle(cid: int, body: CircleUpdate, user: Writer, db: Db) -> Circle:
    """Rename a circle of yours, set who is in it, add and take people out, or set what they may ask your agent
    (`lets`: free_busy lets them see when you're free)."""
    with domain_errors():
        out = circles.update(db, me(user), cid, body.name, body.members, body.add, body.remove, body.lets, body.about)
    auth.audit(db, user.as_audit(), "circle.update", f"circle:{cid}", None)
    return Circle.model_validate(out)


@router.delete("/{cid}")
def delete_circle(cid: int, user: Writer, db: Db) -> Ok:
    """Delete a circle of yours. What you shared with it stays shared; take a share back with DELETE /circles/shares/{id}."""
    with domain_errors():
        circles.delete(db, me(user), cid)
    auth.audit(db, user.as_audit(), "circle.delete", f"circle:{cid}", None)
    return Ok()
