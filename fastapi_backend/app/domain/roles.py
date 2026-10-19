"""Roles and their permissions (docs/access.md#roles-and-permissions).

A role in a namespace is a set of permissions. Lens has three built-in roles, viewer, editor and owner, and admins
can add their own (a `role` row: name, label, description, permissions), given per namespace like the built-in ones
(the `membership` row holds the role's name). Built-in roles can't be changed, so existing access stays as it was.

Every role can read. A custom role also counts as the largest built-in role whose permissions it has all of (its
rank: viewer, editor or owner), and the checks that still ask for a rank (most of a recording's pages) give it that
much. The checks that ask for a permission (importing, processing, curating, sharing, publishing, removing, people,
namespace settings) give it exactly what it holds.
"""

from __future__ import annotations

import re

from . import auth, store

R = store.R

# name: (the built-in role that first has it, what it allows)
PERMISSIONS = {
    "read": ("viewer", "See, search, listen and chat; write your own notes"),
    "edit": ("editor", "Edit transcripts, catalogue records, tags, fields, collections, and notes and comments for everyone"),
    "import": ("editor", "Add recordings, documents and images: uploads, imports and RDF"),
    "process": ("editor", "Reprocess, and run pipelines, workflows, routines and batches"),
    "curate": ("editor", "Rename and merge speakers, faces, entities and topics; change the graph"),
    "share": ("editor", "Make, list and revoke share links"),
    "publish": ("owner", "Set access, open parts and featured; give permission on recordings; answer access requests"),
    "remove": ("owner", "Delete recordings and move them to another namespace"),
    "members": ("owner", "Give people roles in the namespace"),
    "manage": ("owner", "Namespace settings: metadata profile, notifications, import hooks, IP groups, the assistant, vaults"),
}
RANKS = {"viewer": 1, "editor": 2, "owner": 3}
BUILTIN = {role: frozenset(p for p, (first, _) in PERMISSIONS.items() if RANKS[first] <= rank) for role, rank in RANKS.items()}
LABELS = {"viewer": "Viewer", "editor": "Editor", "owner": "Owner"}
DESCRIPTIONS = {
    "viewer": "Reads everything in the namespace.",
    "editor": "Reads, edits, imports, processes and curates.",
    "owner": "Everything, including publishing, people and settings.",
}
NAME = re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
ALL = frozenset(PERMISSIONS)


def minimum(perm):
    """The built-in role that first holds a permission (its rank, for collection roles and rank checks)."""
    return PERMISSIONS[perm][0]


def rank(perms):
    """The largest built-in role whose permissions all of `perms` cover: viewer, editor or owner."""
    return next(r for r in ("owner", "editor", "viewer") if BUILTIN[r] <= perms or r == "viewer")


def custom(db):
    """The custom roles: {name: row}."""
    rows = db.rows("SELECT record::id(id) AS name, label, description, permissions, created_at, updated_at FROM role")
    return {r["name"]: r for r in rows}


def table(db):
    """{role name: its permissions}, built-in and custom."""
    out = dict(BUILTIN)
    for name, r in custom(db).items():
        out[name] = frozenset(r.get("permissions") or ()) | {"read"}
    return out


def exists(db, name):
    return name in BUILTIN or bool(db.one("SELECT VALUE id FROM $r", r=R("role", name)))


def listing(db):
    """Every role, built-in first, with its permissions and how many people hold it."""
    held = {}
    for r in db.values("SELECT VALUE role FROM membership"):
        held[r] = held.get(r, 0) + 1
    out = [
        {
            "name": n,
            "label": LABELS[n],
            "description": DESCRIPTIONS[n],
            "permissions": sorted(BUILTIN[n], key=list(PERMISSIONS).index),
            "builtin": True,
            "rank": n,
            "members": held.get(n, 0),
        }
        for n in RANKS
    ]
    for n, r in sorted(custom(db).items()):
        perms = frozenset(r.get("permissions") or ()) | {"read"}
        out.append(
            {
                "name": n,
                "label": r.get("label") or n,
                "description": r.get("description") or "",
                "permissions": sorted(perms, key=list(PERMISSIONS).index),
                "builtin": False,
                "rank": rank(perms),
                "members": held.get(n, 0),
            }
        )
    return out


def _perms(permissions):
    perms = set(permissions or ())
    unknown = perms - ALL
    if unknown:
        raise ValueError(f"unknown permissions: {', '.join(sorted(unknown))}; they are {', '.join(PERMISSIONS)}")
    return sorted(perms | {"read"}, key=list(PERMISSIONS).index)


def _label(label, name):
    label = " ".join((label or "").split()) or name.replace("-", " ").replace("_", " ").capitalize()
    if len(label) > 60:
        raise ValueError("a role's label has at most 60 characters")
    return label


def create(db, name, permissions, label=None, description=None):
    name = (name or "").strip().lower()
    if not NAME.match(name):
        raise ValueError("a role's name is 2 to 40 lowercase letters, digits, - and _, starting with a letter")
    if name in BUILTIN or name == "admin" or exists(db, name):
        raise ValueError(f"there's already a role called {name}")
    t = store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("role", name),
        d={
            "label": _label(label, name),
            "description": (description or "").strip()[:300],
            "permissions": _perms(permissions),
            "created_at": t,
            "updated_at": t,
        },
    )
    return name


def update(db, name, permissions=None, label=None, description=None):
    """Change a custom role; everyone holding it gets the change at once. Built-in roles can't change."""
    if name in BUILTIN:
        raise ValueError("built-in roles can't be changed; make a custom role instead")
    if not db.one("SELECT VALUE id FROM $r", r=R("role", name)):
        raise KeyError(name)
    patch = store.clean(
        {
            "permissions": _perms(permissions) if permissions is not None else None,
            "label": _label(label, name) if label is not None else None,
            "description": description.strip()[:300] if description is not None else None,
        }
    )
    db.q("UPDATE $r MERGE $p", r=R("role", name), p={**patch, "updated_at": store.now()})


def delete(db, name):
    """Delete a custom role nobody holds (ValueError otherwise: give them another role first)."""
    if name in BUILTIN:
        raise ValueError("built-in roles can't be deleted")
    if not db.one("SELECT VALUE id FROM $r", r=R("role", name)):
        raise KeyError(name)
    n = len(db.values("SELECT VALUE id FROM membership WHERE role = $n", n=name))
    if n:
        raise ValueError(f"{n} {'person holds' if n == 1 else 'people hold'} this role: give them another one first")
    db.q("DELETE $r", r=R("role", name))


def holders(db, sid, perm):
    """The accounts whose role in namespace `sid` has permission `perm` (admins aside)."""
    t = table(db)
    rows = db.rows("SELECT account, role, expires_at FROM membership WHERE space = $s", s=sid)
    return [r["account"] for r in rows if perm in t.get(r["role"], frozenset()) and auth.live(r)]
