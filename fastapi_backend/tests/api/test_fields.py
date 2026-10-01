"""Custom metadata fields: editors define them on a namespace or a collection, for the resources, collections or files
inside it; values are checked by type, kept in the resource's metadata history, filter the Library, and appear in IIIF
and on public pages only when the field is published."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.domain import metadata, store
from tests.helpers import login, make_user, seed

R = store.R
NS = "/api/v1/namespaces/pods"


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    make_user(db, "guest@x.io", "guest password 1")
    e = {
        "a": a,
        "b": b,
        "call": call,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }
    # pods: General (b) · Talks (a)
    e["talks"] = client.post(f"{NS}/collections", headers=e["he"], json={"name": "Talks"}).json()["id"]
    client.post("/api/v1/recordings/collection", headers=e["he"], json={"recordings": [a], "collection": e["talks"]})
    # the guest edits Talks only
    assert (
        client.put(f"{NS}/collections/{e['talks']}/members", headers=e["hr"], json={"email": "guest@x.io", "role": "editor"}).status_code
        == 200
    )
    e["hg"] = login(client, "guest@x.io", "guest password 1")
    return e


def define(client, h, **body):
    return client.post(f"{NS}/fields", headers=h, json=body)


def test_editors_define_fields_where_they_edit(client, env):
    he, hv, hg = env["he"], env["hv"], env["hg"]
    who = define(client, he, label="Interviewer", type="text")
    assert who.status_code == 200, who.text
    assert (who.json()["target"], who.json()["published"], who.json()["collection"]) == ("resource", False, None)
    fmt = define(client, he, label="Format", type="choice", options=["Oral history", "Lecture", " lecture ", ""], published=True)
    assert fmt.json()["options"] == ["Oral history", "Lecture"]  # trimmed, blanks and repeats dropped
    venue = define(client, hg, label="Venue", type="text", collection=env["talks"])
    assert venue.status_code == 200 and venue.json()["collection_path"] == ["Talks"]
    # a collection's editor defines fields on it, not on the namespace
    assert define(client, hg, label="Elsewhere", type="text").status_code == 403
    assert define(client, hv, label="Mine", type="text").status_code == 403
    assert define(client, env["hx"], label="Theirs", type="text").status_code == 404
    # what doesn't fit
    assert define(client, he, label="interviewer", type="text").json()["detail"] == "There's a field called “interviewer” there already."
    assert define(client, he, label="Interviewer", type="text", target="file").status_code == 200  # another target, another place
    assert define(client, he, label="Kind", type="choice").json()["detail"] == "A choice field needs at least one option."
    assert define(client, he, label="Odd", type="colour").status_code == 422
    listed = client.get(f"{NS}/fields", headers=hv).json()
    assert [(f["label"], f["target"], f["can_change"]) for f in listed] == [
        ("Interviewer", "resource", False),
        ("Format", "resource", False),
        ("Interviewer", "file", False),
        ("Venue", "resource", False),
    ]
    assert [f["can_change"] for f in client.get(f"{NS}/fields", headers=hg).json()] == [False, False, False, True]


def test_values_are_checked_kept_in_history_and_reverted(client, env, db, cfg):
    he, a, b = env["he"], env["a"], env["b"]
    who = define(client, he, label="Interviewer", type="text").json()["id"]
    year = define(client, he, label="Year", type="number").json()["id"]
    when = define(client, he, label="Recorded", type="date").json()["id"]
    done = define(client, he, label="Transcribed by hand", type="boolean").json()["id"]
    tags = define(client, he, label="Themes", type="choices", options=["Work", "Family", "War"]).json()["id"]
    link = define(client, he, label="Finding aid", type="link").json()["id"]
    venue = define(client, he, label="Venue", type="text", collection=env["talks"]).json()["id"]
    # a resource in Talks has the namespace's fields and Talks'; one in General only the namespace's
    url = f"/api/v1/resources/{a}/fields"
    assert [x["field"]["label"] for x in client.get(url, headers=env["hv"]).json()["fields"]][-1] == "Venue"
    assert "Venue" not in [x["field"]["label"] for x in client.get(f"/api/v1/resources/{b}/fields", headers=he).json()["fields"]]
    values = {
        str(who): "  Ana   Ruiz ",
        str(year): "1998",
        str(when): "1998-05",
        str(done): True,
        str(tags): ["War", "Work", "War"],
        str(link): "https://example.org/aid",
        str(venue): "Town hall",
    }
    saved = client.put(url, headers=he, json={"values": values})
    assert saved.status_code == 200, saved.text
    got = {x["field"]["label"]: x["value"] for x in saved.json()["fields"]}
    assert got == {
        "Interviewer": "Ana Ruiz",
        "Year": 1998,
        "Recorded": "1998-05",
        "Transcribed by hand": True,
        "Themes": ["Work", "War"],
        "Finding aid": "https://example.org/aid",
        "Venue": "Town hall",
    }
    for bad, message in (
        ({str(year): "about 1998"}, "Year is a number."),
        ({str(when): "1998-02-30"}, "Recorded: 1998-02-30 isn't a day of the calendar."),
        ({str(done): "yes"}, "Transcribed by hand is yes or no."),
        ({str(tags): ["Love"]}, "Themes takes some of: Work, Family, War."),
        ({str(link): "javascript:alert(1)"}, "Finding aid is a link starting with http:// or https://."),
        ({"999": "x"}, "Field 999 doesn't describe this."),
    ):
        r = client.put(url, headers=he, json={"values": bad})
        assert (r.status_code, r.json()["detail"]) == (400, message)
    assert client.put(f"/api/v1/resources/{b}/fields", headers=he, json={"values": {str(venue): "x"}}).status_code == 400
    assert client.put(url, headers=env["hv"], json={"values": {str(who): "x"}}).status_code == 403
    # cleared one at a time; the history keeps every change and a revert puts them back
    client.put(url, headers=he, json={"values": {str(who): None}})
    assert db.one("SELECT fields FROM $r", r=R("recording", a))["fields"].get(f"f{who}") is None
    hist = client.get(f"/api/v1/resources/{a}/metadata/history", headers=he).json()
    assert hist[0]["changed"] == ["fields"]
    assert client.post(f"/api/v1/metadata/edits/{hist[0]['id']}/revert", headers=he).status_code == 200
    assert {x["field"]["label"]: x["value"] for x in client.get(url, headers=he).json()["fields"]}["Interviewer"] == "Ana Ruiz"
    # the descriptive metadata doesn't carry them, and saving it keeps them
    assert "fields" not in client.get(f"/api/v1/resources/{a}/metadata", headers=he).json()["meta"]
    assert client.put(f"/api/v1/resources/{a}/metadata", headers=he, json={"set": {"attribution": "The lab"}}).status_code == 200
    assert metadata.stored(db, a)["fields"][f"f{year}"] == 1998
    audit = [x for x in client.get("/api/v1/audit", headers=env["hr"]).json() if x["action"] == "fields.save"]
    assert audit and audit[-1]["target"] == f"recording:{a}"


def test_collections_and_files_have_their_own_fields(client, env, cfg):
    he, hg, talks = env["he"], env["hg"], env["talks"]
    series = define(client, he, label="Series", type="text", target="collection").json()["id"]
    pages = define(client, he, label="Pages", type="number", target="file").json()["id"]
    url = f"{NS}/collections/{talks}/fields"
    assert [x["field"]["label"] for x in client.get(url, headers=env["hv"]).json()["fields"]] == ["Series"]
    # arranging a collection is for editors of the namespace and admins of the collection: not its editors
    assert client.put(url, headers=hg, json={"values": {str(series): "Spring"}}).status_code == 403
    saved = client.put(url, headers=he, json={"values": {str(series): "Spring talks"}}).json()
    assert saved["fields"][0]["value"] == "Spring talks"
    f = client.post(
        f"/api/v1/resources/{env['a']}/files",
        headers={**he, "Content-Type": "application/octet-stream"},
        params={"name": "notes.pdf", "role": "attachment"},
        content=b"%PDF-1.4",
    ).json()
    furl = f"/api/v1/resources/{env['a']}/files/{f['id']}/fields"
    assert client.put(furl, headers=he, json={"values": {str(pages): 12}}).json()["fields"][0]["value"] == 12
    assert client.put(furl, headers=env["hv"], json={"values": {str(pages): 1}}).status_code == 403
    assert client.get(f"/api/v1/resources/{env['b']}/files/{f['id']}/fields", headers=he).status_code == 404


def test_the_library_filters_by_a_field(client, env):
    he, a, b = env["he"], env["a"], env["b"]
    who = define(client, he, label="Interviewer", type="text").json()["id"]
    fmt = define(client, he, label="Format", type="choice", options=["Oral history", "Lecture"]).json()["id"]
    themes = define(client, he, label="Themes", type="choices", options=["Work", "War"]).json()["id"]
    client.put(
        f"/api/v1/resources/{a}/fields", headers=he, json={"values": {str(who): "Ana Ruiz", str(fmt): "Lecture", str(themes): ["War"]}}
    )
    client.put(f"/api/v1/resources/{b}/fields", headers=he, json={"values": {str(fmt): "Oral history"}})
    ids = lambda **q: sorted(r["id"] for r in client.get("/api/v1/resources", headers=env["hv"], params=q).json())  # noqa: E731
    assert ids(field=who) == [a]  # any value
    assert ids(field=who, value="ruiz") == [a]  # text it contains, ignoring case
    assert ids(field=fmt, value="Oral history") == [b]
    assert ids(field=themes, value="War") == [a]
    assert ids(field=fmt) == [a, b]
    assert client.get("/api/v1/resources", headers=env["hx"], params={"field": who}).status_code == 404
    assert client.get("/api/v1/resources", headers=he, params={"field": 999}).status_code == 404


def test_published_fields_appear_in_iiif_and_public_pages(client, env, db, cfg):
    he, a = env["he"], env["a"]
    shown = define(client, he, label="Format", type="choice", options=["Lecture"], published=True).json()["id"]
    hidden = define(client, he, label="Donor", type="text").json()["id"]
    series = define(client, he, label="Series", type="text", target="collection", published=True).json()["id"]
    client.put(f"/api/v1/resources/{a}/fields", headers=he, json={"values": {str(shown): "Lecture", str(hidden): "Ms. Private"}})
    client.put(f"{NS}/collections/{env['talks']}/fields", headers=he, json={"values": {str(series): "Spring talks"}})
    metadata.save(db, cfg, a, {"access": "public"})
    anon = TestClient(client.app, base_url="https://127.0.0.1")
    pairs = {
        p["label"]["none"][0] if "none" in p["label"] else list(p["label"].values())[0][0]: p["value"]
        for p in anon.get(f"/iiif/{a}/manifest").json()["metadata"]
    }
    assert pairs["Format"] == {"none": ["Lecture"]} and "Donor" not in pairs
    page = anon.get(f"/api/v1/public/recordings/{a}").json()
    labels = [p["label"]["none"][0] for p in page["description"]["metadata"] if "none" in p["label"]]
    assert "Format" in labels and "Donor" not in labels
    assert "fields" not in page["description"]
    coll = anon.get(f"/iiif/collection/pods/{env['talks']}").json()
    assert coll["metadata"] == [{"label": {"none": ["Series"]}, "value": {"none": ["Spring talks"]}}]


def test_changing_and_deleting_fields(client, env, db):
    he, a = env["he"], env["a"]
    fmt = define(client, he, label="Format", type="choice", options=["Lecture", "Interview"]).json()["id"]
    client.put(f"/api/v1/resources/{a}/fields", headers=he, json={"values": {str(fmt): "Lecture"}})
    url = f"{NS}/fields/{fmt}"
    r = client.patch(url, headers=he, json={"options": ["Interview"]})
    assert (r.status_code, r.json()["detail"]) == (400, "1 item chose “Lecture”: change them before taking it away.")
    changed = client.patch(url, headers=he, json={"label": "Kind", "options": ["Lecture", "Interview", "Panel"], "published": True})
    assert changed.status_code == 200 and (changed.json()["label"], changed.json()["published"]) == ("Kind", True)
    assert client.patch(url, headers=env["hv"], json={"label": "X"}).status_code == 403
    assert client.patch(url, headers=he, json={"label": None}).status_code == 400
    assert client.get(url, headers=env["hv"]).json()["uses"] == 1
    gone = client.delete(url, headers=he)
    assert gone.status_code == 200 and gone.json()["uses"] == 1
    assert db.one("SELECT fields FROM $r", r=R("recording", a)).get("fields") in (None, {})
    assert client.get(url, headers=he).status_code == 404
    # a collection's fields go with it
    empty = client.post(f"{NS}/collections", headers=he, json={"name": "Empty"}).json()["id"]
    define(client, he, label="Room", type="text", collection=empty)
    assert client.delete(f"{NS}/collections/{empty}", headers=he).status_code == 200
    assert [f["label"] for f in client.get(f"{NS}/fields", headers=he).json()] == []
    actions = [x["action"] for x in client.get("/api/v1/audit", headers=env["hr"]).json()]
    assert {"field.create", "field.update", "field.delete"} <= set(actions)
