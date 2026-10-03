"""People and roles: admins manage accounts; a namespace's owners manage who else can use it."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, AdminReader, AdminWriter, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, store
from app.schemas.common import Created, Ok
from app.schemas.users import Member, MemberSet, UserCreate, UserUpdate, UserWithRoles

router = APIRouter(tags=["users"])


@router.get("/users")
def list_users(user: AdminReader, db: Db) -> list[UserWithRoles]:
    """Every account, with its role in each namespace."""
    people = db.rows(
        "SELECT record::id(id) AS id, email, name, admin, disabled, created_at, last_login_at, pw != NONE AS password FROM account ORDER BY id"
    )
    keys: dict[int, int] = defaultdict(int)
    for a in db.values("SELECT VALUE account FROM passkey"):
        keys[a] += 1
    roles: dict[int, dict[str, str]] = defaultdict(dict)
    names = store.space_names(db)
    for m in db.rows("SELECT account, space, role FROM membership"):
        roles[m["account"]][names.get(m["space"], str(m["space"]))] = m["role"]
    return [UserWithRoles.model_validate({**p, "roles": roles.get(p["id"], {}), "passkeys": keys[p["id"]]}) for p in people]


@router.post("/users")
def create_user(body: UserCreate, user: AdminWriter, db: Db, cfg: Cfg) -> Created:
    """A new account. Without a password (the only way where passwords are off), send them a sign-in link
    (POST /users/{uid}/signin-link) to add a passkey."""
    if body.password is not None and not auth.passwords_on(cfg):
        raise HTTPException(400, "passwords are turned off here; create the account and send a sign-in link")
    with domain_errors():
        uid = auth.create_account(db, body.email, body.password, body.name, body.admin)
    auth.audit(db, user.as_audit(), "user.create", f"account:{uid}")
    return Created(id=uid)


@router.patch("/users/{uid}")
def update_user(uid: int, body: UserUpdate, user: AdminWriter, db: Db, cfg: Cfg) -> Ok:
    """Rename, promote or demote, disable, or set a new password (which signs the person out everywhere; only where
    passwords are on)."""
    if body.password and not auth.passwords_on(cfg):
        raise HTTPException(400, "passwords are turned off here; send a sign-in link instead")
    if uid == user.id and (body.admin is False or body.disabled):
        raise HTTPException(400, "you can't remove your own admin rights or disable yourself")
    if not auth.get_account(db, uid):
        raise HTTPException(404, "not found")
    with domain_errors():
        auth.update_account(db, uid, body.name, body.admin, body.disabled, body.password)
    auth.audit(db, user.as_audit(), "user.update", f"account:{uid}", body.model_dump(exclude_unset=True, exclude={"password"}))
    return Ok()


@router.get("/namespaces/{name}/members")
def list_members(name: str, user: CurrentUser, acl: Acl, db: Db) -> list[Member]:
    """Who has a role in this namespace (owners only)."""
    return auth.members(db, acl.namespace(name, "owner"))


@router.put("/namespaces/{name}/members")
def set_member(name: str, body: MemberSet, user: Writer, acl: Acl, db: Db) -> Ok:
    """Give someone a role in this namespace, change it, or (role null) remove them. Owners only."""
    sid = acl.namespace(name, "owner")
    acct = auth.find_account(db, body.email) if body.email else auth.get_account(db, body.account or 0)
    if not acct:
        raise HTTPException(404, "no such person")
    with domain_errors():
        auth.set_role(db, acct["id"], sid, body.role)
    auth.audit(db, user.as_audit(), "member.set", f"space:{sid}", {"account": acct["id"], "role": body.role})
    return Ok()
