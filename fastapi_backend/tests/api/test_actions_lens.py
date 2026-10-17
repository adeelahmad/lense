"""Automation actions that change an asset or write a note (docs/automations.md, Actions): set_field keeps the change
in the asset's history by the automation, add_to_collection never shows an asset to new people on its own, and
write_note writes a page (adding to a thing's own page) or a note on an asset, private ones running at once."""

from __future__ import annotations

import pytest

from app.domain import actions, fields, hierarchy, metadata, notebook, notes, store
from tests import fake_llm
from tests.api._assist import start_llm
from tests.helpers import make_user, seed


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    srv = start_llm(cfg)
    yield create_app(cfg, db, background=False)
    fake_llm.Handler.usage = None
    srv.shutdown()


@pytest.fixture
def env(app, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    return {
        "a": a,
        "call": call,
        "pods": pods,
        "me": make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"}),
        "viewer": make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"}),
        "status": fields.create(db, pods, "Status", "choice", "resource", options=["new", "filed"])["id"],
    }


def run(db, cfg, env, action, params, dry=False, **kw):
    intent = {
        "key": "51-n6",
        "action": action,
        "params": params,
        "effect": "lens",
        "risk": "routine",
        "untrusted": False,
        "spaces": [env["pods"]],
        "run_as": env["me"],
        "node": "n6",
        "automation": 4,
        "run": 51,
        **kw,
    }
    return actions.perform(db, cfg, intent, None, dry)


def mine(env, rid):
    return {
        "event": {"object": {"id": f"lens:recording/{rid}"}, "lens": {"spaces": [env["pods"]]}},
        "automation": {"spaces": [env["pods"]]},
    }


def test_on_the_asset_the_activity_is_about_they_run_at_once_and_private_notes_always(env):
    ctx = mine(env, env["a"])
    sf, ac, wn = (actions.get(n) for n in ("set_field", "add_to_collection", "write_note"))
    assert actions.resolve_risk(sf, {"field": "Status", "asset": f"lens:recording/{env['a']}"}, ctx) == "routine"
    assert actions.resolve_risk(sf, {"field": "Status", "asset": env["call"]}, ctx) == "ask"
    assert actions.resolve_risk(ac, {"collection": 1, "asset": env["a"]}, ctx) == "routine"
    assert actions.effect_of(wn, {"text": "x", "on": "asset"}) == "self"
    assert actions.effect_of(wn, {"text": "x", "on": "asset", "shared": True}) == "lens"
    assert actions.resolve_risk(wn, {"text": "x", "on": "asset", "asset": env["call"]}, ctx) == "routine"  # only theirs
    assert actions.resolve_risk(wn, {"text": "x", "about": f"lens:recording/{env['a']}"}, ctx) == "routine"
    assert actions.resolve_risk(wn, {"text": "x"}, ctx) == "ask"  # a page about nothing in particular
    assert actions.describe("set_field", {"field": "Status", "value": "filed"}) == "Set Status on the asset it's about to “filed”"


def test_set_field_is_kept_in_the_assets_history_by_the_automation(db, cfg, env):
    rid, fid = env["a"], env["status"]
    got = run(db, cfg, env, "set_field", {"field": "status", "value": "filed", "asset": f"lens:recording/{rid}"}, dry=True)
    assert got["would"].startswith("set Status on") and got["changed"] and not fields.values_of(fields.resource_fields(db, rid)[1])
    got = run(db, cfg, env, "set_field", {"field": fid, "value": "filed", "asset": rid})
    assert got == {"asset": rid, "field": fid, "label": "Status", "value": "filed", "changed": True}
    assert fields.values_of(fields.resource_fields(db, rid)[1]) == {fid: "filed"}
    last = metadata.history(db, f"recording:{rid}")[0]
    assert last["by"] == "automation: Automation 4" and last["changed"] == ["fields"]
    audit = db.one("SELECT email, detail FROM audit_log WHERE action = 'fields.save'")
    assert audit["email"] == "automation: Automation 4" and audit["detail"]["for"] == "me@x.io"
    assert run(db, cfg, env, "set_field", {"field": "Status", "value": "filed", "asset": rid})["changed"] is False
    with pytest.raises(ValueError, match="Status is one of: new, filed"):
        run(db, cfg, env, "set_field", {"field": "Status", "value": "lost", "asset": rid})
    with pytest.raises(ValueError, match="no field Colour describes"):
        run(db, cfg, env, "set_field", {"field": "Colour", "value": "red", "asset": rid})
    with pytest.raises(ValueError, match="vi can't edit assets in that namespace"):
        run(db, cfg, env, "set_field", {"field": "Status", "value": "new", "asset": rid}, run_as=env["viewer"])


def test_add_to_collection_never_shows_an_asset_to_new_people_on_its_own(db, cfg, env):
    rid, pods = env["a"], env["pods"]
    inbox = hierarchy.create(db, pods, "Inbox")
    got = run(db, cfg, env, "add_to_collection", {"collection": inbox, "asset": rid})
    assert got == {"asset": rid, "collection": inbox, "name": "Inbox", "moved": True}
    assert db.one("SELECT collection FROM $r", r=store.R("recording", rid))["collection"] == inbox
    assert db.one("SELECT email FROM audit_log WHERE action = 'recording.collection'")["email"] == "automation: Automation 4"
    # a collection someone outside the namespace was given a role on
    shared = hierarchy.create(db, pods, "With Sam")
    hierarchy.give(db, shared, make_user(db, "sam@x.io", "sam password 1"), "viewer")
    got = run(db, cfg, env, "add_to_collection", {"collection": shared, "asset": rid}, dry=True)
    assert got["newly_see"] == ["sam"]
    with pytest.raises(ValueError, match="would show it to sam, who can't see it now"):
        run(db, cfg, env, "add_to_collection", {"collection": shared, "asset": rid})
    assert db.one("SELECT collection FROM $r", r=store.R("recording", rid))["collection"] == inbox
    assert run(db, cfg, env, "add_to_collection", {"collection": shared, "asset": rid}, risk="ask")["moved"]  # approved
    other = hierarchy.create(db, store.ns_id(db, "calls"), "Elsewhere")
    with pytest.raises(ValueError, match="in another namespace"):
        run(db, cfg, env, "add_to_collection", {"collection": other, "asset": rid})
    with pytest.raises(ValueError, match="there's no collection 999"):
        run(db, cfg, env, "add_to_collection", {"collection": 999, "asset": rid})


def test_write_note_writes_a_page_adds_to_a_things_page_or_notes_an_asset(db, cfg, env):
    rid, pods = env["a"], env["pods"]
    got = run(db, cfg, env, "write_note", {"text": "School trip on Friday\n\nBring a packed lunch."})
    page = notebook.get(db, got["page"])
    assert page["title"] == "School trip on Friday" and page["space"] == pods and page["created_by"] == "automation:4"
    assert page["author"] == "assistant" and got["url"] == f"/notes/{got['page']}" and got["namespace"] == "pods"
    # a thing has one page: a second note about it goes at its end
    first = run(db, cfg, env, "write_note", {"text": "Filed.", "about": f"lens:recording/{rid}", "title": "Episode"})
    again = run(db, cfg, env, "write_note", {"text": "Checked.", "about": f"recording:{rid}"})
    assert again["page"] == first["page"] and again["added"] and notebook.get(db, first["page"])["body"].endswith("Filed.\n\nChecked.")
    assert run(db, cfg, env, "write_note", {"text": "x", "about": f"recording:{rid}"}, dry=True)["would"].startswith("add to the page")
    # a private note on an asset is the person's
    got = run(db, cfg, env, "write_note", {"text": "Listen again", "on": "asset", "asset": rid})
    n = notes.get(db, got["note"])
    assert n["account"] == env["me"] and not n["shared"] and [x["id"] for x in notes.visible(db, rid, env["viewer"])] == []
    got = run(db, cfg, env, "write_note", {"text": "For everyone", "on": "asset", "asset": rid, "shared": True})
    assert [x["text"] for x in notes.visible(db, rid, env["viewer"])] == ["For everyone"]
    # a viewer may keep private notes, not write pages or shared notes
    assert run(db, cfg, env, "write_note", {"text": "mine", "on": "asset", "asset": rid}, run_as=env["viewer"])["note"]
    with pytest.raises(ValueError, match="vi can't write pages in pods"):
        run(db, cfg, env, "write_note", {"text": "x"}, run_as=env["viewer"])
    with pytest.raises(ValueError, match="vi can't edit assets"):
        run(db, cfg, env, "write_note", {"text": "x", "on": "asset", "asset": rid, "shared": True}, run_as=env["viewer"])
    with pytest.raises(ValueError, match="calls isn't one of this automation's namespaces"):
        run(db, cfg, env, "write_note", {"text": "x", "namespace": "calls"})
    with pytest.raises(ValueError, match="a page is about an asset, entity"):
        run(db, cfg, env, "write_note", {"text": "x", "about": "email:3"})
