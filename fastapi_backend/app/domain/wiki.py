"""Wiki addresses: every entity has a page at /wiki/<namespace>/<Name_As_Written>, as wikis name their pages.

The name is the address: spaces become underscores and the rest stays as written, so `Ada Lovelace` in namespace `work`
is /wiki/work/Ada_Lovelace. Nothing is stored for it. An address is read the way analysis matches names (any case,
any spacing, the same entity key), so /wiki/work/ada_lovelace finds the page too; a name the entity was known by
before a rename or a merge (its aliases) finds the entity it is part of now. Either way the page answers with the
address to show (`canonical` False means the browser should switch to it).
"""

from __future__ import annotations

import re
import urllib.parse

from . import analyze, store

R = store.R


def slug(name: str) -> str:
    """Ada Lovelace -> Ada_Lovelace."""
    return re.sub(r"\s+", "_", (name or "").strip())


def _part(s: str) -> str:
    """Escaped as the web app's encodeURIComponent escapes it, so a name with / or ? in it stays one part."""
    return urllib.parse.quote(s, safe="!*'()")


def path(namespace: str, name: str) -> str:
    """The page's address in the web app, like /wiki/work/Ada_Lovelace."""
    return f"/wiki/{_part(namespace)}/{_part(slug(name))}"


def find(db, sid: int, address: str) -> tuple[int, bool] | None:
    """(entity id, whether `address` is already its own address) for an address in namespace `sid`, or None."""
    address = (address or "").strip().strip("/")
    keys = [k for k in dict.fromkeys((analyze.ent_key(address.replace("_", " ")), analyze.ent_key(address))) if k]
    eid = None
    for key in keys:
        row = db.one("SELECT record::id(id) AS id FROM entity WHERE ekey = $k LIMIT 1", k=f"{sid}:{key}")
        if row:
            eid = row["id"]
            break
    if eid is None:  # a name it had before a rename, or the name of one merged into it
        for key in keys:
            row = db.one("SELECT entity FROM entity_alias WHERE space = $s AND key = $k LIMIT 1", s=sid, k=key)
            if row and db.one("SELECT VALUE id FROM $r", r=R("entity", row["entity"])):
                eid = row["entity"]
                break
    if eid is None:
        return None
    return eid, slug(db.one("SELECT VALUE name FROM $r", r=R("entity", eid))) == address
