"""Notes as pages: free notes in a tree, a page per thing, @ and # links with backlinks, and pages that outlive or follow
what they were about."""

from __future__ import annotations

import pytest

from app.domain import deletion, entities, moving, notebook, store
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


def _entity(db, name, typ, ns="pods"):
    sid = store.ns_id(db, ns)
    eid = db.next_id("entity")
    key = name.casefold()
    db.q("CREATE $r CONTENT $d", r=R("entity", eid), d={"space": sid, "key": key, "ekey": f"{sid}:{key}", "name": name, "type": typ})
    return {"id": eid, "name": name}


def _new(client, h, **kw):
    r = client.post("/api/v1/notes", headers=h, json={"ns": "pods", **kw})
    assert r.status_code == 200, r.text
    return r.json()


def test_tree(client, env):
    he, hv = env["he"], env["hv"]
    top = _new(client, he, title="  Plans ", summary="What we do next\n quarter", place="project")
    assert (top["title"], top["summary"], top["summary_by"], top["place"], top["author"]) == (
        "Plans",
        "What we do next quarter",
        "person",
        "project",
        "person",
    )
    assert top["date"] and top["created_by"] == "ed@x.io" and top["can_edit"]
    kid = _new(client, he, title="Hiring", parent=top["id"])
    kid2 = _new(client, he, title="Budget", parent=top["id"])
    tree = client.get("/api/v1/notes?ns=pods", headers=hv).json()
    assert [(p["title"], p["parent"]) for p in tree["pages"]] == [("Plans", None), ("Hiring", top["id"]), ("Budget", top["id"])]
    # viewers read but don't write
    assert not client.get(f"/api/v1/notes/{top['id']}", headers=hv).json()["can_edit"]
    assert client.post("/api/v1/notes", headers=hv, json={"ns": "pods", "title": "x"}).status_code == 403
    assert client.patch(f"/api/v1/notes/{top['id']}", headers=hv, json={"title": "x"}).status_code == 403
    # people outside the namespace see nothing
    assert client.get(f"/api/v1/notes/{top['id']}", headers=env["hx"]).status_code == 404
    assert client.get("/api/v1/notes?ns=pods", headers=env["hx"]).status_code == 404
    # move Budget before Hiring, then a page can't go inside itself or its own child
    r = client.post(f"/api/v1/notes/{kid2['id']}/move", headers=he, json={"parent": top["id"], "before": kid["id"]})
    assert r.status_code == 200, r.text
    tree = client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]
    assert [p["title"] for p in tree if p["parent"] == top["id"]] == ["Budget", "Hiring"]
    assert client.post(f"/api/v1/notes/{top['id']}/move", headers=he, json={"parent": kid["id"]}).status_code == 400
    assert client.post(f"/api/v1/notes/{top['id']}/move", headers=he, json={"parent": top["id"]}).status_code == 400
    # change and clear fields; place "" unfiles it
    r = client.patch(f"/api/v1/notes/{top['id']}", headers=he, json={"summary": "", "place": "", "date": "2026-01-02"})
    assert r.status_code == 200, r.text
    assert (r.json()["summary"], r.json()["place"], r.json()["date"]) == (None, None, "2026-01-02")
    assert client.patch(f"/api/v1/notes/{top['id']}", headers=he, json={"date": "soon"}).status_code == 400
    # deleting a page moves what's inside it up
    assert client.delete(f"/api/v1/notes/{top['id']}", headers=he).status_code == 200
    tree = client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]
    assert sorted((p["title"], p["parent"]) for p in tree) == [("Budget", None), ("Hiring", None)]


def test_links_and_backlinks(client, env, db):
    he, a = env["he"], env["a"]
    topic, person = _entity(db, "Capsids", "TERM"), _entity(db, "Ada Lovelace", "PERSON")
    # what @ and # offer
    hits = client.get("/api/v1/notes/targets?ns=pods&sign=%23", headers=he).json()
    assert hits and {h["kind"] for h in hits} == {"topic"}
    hits = client.get(f"/api/v1/notes/targets?ns=pods&q={person['name'][:3]}", headers=he).json()
    assert f"entity:{person['id']}" in [h["target"] for h in hits]
    other = _new(client, he, title="Background")
    body = (
        f"Met @[{person['name']}](entity:{person['id']}) about #[{topic['name']}](entity:{topic['id']}) in "
        f"@[the call](recording:{a}); see @[bg](page:{other['id']}) and @[gone](recording:999999)."
    )
    p = _new(client, he, title="Meeting", body=body)
    assert [(x["sign"], x["target"], x["name"] is not None) for x in p["links"]] == [
        ("@", f"entity:{person['id']}", True),
        ("#", f"entity:{topic['id']}", True),
        ("@", f"recording:{a}", True),
        ("@", f"page:{other['id']}", True),
        ("@", "recording:999999", False),
    ]
    # the linked page and the recording's page show the backlink
    assert [b["title"] for b in client.get(f"/api/v1/notes/{other['id']}", headers=he).json()["backlinks"]] == ["Meeting"]
    draft = client.get(f"/api/v1/notes/about/recording/{a}", headers=he).json()
    assert draft["id"] is None and draft["about"] == f"recording:{a}" and [b["title"] for b in draft["backlinks"]] == ["Meeting"]
    # the body's plain text drops the link syntax
    assert db.one("SELECT text FROM $r", r=R("note_page", p["id"]))["text"].startswith(f"Met {person['name']} about")
    # a new body replaces the links
    client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "nothing now"})
    assert client.get(f"/api/v1/notes/{other['id']}", headers=he).json()["backlinks"] == []


def test_pages_of_things(client, env, db, cfg):
    he, hv, a, b = env["he"], env["hv"], env["a"], env["b"]
    # a recording's page is a draft until someone writes it
    d = client.get(f"/api/v1/notes/about/recording/{a}", headers=hv).json()
    assert d["id"] is None and d["title"] and not d["can_edit"]
    p = _new(client, he, title="About ep1", about=f"recording:{a}", body="Good one", doc="opaque-editor-state")
    assert p["about"] == f"recording:{a}" and p["doc"] == "opaque-editor-state"
    assert client.get(f"/api/v1/notes/about/recording/{a}", headers=hv).json()["id"] == p["id"]
    # one page per thing; things of another namespace can't have a page here
    assert client.post("/api/v1/notes", headers=he, json={"ns": "pods", "title": "x", "about": f"recording:{a}"}).status_code == 400
    assert (
        client.post("/api/v1/notes", headers=he, json={"ns": "pods", "title": "x", "about": f"recording:{env['call']}"}).status_code == 400
    )
    assert client.get(f"/api/v1/notes/about/recording/{env['call']}", headers=hv).status_code == 404
    # pages of things stay out of the tree unless asked for, and don't move in it
    assert client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"] == []
    assert [x["id"] for x in client.get("/api/v1/notes?ns=pods&all=true", headers=hv).json()["pages"]] == [p["id"]]
    assert client.post(f"/api/v1/notes/{p['id']}/move", headers=he, json={}).status_code == 400
    # a new body without the editor's state drops it
    r = client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "Changed by the assistant"})
    assert r.json()["doc"] is None
    # an entity's page; the entity merged away leaves its page as a free note
    e1, e2 = _entity(db, "Ada Lovelace", "PERSON")["id"], _entity(db, "A. Lovelace", "PERSON")["id"]
    ep = _new(client, he, title="Who", about=f"entity:{e2}")
    entities.merge(db, e1, [e2])
    assert notebook.get(db, ep["id"]).get("about") is None
    assert ep["id"] in [x["id"] for x in client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]]
    # a recording moved to another namespace takes its page along; a deleted one leaves it as a free note
    pb = _new(client, he, title="About ep2", about=f"recording:{b}")
    moving.move(db, cfg, b, store.ns_id(db, "calls"))
    moved = notebook.get(db, pb["id"])
    assert moved["space"] == store.ns_id(db, "calls") and moved["about"] == f"recording:{b}"
    assert client.get(f"/api/v1/notes/about/recording/{b}", headers=env["hx"]).json()["id"] == pb["id"]
    deletion.delete(db, cfg, a)
    left = notebook.get(db, p["id"])
    assert left["about"] is None and left["body"] == "Changed by the assistant"


def test_domain_helpers():
    body = "**Hi** @[Ada](entity:5) and #[Caps](entity:9), @[Ada](entity:5) twice, @[x](nope:1)\n\n- [ ] [site](http://x)"
    assert notebook.mentions(body) == [("@", "entity", 5, "Ada"), ("#", "entity", 9, "Caps")]
    assert notebook.plain(body) == "Hi Ada and Caps, Ada twice, x\n\nsite"


@pytest.fixture
def llm(cfg):
    from tests import fake_llm

    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.seen = []
    yield fake_llm.Handler
    srv.shutdown()


def _age(db, pid, seconds=600):
    import datetime as dt

    old = (dt.datetime.now(dt.UTC) - dt.timedelta(seconds=seconds)).isoformat(timespec="seconds")
    db.q("UPDATE $r SET updated_at = $t", r=R("note_page", pid), t=old)


def test_refining_titles_and_summaries(client, env, db, cfg, llm):
    he = env["he"]
    p = _new(client, he, title="Untitled", body="Capsid samples ship on Friday.\n\nAlice sends them.")
    # nothing happens while someone may still be typing
    assert notebook.refine_due(db, cfg) == 0
    _age(db, p["id"])
    assert notebook.refine_due(db, cfg) == 1
    got = client.get(f"/api/v1/notes/{p['id']}", headers=he).json()
    assert (got["title"], got["summary"], got["summary_by"]) == ("Capsid plan", "About Capsid samples ship on Friday.", "assistant")
    # done until the note changes; a person's title change makes it due again, and their own title stays
    assert notebook.refine_due(db, cfg) == 0
    client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"title": "Shipping"})
    _age(db, p["id"])
    assert notebook.refine_due(db, cfg) == 1
    assert client.get(f"/api/v1/notes/{p['id']}", headers=he).json()["title"] == "Shipping"
    # switched off, nothing is sent
    client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "New text"})
    _age(db, p["id"])
    cfg["ai"]["refine_notes"] = False
    n = len(llm.seen)
    assert notebook.refine_due(db, cfg) == 0 and len(llm.seen) == n
    # empty notes aren't sent
    cfg["ai"]["refine_notes"] = True
    e = _new(client, he, title="Empty")
    _age(db, e["id"])
    _age(db, p["id"])
    notebook.refine_due(db, cfg)
    assert len(llm.seen) == n + 1


def test_assistant_keeps_notes(client, env, db, cfg):
    import json

    from app.domain import ai_tools

    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'ed@x.io'")["id"]
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    box = ai_tools.Toolbox(db, cfg, {"id": uid, "email": "ed@x.io"}, {pods}, {pods}, {}, None)
    names = [t["function"]["name"] for t in box.specs()]
    assert {"find_notes", "read_note", "write_note", "update_note"} <= set(names)

    def call(name, **args):
        out, _ = box.call(name, args)
        return json.loads(out)

    w = call("write_note", namespace="pods", title="Shipping", body=f"Plan for @[ep1](recording:{env['a']}).", place="project")
    assert w["note_id"]
    page = notebook.get(db, w["note_id"])
    assert (page["author"], page["place"]) == ("assistant", "project")
    assert call("write_note", namespace="calls", title="x", body="y")["error"]  # not a namespace it can write in
    call("update_note", note_id=w["note_id"], append="Alice sends them.", summary="When the samples ship")
    got = call("read_note", note_id=w["note_id"])
    assert got["body"].endswith("Alice sends them.") and got["summary"] == "When the samples ship"
    assert got["links"] == [{"sign": "@", "target": f"recording:{env['a']}", "label": "ep1"}]
    assert call("read_note", about=f"recording:{env['a']}")["note"] is None
    assert [n["id"] for n in call("find_notes", query="alice")["notes"]] == [w["note_id"]]
    assert call("find_notes", place="area")["notes"] == []
    # a viewer's assistant reads but doesn't write
    view = ai_tools.Toolbox(db, cfg, {"id": uid, "email": "ed@x.io"}, {pods}, set(), {}, None)
    vnames = [t["function"]["name"] for t in view.specs()]
    assert "read_note" in vnames and "write_note" not in vnames
    assert calls not in view.readable


def test_filing_in_para(client, env, db, cfg, llm):
    he = env["he"]
    a = _new(client, he, title="Ship the samples", body="Ship by Friday.")
    b = _new(client, he, title="Mine", body="Mine.", place="area")
    c = _new(client, he, title="Unsure", body="Hmm.")
    for p in (a, b, c):
        db.q("UPDATE $r SET refine_pending = false", r=R("note_page", p["id"]))
    llm.decision = {"choice": "project", "confidence": 0.95}
    db.q("UPDATE $r SET filed = true", r=R("note_page", c["id"]))  # c waits for the next pass
    assert notebook.file_due(db, cfg) == 1
    got = client.get(f"/api/v1/notes/{a['id']}", headers=he).json()
    assert (got["place"], got["place_by"], got["place_suggestion"]) == ("project", "assistant", None)
    # what a person filed stays; an unsure answer waits as a suggestion, and filing it takes the suggestion away
    assert client.get(f"/api/v1/notes/{b['id']}", headers=he).json()["place"] == "area"
    db.q("UPDATE $r SET filed = false", r=R("note_page", c["id"]))
    llm.decision = {"choice": "resource", "confidence": 0.4}
    assert notebook.file_due(db, cfg) == 1
    got = client.get(f"/api/v1/notes/{c['id']}", headers=he).json()
    assert got["place"] is None and got["place_suggestion"]["place"] == "resource"
    got = client.patch(f"/api/v1/notes/{c['id']}", headers=he, json={"place": "resource"}).json()
    assert (got["place"], got["place_by"], got["place_suggestion"]) == ("resource", "person", None)
    # switched off, nothing is decided
    d = _new(client, he, title="Later", body="x")
    db.q("UPDATE $r SET refine_pending = false", r=R("note_page", d["id"]))
    cfg["ai"]["organise_notes"] = False
    assert notebook.file_due(db, cfg) == 0
    llm.decision = None
