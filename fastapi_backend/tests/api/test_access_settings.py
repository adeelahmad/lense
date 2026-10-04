"""A recording's access (public, restricted or private), the parts a public one opens, featured, namespace defaults,
and converting the old IIIF-only levels."""

from __future__ import annotations

import json

from app.domain import access, metadata, store
from tests.helpers import login, make_user, seed

R = store.R


def activities(db, rid):
    rows = db.rows("SELECT type, record::id(id) AS n FROM iiif_activity WHERE recording = $r ORDER BY n", r=rid)
    return [x["type"] for x in rows]


def test_access_endpoint_and_defaults(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    hv, he = login(client, "vi@x.io", "viewer password 1"), login(client, "ed@x.io", "editor password 1")
    ho = login(client, "own@x.io", "owner password 1")
    url = f"/api/v1/recordings/{a}/access"
    # nothing set: private, following the namespace (whose default is private, every part open)
    got = client.get(url, headers=hv).json()
    assert got == {
        "access": "private",
        "open": ["media", "transcript", "index"],
        "featured": False,
        "inherited": True,
        "default": {"access": "private", "open": ["media", "transcript", "index"]},
    }
    # members read it; publishing is for owners, whichever way it's asked for
    assert client.put(url, headers=hv, json={"access": "public"}).status_code == 403
    assert client.put(url, headers=he, json={"access": "public"}).status_code == 403
    meta_url = f"/api/v1/recordings/{a}/metadata"
    assert client.put(meta_url, headers=he, json={"set": {"featured": True}}).status_code == 403
    assert client.put(meta_url, headers=he, json={"reset": ["access"]}).status_code == 403
    assert client.post("/api/v1/metadata/bulk", headers=he, json={"recordings": [a], "set": {"access": "public"}}).status_code == 403
    cc = "https://creativecommons.org/licenses/by/4.0/"
    assert client.put(meta_url, headers=he, json={"set": {"rights": cc}}).status_code == 200  # other fields: editors
    assert client.get(f"/api/v1/recordings/{call}/access", headers=ho).status_code == 404
    got = client.put(url, headers=ho, json={"access": "public", "open": ["transcript", "index"], "featured": True}).json()
    assert (got["access"], got["open"], got["featured"], got["inherited"]) == ("public", ["transcript", "index"], True, False)
    assert activities(db, a) == ["Create"]  # IIIF harvesters hear it was published
    # the recording's detail and the list say so too
    d = client.get(f"/api/v1/recordings/{a}", headers=hv).json()
    assert (d["access"], d["open"], d["featured"], d["access_inherited"]) == ("public", ["transcript", "index"], True, False)
    assert "access_parts" not in d
    row = next(r for r in client.get("/api/v1/recordings", headers=hv).json() if r["id"] == a)
    assert (row["access"], row["open"], row["featured"]) == ("public", ["transcript", "index"], True)
    # every part closed is a choice of its own; bad values are refused
    assert client.put(url, headers=ho, json={"open": []}).json()["open"] == []
    for bad in ({"access": "signed-in"}, {"open": ["audio"]}, {"featured": "maybe"}):
        assert client.put(url, headers=ho, json=bad).status_code == 422, bad
    assert client.put(url, headers=ho, json={}).status_code == 400
    # kept in the metadata history, and audited; reverting an access change is for owners too
    hist = client.get(f"/api/v1/recordings/{a}/metadata/history", headers=he).json()
    assert hist[0]["changed"] == ["open"] and hist[1]["changed"] == ["access", "featured", "open"]
    assert client.post(f"/api/v1/metadata/edits/{hist[0]['id']}/revert", headers=he).status_code == 403
    # a revert puts the whole earlier state back: undoing the rights edit would undo the access change too
    assert hist[2]["changed"] == ["rights"]
    assert client.post(f"/api/v1/metadata/edits/{hist[2]['id']}/revert", headers=he).status_code == 403
    details = [x["detail"] for x in db.rows("SELECT detail FROM audit_log WHERE action = 'recording.access'")]
    assert {"access": "public", "featured": True, "open": ["transcript", "index"]} in details
    # null follows the namespace again: owners set its default access and parts
    got = client.put(url, headers=ho, json={"access": None, "open": None, "featured": False}).json()
    assert (got["access"], got["inherited"], got["featured"]) == ("private", True, False)
    assert activities(db, a) == ["Create", "Update", "Delete"]
    assert client.put("/api/v1/namespaces/pods/metadata", headers=he, json={"profile": {"default_access": "public"}}).status_code == 403
    profile = {"default_access": "public", "default_open": ["index", "media"]}
    r = client.put("/api/v1/namespaces/pods/metadata", headers=ho, json={"profile": profile})
    assert r.status_code == 200 and r.json()["profile"]["default_open"] == ["media", "index"]
    got = client.get(url, headers=hv).json()
    assert (got["access"], got["open"], got["inherited"], got["default"]["access"]) == ("public", ["media", "index"], True, "public")
    assert activities(db, a)[-1] == "Create" and activities(db, b) == ["Create"]  # both follow the namespace
    bad = {"default_access": "transcript"}
    assert client.put("/api/v1/namespaces/pods/metadata", headers=ho, json={"profile": bad}).status_code == 400
    # a recording's own setting wins over the namespace's
    client.put(url, headers=ho, json={"access": "restricted"})
    assert client.get(url, headers=hv).json()["access"] == "restricted"
    client.put("/api/v1/namespaces/pods/metadata", headers=ho, json={"profile": {"default_access": "private"}})
    assert activities(db, b)[-1] == "Delete" and activities(db, a)[-1] == "Delete"


def test_list_filters_by_access(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    h = login(client, "root@x.io", "root password 1")
    metadata.save(db, cfg, a, {"access": "public", "featured": True})
    metadata.save(db, cfg, call, {"access": "restricted"})
    metadata.save_namespace(db, store.ns_id(db, "pods"), profile={"default_access": "public"})  # b follows it

    def ids(**params):
        r = client.get("/api/v1/recordings", params=params, headers=h)
        assert r.status_code == 200, r.text
        return sorted(x["id"] for x in r.json())

    assert ids(access="public") == sorted([a, b])
    assert ids(access=["restricted", "private"]) == [call]
    assert ids(access="private") == []
    assert ids(featured="true") == [a]
    assert ids(featured="false") == sorted([b, call])
    assert ids(access="public", featured="false") == [b]
    assert client.get("/api/v1/recordings", params={"access": "open"}, headers=h).status_code == 422


def test_convert_the_iiif_levels(db, cfg, folder):
    """The first start after the upgrade turns the IIIF-only levels into the access setting, once."""
    ids = seed(db, cfg, folder)
    x = ids + [db.next_id("recording")]
    db.q("CREATE $r CONTENT $d", r=R("recording", x[3]), d={"space": store.ns_id(db, "calls"), "title": "t"})
    for rid, level in zip(x, ("public", "transcript", "signed-in", "private"), strict=True):
        db.q("UPDATE $r SET meta_json = $m", r=R("recording", rid), m=json.dumps({"access": level, "rights": "keep me"}))
    db.q("UPDATE $r SET profile_json = $p", r=R("space", store.ns_id(db, "calls")), p=json.dumps({"default_access": "signed-in"}))
    inheriting = db.next_id("recording")
    db.q("CREATE $r CONTENT $d", r=R("recording", inheriting), d={"space": store.ns_id(db, "calls"), "title": "follows calls"})
    # before the conversion, reads already understand the old levels
    assert metadata.stored(db, x[1])["access"] == "public" and metadata.stored(db, x[1])["open"] == ["transcript", "index"]
    assert access.of(db, inheriting)["access"] == "restricted"
    db.q("DELETE $r", r=R("migration", "access-levels"))
    store.migrate(db)
    got = {rid: access.of(db, rid) for rid in x}
    assert [(got[r]["access"], got[r]["open"], got[r]["inherited"]) for r in x[:3]] == [
        ("public", ["media", "transcript", "index"], False),
        ("public", ["transcript", "index"], False),
        ("restricted", ["media", "transcript", "index"], False),
    ]
    assert got[x[3]]["access"] == "private"
    assert json.loads(db.one("SELECT meta_json FROM $r", r=R("recording", x[0]))["meta_json"]) == {"rights": "keep me"}
    assert json.loads(db.one("SELECT profile_json FROM $r", r=R("space", store.ns_id(db, "calls")))["profile_json"]) == {
        "default_access": "restricted"
    }
    # signed-in ones were listed in IIIF and restricted ones aren't: harvesters hear a Delete
    assert activities(db, x[2]) == ["Delete"] and activities(db, inheriting) == ["Delete"] and activities(db, x[1]) == []
    store.migrate(db)  # once only
    assert activities(db, x[2]) == ["Delete"]
    # reverting a metadata edit from before the upgrade converts the old level it brings back
    eid = db.next_id("meta_edit")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("meta_edit", eid),
        d={"target": f"recording:{x[0]}", "before": json.dumps({"access": "transcript"}), "after": "{}", "at": store.now()},
    )
    metadata.revert(db, cfg, eid)
    assert (access.of(db, x[0])["access"], access.of(db, x[0])["open"]) == ("public", ["transcript", "index"])
