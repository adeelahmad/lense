"""People and roles: admins manage accounts; a namespace's owners manage who else can use it."""

from __future__ import annotations

from collections import defaultdict

from fastapi import APIRouter, HTTPException

from app.api.deps import Acl, AdminReader, AdminWriter, Cfg, CurrentUser, Db, Writer, domain_errors
from app.domain import auth, roles, store
from app.schemas.common import Created, Ok
from app.schemas.users import (
    Member,
    MemberSet,
    RoleCreate,
    RoleOut,
    RolePermission,
    Roles,
    RoleUpdate,
    UserCreate,
    UserUpdate,
    UserWithRoles,
)

router = APIRouter(tags=["users"])


@router.get("/users")
def list_users(user: AdminReader, db: Db) -> list[UserWithRoles]:
    """Every account, with its role in each namespace."""
    people = db.rows(
        "SELECT record::id(id) AS id, email, name, admin, disabled, created_at, last_login_at, pw != NONE AS password FROM account "
        "WHERE removed_at = NONE ORDER BY id"
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
    target = auth.get_account(db, uid)
    if not target or target.get("removed_at"):
        raise HTTPException(404, "not found")
    with domain_errors():
        auth.update_account(db, uid, body.name, body.admin, body.disabled, body.password)
    auth.audit(db, user.as_audit(), "user.update", f"account:{uid}", body.model_dump(exclude_unset=True, exclude={"password"}))
    return Ok()


@router.delete("/users/{uid}")
def remove_user(uid: int, user: AdminWriter, db: Db, cfg: Cfg, lose_vaults: bool = False) -> Ok:
    """Remove someone for good (docs/authentication.md#removing-someone): their sign-ins, roles, permissions and
    private things go; what they shared stays under their name, and their email can be invited again. Disabling
    (PATCH) is the reversible way. Refused (409) for the account Matterbridge answers as, and when their passkeys are
    the only way into a vault, unless lose_vaults=true. Audited as `user.remove`."""
    if uid == user.id:
        raise HTTPException(400, "you can't remove yourself")
    u = auth.get_account(db, uid)
    if not u or u.get("removed_at"):
        raise HTTPException(404, "not found")
    if ((cfg.get("bridge") or {}).get("account") or "").strip().lower() == u["email"]:
        raise HTTPException(409, "Matterbridge answers as this account: choose another one in Settings first")
    try:
        auth.remove_account(db, uid, lose_vaults)
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "user.remove", f"account:{uid}", {"email": u["email"]})
    return Ok()


@router.get("/namespaces/{name}/members")
def list_members(name: str, user: CurrentUser, acl: Acl, db: Db) -> list[Member]:
    """Who has a role in this namespace (owners only)."""
    return auth.members(db, acl.namespace(name, "members"))


@router.put("/namespaces/{name}/members")
def set_member(name: str, body: MemberSet, user: Writer, acl: Acl, db: Db) -> Ok:
    """Give someone a role in this namespace (built-in or custom), change it, or (role null) remove them. Needs the
    members permission; someone who isn't an admin gives only roles they hold every permission of, and changes only
    people whose role they hold every permission of."""
    sid = acl.namespace(name, "members")
    acct = auth.find_account(db, body.email) if body.email else auth.get_account(db, body.account or 0)
    if not acct or acct.get("removed_at"):
        raise HTTPException(404, "no such person")
    if not user.admin:
        mine = user.perms.get(sid, frozenset())
        table = roles.table(db)
        now = db.one("SELECT VALUE role FROM $r", r=auth.R("membership", f"{acct['id']}-{sid}"))
        for r in (body.role, now):
            if r and not table.get(r, roles.ALL) <= mine:
                raise HTTPException(403, f"the {r} role can do more than yours here")
    with domain_errors():
        auth.set_role(db, acct["id"], sid, body.role)
    auth.audit(db, user.as_audit(), "member.set", f"space:{sid}", {"account": acct["id"], "role": body.role})
    return Ok()


@router.get("/roles")
def list_roles(user: CurrentUser, db: Db) -> Roles:
    """The permissions a role can have, and every role: viewer, editor and owner, then the custom ones."""
    return Roles(
        permissions=[RolePermission(name=n, label=label, first=first) for n, (first, label) in roles.PERMISSIONS.items()],
        roles=[RoleOut.model_validate(r) for r in roles.listing(db)],
    )


@router.post("/roles")
def create_role(body: RoleCreate, user: AdminWriter, db: Db) -> RoleOut:
    """A custom role (admins), given per namespace like the built-in ones. Audited as `role.create`."""
    with domain_errors():
        name = roles.create(db, body.name, body.permissions, body.label, body.description)
    auth.audit(db, user.as_audit(), "role.create", f"role:{name}", {"permissions": sorted(body.permissions)})
    return next(RoleOut.model_validate(r) for r in roles.listing(db) if r["name"] == name)


@router.patch("/roles/{name}")
def update_role(name: str, body: RoleUpdate, user: AdminWriter, db: Db) -> RoleOut:
    """Change a custom role's label, description or permissions; everyone holding it gets the change at once.
    Built-in roles can't change. Audited as `role.update`."""
    with domain_errors():
        roles.update(db, name, body.permissions, body.label, body.description)
    auth.audit(db, user.as_audit(), "role.update", f"role:{name}", body.model_dump(exclude_unset=True))
    return next(RoleOut.model_validate(r) for r in roles.listing(db) if r["name"] == name)


@router.delete("/roles/{name}")
def delete_role(name: str, user: AdminWriter, db: Db) -> Ok:
    """Delete a custom role. Refused (409) while anyone holds it. Audited as `role.delete`."""
    try:
        roles.delete(db, name)
    except KeyError:
        raise HTTPException(404, "not found") from None
    except ValueError as e:
        raise HTTPException(409, str(e)) from None
    auth.audit(db, user.as_audit(), "role.delete", f"role:{name}")
    return Ok()
