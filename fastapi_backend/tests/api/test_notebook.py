"""Notes as pages: free notes in a tree, a page per thing, @ and # links with backlinks, and pages that outlive or follow
what they were about."""

from __future__ import annotations

import base64
import hashlib

import pytest

from app.domain import deletion, entities, moving, notebook, store, topics
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
    term, person = _entity(db, "Capsids", "TERM"), _entity(db, "Ada Lovelace", "PERSON")
    tid = topics.create(db, store.ns_id(db, "pods"), "Capsid design", alt=["AAV capsids"])
    # what @ and # offer: # the topics, found by any of their labels
    hits = client.get("/api/v1/notes/targets?ns=pods&sign=%23&q=aav", headers=he).json()
    assert hits == [{"target": f"topic:{tid}", "label": "Capsid design", "kind": "topic"}]
    hits = client.get(f"/api/v1/notes/targets?ns=pods&q={person['name'][:3]}", headers=he).json()
    assert f"entity:{person['id']}" in [h["target"] for h in hits]
    other = _new(client, he, title="Background")
    body = (
        f"Met @[{person['name']}](entity:{person['id']}) about #[Capsid design](topic:{tid}) and #[{term['name']}](entity:{term['id']}) in "
        f"@[the call](recording:{a}); see @[bg](page:{other['id']}) and @[gone](recording:999999)."
    )
    p = _new(client, he, title="Meeting", body=body)
    assert [(x["sign"], x["target"], x["name"] is not None) for x in p["links"]] == [
        ("@", f"entity:{person['id']}", True),
        ("#", f"topic:{tid}", True),
        ("#", f"entity:{term['id']}", True),  # links to terms written before topics still work
        ("@", f"recording:{a}", True),
        ("@", f"page:{other['id']}", True),
        ("@", "recording:999999", False),
    ]
    # the linked page and the recording's page show the backlink
    assert [b["title"] for b in client.get(f"/api/v1/notes/{other['id']}", headers=he).json()["backlinks"]] == ["Meeting"]
    draft = client.get(f"/api/v1/notes/about/recording/{a}", headers=he).json()
    assert draft["id"] is None and draft["about"] == f"recording:{a}" and [b["title"] for b in draft["backlinks"]] == ["Meeting"]
    t = client.get(f"/api/v1/notes/about/topic/{tid}", headers=he).json()  # a topic has a page of its own too
    assert (t["title"], [b["title"] for b in t["backlinks"]]) == ("Capsid design", ["Meeting"])
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
    # a new body without the editor's state keeps it (drawings live there) but marks it stale; the editor's save clears it
    r = client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "Changed by the assistant", "view": "edgeless"})
    assert (r.json()["doc"], r.json()["doc_stale"], r.json()["view"]) == ("opaque-editor-state", True, "edgeless")
    r = client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "Changed by the assistant", "doc": "state-2"})
    assert (r.json()["doc"], r.json()["doc_stale"]) == ("state-2", False)
    assert client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"view": "canvas"}).status_code == 422
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


def test_search(client, env, db):
    he, hv, hx = env["he"], env["hv"], env["hx"]
    plans = _new(client, he, title="Plans", place="project", body="Hire two people.")
    _new(client, he, title="Hiring", parent=plans["id"], body="We talk to " + "lots of people " * 20 + "about capsids today.")
    _new(client, he, title="Old trip", place="archive", body="Lisbon")
    r = client.get("/api/v1/notes/search?q=capsids", headers=hv)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["total"] == 1
    hit = out["pages"][0]
    assert (hit["title"], hit["parent"], hit["namespace"], hit["author"]) == ("Hiring", plans["id"], "pods", "person")
    assert hit["snippet"].startswith("…") and hit["snippet"].endswith("about capsids today.")
    # title matches first; a place narrows it; no words: everything, the latest changed first
    assert [p["title"] for p in client.get("/api/v1/notes/search?q=hir", headers=hv).json()["pages"]] == ["Hiring", "Plans"]
    assert [p["title"] for p in client.get("/api/v1/notes/search?place=archive", headers=hv).json()["pages"]] == ["Old trip"]
    assert client.get("/api/v1/notes/search?ns=pods", headers=hv).json()["total"] == 3
    # a thing's page is found too, unless only free notes are asked for
    _new(client, he, title="Episode 1", about=f"recording:{env['a']}", body="capsids again")
    assert client.get("/api/v1/notes/search?q=capsids", headers=hv).json()["total"] == 2
    assert client.get("/api/v1/notes/search?q=capsids&free=true", headers=hv).json()["total"] == 1
    # people outside the namespace find nothing in it
    assert client.get("/api/v1/notes/search?q=capsids", headers=hx).json()["total"] == 0
    assert client.get("/api/v1/notes/search?ns=pods", headers=hx).status_code == 404


def test_snippet():
    assert notebook.snippet("one two three", "four") is None
    assert notebook.snippet("one two three", "") is None
    assert notebook.snippet("a  short\ntext", "short") == "a short text"
    long = " ".join(f"w{i}" for i in range(200))
    s = notebook.snippet(long, "w100")
    assert s.startswith("…w") and s.endswith("…") and "w100" in s and len(s) <= notebook.SNIPPET + 2


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
    # what it cost counts for the page and its namespace, and the page's readers see it with the requests that changed it
    led = client.get("/api/v1/activity", headers=he, params={"resource": f"note_page:{p['id']}"}).json()
    kinds = {(e["kind"], e["action"]) for e in led}
    assert ("out", "model.chat") in kinds and any(k == "in" for k, _ in kinds), kinds
    assert any(e["kind"] == "out" and f"space:{store.ns_id(db, got['namespace'])}" in e["resources"] for e in led)
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


def test_page_history(client, env, db, cfg, llm):
    from app.domain import ai_tools

    he, hv = env["he"], env["hv"]
    p = _new(client, he, title="Plan", body="First draft.")
    # a person's edits in one sitting make one version: what the page was before they started
    client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "Second draft."})
    client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"body": "Third draft."})
    client.patch(f"/api/v1/notes/{p['id']}", headers=he, json={"place": "project"})  # not the text: no version
    h = client.get(f"/api/v1/notes/{p['id']}/history", headers=hv).json()["versions"]
    assert [(v["author"], v["by"], v["size"]) for v in h] == [("person", "ed@x.io", len("First draft."))]
    # the assistant's change is a version of its own, and can be undone
    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'ed@x.io'")["id"]
    pods = store.ns_id(db, "pods")
    box = ai_tools.Toolbox(db, cfg, {"id": uid, "email": "ed@x.io"}, {pods}, {pods}, {}, None)
    box.call("update_note", {"note_id": p["id"], "body": "Rewritten by the assistant."})
    h = client.get(f"/api/v1/notes/{p['id']}/history", headers=he).json()["versions"]
    assert [v["author"] for v in h] == ["assistant", "person"]
    before = client.get(f"/api/v1/notes/{p['id']}/history/{h[0]['id']}", headers=hv).json()
    assert before["body"] == "Third draft."
    assert client.post(f"/api/v1/notes/{p['id']}/history/{h[0]['id']}/restore", headers=hv).status_code == 403
    back = client.post(f"/api/v1/notes/{p['id']}/history/{h[0]['id']}/restore", headers=he).json()
    assert back["body"] == "Third draft." and back["doc_stale"] is False
    # restoring is itself undoable: the assistant's text is the newest version now
    h = client.get(f"/api/v1/notes/{p['id']}/history", headers=he).json()["versions"]
    assert (
        h[0]["why"].startswith("restored")
        and client.get(f"/api/v1/notes/{p['id']}/history/{h[0]['id']}", headers=he).json()["body"] == "Rewritten by the assistant."
    )
    # the model's refinement keeps a version too; another page's version isn't this page's
    other = _new(client, he, title="Other", body="x")
    assert client.get(f"/api/v1/notes/{other['id']}/history/{h[0]['id']}", headers=he).status_code == 404
    _age(db, p["id"])
    notebook.refine_due(db, cfg)
    h2 = client.get(f"/api/v1/notes/{p['id']}/history", headers=he).json()["versions"]
    assert h2[0]["author"] == "assistant" and h2[0]["by"] is None and len(h2) == len(h) + 1
    # deleting the page keeps its history while it can be put back; deleting it for good drops it
    client.delete(f"/api/v1/notes/{p['id']}", headers=he)
    assert db.rows("SELECT id FROM note_version WHERE page = $p", p=p["id"])
    client.delete(f"/api/v1/notes/deleted/{p['id']}", headers=he)
    assert not db.rows("SELECT id FROM note_version WHERE page = $p", p=p["id"])


def test_link_suggestions(client, env, db, cfg):
    from app.domain import ai_tools, topics

    he, hv = env["he"], env["hv"]
    pods = store.ns_id(db, "pods")
    caps = topics.create(db, pods, "Capsid design", ["capsids"])
    ada, acme = _entity(db, "Ada Lovelace", "PERSON"), _entity(db, "Acme", "ORG")
    _entity(db, "Friday", "DATE")
    body = (
        f"Met Ada Lovelace about capsids on Friday; @[Acme](entity:{acme['id']}) ships them.\n\n"
        "acme is lowercase here, and Ada Lovelace again."
    )
    p = _new(client, he, title="Meeting", body=body)
    got = client.get(f"/api/v1/notes/{p['id']}/suggestions", headers=hv).json()
    # in the order the text names them, each once; what's linked already, dates, and lowercase single words are left out
    assert [(x["sign"], x["target"], x["label"]) for x in got] == [
        ("@", f"entity:{ada['id']}", "Ada Lovelace"),
        ("#", f"topic:{caps}", "Capsid design"),
    ]
    # the assistant sees them when it reads the note
    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'ed@x.io'")["id"]
    box = ai_tools.Toolbox(db, cfg, {"id": uid, "email": "ed@x.io"}, {pods}, {pods}, {}, None)
    import json

    out = json.loads(box.call("read_note", {"note_id": p["id"]})[0])
    assert out["could_link"] == [f"@[Ada Lovelace](entity:{ada['id']})", f"#[Capsid design](topic:{caps})"]
    assert client.get(f"/api/v1/notes/{_new(client, he, title='Empty')['id']}/suggestions", headers=he).json() == []


def test_home_suggestions(client, env, db, cfg):
    from app.domain import ai_tools

    he, hv = env["he"], env["hv"]
    pods = store.ns_id(db, "pods")
    ada, acme = _entity(db, "Ada Lovelace", "PERSON"), _entity(db, "Acme", "ORG")
    launch = _new(client, he, title="Capsid launch", place="project", body=f"@[Acme](entity:{acme['id']}) and @[Ada](entity:{ada['id']})")
    health = _new(client, he, title="Health", place="area")
    _new(client, he, title="Reading list", place="resource", body=f"@[Acme](entity:{acme['id']})")  # not a project or area
    old = _new(client, he, title="Capsid launch retro", place="archive")
    homes = lambda pid, h=hv: client.get(f"/api/v1/notes/{pid}/homes", headers=h).json()  # noqa: E731

    # it links what the project links, and names the project's title: the project first, with why
    p = _new(client, he, title="Call with Acme", body=f"About the capsid launch. @[Acme](entity:{acme['id']}) is in.")
    got = homes(p["id"])
    assert [(x["page"], x["place"]) for x in got] == [(launch["id"], "project")]
    assert got[0]["score"] == 3 and got[0]["why"] == ["both link Acme", "it names capsid, launch"]
    # a link to an area page is enough on its own
    q = _new(client, he, title="Sleep", body=f"See @[Health](page:{health['id']}).")
    assert [x["page"] for x in homes(q["id"])] == [health["id"]]
    # one shared word isn't enough; nothing in common, nothing suggested
    assert homes(_new(client, he, title="Launch party")["id"]) == []
    assert homes(_new(client, he, title="Groceries", body="milk")["id"]) == []
    # nested, filed as a project or area, archived, or a thing's page: none
    client.post(f"/api/v1/notes/{p['id']}/move", headers=he, json={"parent": launch["id"]})
    assert homes(p["id"]) == []
    assert homes(launch["id"]) == [] and homes(old["id"]) == []
    # a project can't be suggested as a home for a page it sits inside
    client.post(f"/api/v1/notes/{launch['id']}/move", headers=he, json={"parent": q["id"]})
    assert [x["page"] for x in homes(q["id"])] == [health["id"]]
    # the assistant sees them when it reads the note, and moves it with update_note
    uid = db.one("SELECT record::id(id) AS id FROM account WHERE email = 'ed@x.io'")["id"]
    box = ai_tools.Toolbox(db, cfg, {"id": uid, "email": "ed@x.io"}, {pods}, {pods}, {}, None)
    import json

    out = json.loads(box.call("read_note", {"note_id": q["id"]})[0])
    assert out["could_go_in"] == [{"parent_id": health["id"], "title": "Health", "place": "area", "why": ["it links to this page"]}]


def test_page_files(client, env, db, cfg, folder):
    he, hv, hx = env["he"], env["hv"], env["hx"]
    p = _new(client, he, title="Diagrams")
    png = b"\x89PNG\r\n\x1a\n" + b"pixels" * 100
    svg = b"<svg onload='alert(1)'/>"
    k = lambda b: base64.urlsafe_b64encode(hashlib.sha256(b).digest()).decode()  # noqa: E731
    url = f"/api/v1/notes/{p['id']}/blobs"
    put = lambda key, body, h=he, ctype="image/png", name="flow.png": client.put(  # noqa: E731
        url, params={"key": key, "name": name}, content=body, headers={**h, "Content-Type": ctype}
    )
    KP, KS = k(png), k(svg)
    r = put(KP, png)
    assert r.status_code == 200, r.text
    assert (r.json()["key"], r.json()["name"], r.json()["type"], r.json()["size"]) == (KP, "flow.png", "image/png", len(png))
    assert put(KP, png).status_code == 200  # the same key is the same bytes: kept once
    assert [f["key"] for f in client.get(f"/api/v1/notes/{p['id']}/files", headers=hv).json()] == [KP]
    # anyone who reads the page reads its files; images show in place
    r = client.get(url, params={"key": KP}, headers=hv)
    assert r.status_code == 200 and r.content == png
    assert r.headers["content-type"] == "image/png" and r.headers["content-disposition"].startswith("inline")
    assert r.headers["x-content-type-options"] == "nosniff"
    # what a browser would run comes as bytes, to save
    assert put(KS, svg, ctype="image/svg+xml", name="x.svg").status_code == 200
    r = client.get(url, params={"key": KS}, headers=hv)
    assert r.headers["content-type"] == "application/octet-stream" and r.headers["content-disposition"].startswith("attachment")
    # only editors add; outsiders see nothing; keys are the editor's hashes
    assert put(k(b"x"), b"x", h=hv).status_code == 403
    assert client.get(url, params={"key": KP}, headers=hx).status_code in (403, 404)
    assert put("bad key!", b"x").status_code == 400
    assert put(k(b"other"), b"not the same bytes").status_code == 400  # the key is the bytes' hash
    assert put(k(b""), b"").status_code == 400
    assert client.get(url, params={"key": "hashOfNothing"}, headers=hv).status_code == 404
    # kept where Settings → Storage says: here, data_dir/objects
    kept = list((folder / "data" / "objects" / "notes").rglob("*"))
    assert len([k for k in kept if k.is_file()]) == 2
    # removing one removes what was kept; deleting the page keeps its files until it's deleted for good
    assert client.delete(f"/api/v1/notes/{p['id']}/files", params={"key": KS}, headers=he).status_code == 200
    assert client.get(url, params={"key": KS}, headers=hv).status_code == 404
    assert client.delete(f"/api/v1/notes/{p['id']}", headers=he).status_code == 200
    assert len([k for k in (folder / "data" / "objects" / "notes").rglob("*") if k.is_file()]) == 1
    assert client.post(f"/api/v1/notes/deleted/{p['id']}/restore", headers=he).status_code == 200
    assert client.get(url, params={"key": KP}, headers=hv).content == png  # back with the page
    assert client.delete(f"/api/v1/notes/{p['id']}", headers=he).status_code == 200
    assert client.delete(f"/api/v1/notes/deleted/{p['id']}", headers=hv).status_code == 403
    assert client.delete(f"/api/v1/notes/deleted/{p['id']}", headers=he).status_code == 200
    assert not [k for k in (folder / "data" / "objects" / "notes").rglob("*") if k.is_file()]
    assert not db.rows("SELECT id FROM note_file")


def test_recently_deleted(client, env, db, cfg):
    he, hv, hx = env["he"], env["hv"], env["hx"]
    plans = _new(client, he, title="Plans", place="project", body="See @[Ideas](page:999).")
    hiring = _new(client, he, title="Hiring", parent=plans["id"])
    budget = _new(client, he, title="Budget", parent=plans["id"], body=f"For @[Plans](page:{plans['id']}).")
    client.patch(f"/api/v1/notes/{plans['id']}", headers=he, json={"body": "Version two."})
    # deleting moves what's inside up a level, and the page into recently deleted
    assert client.delete(f"/api/v1/notes/{plans['id']}", headers=he).status_code == 200
    assert client.get(f"/api/v1/notes/{plans['id']}", headers=hv).status_code == 404
    tree = client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]
    assert sorted((p["title"], p["parent"]) for p in tree) == [("Budget", None), ("Hiring", None)]
    assert client.get("/api/v1/notes/search?q=version", headers=hv).json()["total"] == 0
    gone = client.get("/api/v1/notes/deleted?ns=pods", headers=hv).json()
    assert [(d["id"], d["title"], d["namespace"], d["deleted_by"]) for d in gone] == [(plans["id"], "Plans", "pods", "ed@x.io")]
    assert client.get("/api/v1/notes/deleted", headers=hx).json() == []
    assert client.get("/api/v1/notes/deleted?ns=pods", headers=hx).status_code == 404
    # a link to it shows without a name while it's gone
    links = client.get(f"/api/v1/notes/{budget['id']}", headers=hv).json()["links"]
    assert links[0]["target"] == f"page:{plans['id']}" and links[0]["name"] is None
    # viewers can't put it back; editors can, where it was, with what was inside it, its links and history
    assert client.post(f"/api/v1/notes/deleted/{plans['id']}/restore", headers=hv).status_code == 403
    r = client.post(f"/api/v1/notes/deleted/{plans['id']}/restore", headers=he)
    assert r.status_code == 200, r.text
    back = r.json()
    assert (back["id"], back["title"], back["place"], back["body"]) == (plans["id"], "Plans", "project", "Version two.")
    tree = client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]
    assert sorted((p["title"], p["parent"]) for p in tree) == [("Budget", plans["id"]), ("Hiring", plans["id"]), ("Plans", None)]
    assert [b["page"] for b in back["backlinks"]] == [budget["id"]]
    assert client.get(f"/api/v1/notes/{plans['id']}/history", headers=hv).json()["versions"]
    assert client.get("/api/v1/notes/deleted?ns=pods", headers=hv).json() == []
    assert client.post(f"/api/v1/notes/deleted/{plans['id']}/restore", headers=he).status_code == 404
    # a page that moved out meanwhile stays where it is
    assert client.delete(f"/api/v1/notes/{plans['id']}", headers=he).status_code == 200
    client.post(f"/api/v1/notes/{hiring['id']}/move", headers=he, json={"parent": budget["id"]})
    client.post(f"/api/v1/notes/deleted/{plans['id']}/restore", headers=he)
    tree = {p["title"]: p["parent"] for p in client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]}
    assert tree == {"Plans": None, "Budget": plans["id"], "Hiring": budget["id"]}
    # a thing's page comes back as its page, unless the thing has a new one by now: then as a free note
    a = env["a"]
    about = _new(client, he, title="Episode", about=f"recording:{a}", body="First take")
    client.delete(f"/api/v1/notes/{about['id']}", headers=he)
    assert client.get(f"/api/v1/notes/about/recording/{a}", headers=hv).json()["id"] is None
    again = _new(client, he, title="Episode", about=f"recording:{a}", body="Second take")
    back = client.post(f"/api/v1/notes/deleted/{about['id']}/restore", headers=he).json()
    assert back["about"] is None and back["body"] == "First take"
    assert client.get(f"/api/v1/notes/about/recording/{a}", headers=hv).json()["id"] == again["id"]
    client.delete(f"/api/v1/notes/{again['id']}", headers=he)
    assert client.post(f"/api/v1/notes/deleted/{again['id']}/restore", headers=he).json()["about"] == f"recording:{a}"


def test_deleted_pages_go_after_a_while(client, env, db, cfg):
    he = env["he"]
    old, new = _new(client, he, title="Old"), _new(client, he, title="New")
    client.delete(f"/api/v1/notes/{old['id']}", headers=he)
    client.delete(f"/api/v1/notes/{new['id']}", headers=he)
    db.q("UPDATE $r SET deleted_at = '2000-01-01T00:00:00+00:00'", r=R("note_trash", old["id"]))
    assert notebook.purge_due(db, cfg) == 1
    assert [d["title"] for d in client.get("/api/v1/notes/deleted?ns=pods", headers=he).json()] == ["New"]
    assert not db.rows("SELECT id FROM note_version WHERE page = $p", p=old["id"])


def test_pins(client, env, db):
    he, hv, hx = env["he"], env["hv"], env["hx"]
    a = _new(client, he, title="Plans")
    b = _new(client, he, title="Hiring", parent=a["id"])
    # a viewer pins a note for themselves; nobody else sees it pinned, and the page itself doesn't change
    r = client.put(f"/api/v1/notes/{b['id']}/pin", headers=hv)
    assert r.status_code == 200 and r.json()["pinned"] is True
    assert "pinned_by" not in r.json()
    assert r.json()["updated_at"] == b["updated_at"]
    assert client.put(f"/api/v1/notes/{b['id']}/pin", headers=hv).status_code == 200  # twice is once
    mine = {p["id"]: p["pinned"] for p in client.get("/api/v1/notes?ns=pods", headers=hv).json()["pages"]}
    theirs = {p["id"]: p["pinned"] for p in client.get("/api/v1/notes?ns=pods", headers=he).json()["pages"]}
    assert mine == {a["id"]: False, b["id"]: True} and theirs == {a["id"]: False, b["id"]: False}
    assert client.get(f"/api/v1/notes/{b['id']}", headers=hv).json()["pinned"] is True
    assert len(db.values("SELECT VALUE pinned_by FROM $r", r=R("note_page", b["id"]))[0]) == 1
    # outside the namespace it isn't there to pin
    assert client.put(f"/api/v1/notes/{b['id']}/pin", headers=hx).status_code == 404
    r = client.delete(f"/api/v1/notes/{b['id']}/pin", headers=hv)
    assert r.status_code == 200 and r.json()["pinned"] is False
    # there's a limit, per person and namespace
    old = notebook.PINS_MAX
    notebook.PINS_MAX = 1
    try:
        assert client.put(f"/api/v1/notes/{a['id']}/pin", headers=hv).status_code == 200
        r = client.put(f"/api/v1/notes/{b['id']}/pin", headers=hv)
        assert r.status_code == 400 and "unpin one first" in r.json()["detail"]
        assert client.put(f"/api/v1/notes/{b['id']}/pin", headers=he).status_code == 200
    finally:
        notebook.PINS_MAX = old


def test_daily_notes(client, env, db):
    he, hv = env["he"], env["hv"]
    r = client.post("/api/v1/notes/today", headers=he, json={"ns": "pods", "date": "2026-10-15"})
    assert r.status_code == 200, r.text
    day = r.json()
    assert (day["title"], day["date"]) == ("2026-10-15", "2026-10-15")
    root = client.get(f"/api/v1/notes/{day['parent']}", headers=he).json()
    assert root["title"] == "Daily notes" and root["parent"] is None
    # the same day again is the same note, even renamed; the next day goes beside it
    client.patch(f"/api/v1/notes/{day['id']}", headers=he, json={"title": "Launch day"})
    assert client.post("/api/v1/notes/today", headers=he, json={"ns": "pods", "date": "2026-10-15"}).json()["id"] == day["id"]
    nxt = client.post("/api/v1/notes/today", headers=he, json={"ns": "pods", "date": "2026-10-16"}).json()
    assert nxt["id"] != day["id"] and nxt["parent"] == root["id"]
    # without a date it's the server's today
    assert client.post("/api/v1/notes/today", headers=he, json={"ns": "pods"}).json()["date"] == store.now()[:10]
    # editors only, and a date that is one
    assert client.post("/api/v1/notes/today", headers=hv, json={"ns": "pods"}).status_code == 403
    assert client.post("/api/v1/notes/today", headers=he, json={"ns": "pods", "date": "soon"}).status_code == 400
    # the daily notes' note, when deleted, is made again the next time
    assert client.delete(f"/api/v1/notes/{root['id']}", headers=he).status_code == 200
    again = client.post("/api/v1/notes/today", headers=he, json={"ns": "pods", "date": "2026-10-17"}).json()
    assert again["parent"] not in (None, root["id"])


def test_markdown_export(client, env, db):
    import io
    import zipfile

    he, hv, hx = env["he"], env["hv"], env["hx"]
    plans = _new(client, he, title="Plans: Q4", summary="What's next", place="project")
    old = _new(client, he, title="Hiring", parent=plans["id"])
    _new(client, he, title="Budget", parent=plans["id"])
    _new(client, he, title="budget", parent=plans["id"])  # the same title twice in one place
    ada = _entity(db, "Ada Lovelace", "PERSON")
    body = f"Talk to @[Ada](entity:{ada['id']}) about @[Hiring](page:{old['id']})."
    client.patch(f"/api/v1/notes/{plans['id']}", headers=he, json={"body": body})
    client.patch(f"/api/v1/notes/{old['id']}", headers=he, json={"title": "Hiring plan"})
    _new(client, he, title="Ada's page", about=f"entity:{ada['id']}")

    r = client.get(f"/api/v1/notes/{plans['id']}/markdown", headers=hv)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert "Plans  Q4.md" not in r.headers["content-disposition"] and "Plans Q4.md" in r.headers["content-disposition"]
    assert r.text.startswith('---\ntitle: "Plans: Q4"\n')
    assert 'place: "project"' in r.text and 'summary: "What\'s next"' in r.text
    assert r.text.endswith("Talk to Ada about [[Hiring plan]].\n")  # the page's title now, the entity's name
    assert client.get(f"/api/v1/notes/{plans['id']}/markdown", headers=hx).status_code == 404

    r = client.get("/api/v1/notes/export?ns=pods", headers=hv)
    assert r.status_code == 200 and r.headers["content-type"] == "application/zip"
    names = sorted(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
    assert names == [
        "Pages of things/Entities/Ada's page.md",
        "Plans Q4.md",
        "Plans Q4/Budget.md",
        "Plans Q4/Hiring plan.md",
        "Plans Q4/budget (2).md",
    ]
    assert client.get("/api/v1/notes/export?ns=pods", headers=hx).status_code == 404
