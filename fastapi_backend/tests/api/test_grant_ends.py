"""Grants that end (docs/access.md#grants-that-end): a role in a namespace, a role on a collection and permission on an
asset can each have an end date. Until then it holds; after it, it opens nothing anywhere, and lists show it as
expired."""

from __future__ import annotations

import datetime as dt

from app.domain import store
from tests.api.test_permissions import env  # noqa: F401  (the fixture)
from tests.helpers import login, make_user

R = store.R
SOON = (dt.datetime.now(dt.UTC) + dt.timedelta(days=7)).replace(microsecond=0)
PAST = "2020-01-01T00:00:00+00:00"


def _end(db, table, key):
    """Time passes: the grant's end date is now behind us."""
    db.q("UPDATE $r SET expires_at = $e", r=R(table, key), e=PAST)


def test_asset_permission_holds_until_it_ends(client, env, db):  # noqa: F811
    clip, ho, hg = env["clip"], env["ho"], env["hg"]
    url, page = f"/api/v1/recordings/{clip}/permissions", f"/api/v1/public/recordings/{clip}"
    # an end date that has passed isn't one to give
    assert client.post(url, headers=ho, json={"email": "guest@x.io", "expires_at": PAST}).status_code == 400
    people = client.post(url, headers=ho, json={"email": "guest@x.io", "expires_at": SOON.isoformat()}).json()
    guest = people[0]["account"]
    assert (people[0]["expires_at"], people[0]["expired"]) == (SOON.isoformat(), False)
    # not ended yet: they see all of it
    assert client.get(page, headers=hg).json()["view"] == "full"
    assert [c["id"] for c in client.get("/api/v1/public/home", headers=hg).json()["shared"]] == [clip]
    # giving it again without a date keeps the end
    assert client.post(url, headers=ho, json={"email": "guest@x.io"}).json()[0]["expires_at"] == SOON.isoformat()

    _end(db, "permission", f"{clip}-{guest}")
    # ended: nothing anywhere, and the owner's list says so
    assert client.get(page, headers=hg).status_code == 404
    assert client.get("/api/v1/public/home", headers=hg).json()["shared"] == []
    assert client.get(f"/iiif/{clip}/manifest", headers=hg).status_code == 404
    assert [(p["email"], p["expired"]) for p in client.get(url, headers=ho).json()] == [("guest@x.io", True)]

    # changing the end brings it back; null means no end
    assert client.patch(f"{url}/{guest}", headers=env["he"], json={"expires_at": None}).status_code == 403
    assert client.patch(f"{url}/999999", headers=ho, json={"expires_at": None}).status_code == 404
    back = client.patch(f"{url}/{guest}", headers=ho, json={"expires_at": None}).json()
    assert (back[0]["expires_at"], back[0]["expired"]) == (None, False)
    assert client.get(page, headers=hg).json()["view"] == "full"
    actions = db.values("SELECT VALUE action FROM audit_log WHERE action CONTAINS 'permission'")
    assert "recording.permission.change" in actions
    # an ended permission given again without a date starts over, without one
    _end(db, "permission", f"{clip}-{guest}")
    assert client.post(url, headers=ho, json={"email": "guest@x.io"}).json()[0]["expired"] is False


def test_namespace_role_holds_until_it_ends(client, env, db):  # noqa: F811
    ho = env["ho"]
    uid = make_user(db, "temp@x.io", "temp password 1")
    sid = store.ns_id(db, "pods")
    members = "/api/v1/namespaces/pods/members"
    r = client.put(members, headers=ho, json={"email": "temp@x.io", "role": "editor", "expires_at": SOON.isoformat()})
    assert r.status_code == 200, r.text
    ht = login(client, "temp@x.io", "temp password 1")
    assert client.get(f"/api/v1/recordings/{env['clip']}", headers=ht).status_code == 200
    me = client.get("/api/v1/auth/me", headers=ht).json()
    assert "pods" in me["roles"]
    row = next(m for m in client.get(members, headers=ho).json() if m["email"] == "temp@x.io")
    assert (row["role"], row["expires_at"], row["expired"]) == ("editor", SOON.isoformat(), False)

    _end(db, "membership", f"{uid}-{sid}")
    # ended: the role opens nothing, wherever it's checked
    assert client.get(f"/api/v1/recordings/{env['clip']}", headers=ht).status_code == 404
    assert "pods" not in client.get("/api/v1/auth/me", headers=ht).json()["roles"]
    assert client.get("/api/v1/recordings", headers=ht, params={"ns": "pods"}).status_code == 404
    assert client.get(f"/api/v1/public/recordings/{env['clip']}", headers=ht).status_code == 404
    row = next(m for m in client.get(members, headers=ho).json() if m["email"] == "temp@x.io")
    assert row["expired"] is True
    # giving the role again starts it over
    client.put(members, headers=ho, json={"email": "temp@x.io", "role": "viewer"})
    assert client.get(f"/api/v1/recordings/{env['clip']}", headers=ht).status_code == 200


def test_collection_role_holds_until_it_ends(client, env, db):  # noqa: F811
    ho, hg = env["ho"], env["hg"]
    cols = "/api/v1/namespaces/pods/collections"
    general = next(n["id"] for n in client.get(cols, headers=ho).json() if n["default"])
    r = client.put(f"{cols}/{general}/members", headers=ho, json={"email": "guest@x.io", "role": "viewer", "expires_at": SOON.isoformat()})
    assert r.status_code == 200, r.text
    assert (r.json()[0]["expires_at"], r.json()[0]["expired"]) == (SOON.isoformat(), False)
    assert client.get(f"/api/v1/recordings/{env['clip']}", headers=hg).status_code == 200

    guest = r.json()[0]["account"]
    _end(db, "collection_role", f"{general}-{guest}")
    assert client.get(f"/api/v1/recordings/{env['clip']}", headers=hg).status_code == 404
    assert client.get(cols, headers=hg).status_code == 404
    assert client.get(f"{cols}/{general}/members", headers=ho).json()[0]["expired"] is True
