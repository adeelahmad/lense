"""Reactions and comments on the timeline (docs/timeline.md, Reactions and comments): everyone who sees an activity
reacts to it with an emoji and talks on it in threads, as on a post in a feed; a page carries everyone's reactions and
each item's comment count; agents do the same over MCP."""

from __future__ import annotations

from app.domain import auth, event_reactions, events, store
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"
ASSETS = {"kinds": "asset.added"}


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    root = make_user(db, "root@x.io", "root password 1", admin=True)
    vi = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ids": [f"create-recording-{i}" for i in ids],
        "rids": ids,
        "root": root,
        "vi": vi,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def test_reactions_are_shared_counted_and_taken_back(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    hr, hv = env["hr"], env["hv"]
    assert client.get(URL, headers=hr).json()["reactions"] == {}
    # the admin and the viewer both 👍 the first episode; the viewer ❤️s it too
    got = client.post(f"{URL}/{pods1}/react", json={"emoji": "👍"}, headers=hr).json()
    assert got == {"reactions": [{"emoji": "👍", "count": 1, "mine": True}]}
    client.post(f"{URL}/{pods1}/react", json={"emoji": "👍"}, headers=hv)
    got = client.post(f"{URL}/{pods1}/react", json={"emoji": "❤️"}, headers=hv).json()["reactions"]
    assert got == [{"emoji": "👍", "count": 2, "mine": True}, {"emoji": "❤️", "count": 1, "mine": True}]
    # everyone sees the same counts on the page, each with their own `mine`
    page = client.get(URL, params=ASSETS, headers=hr).json()
    assert list(page["reactions"]) == [pods1]
    assert page["reactions"][pods1] == [{"emoji": "👍", "count": 2, "mine": True}, {"emoji": "❤️", "count": 1, "mine": False}]
    assert client.get(URL, params=ASSETS, headers=hv).json()["reactions"][pods1][1]["mine"] is True
    # giving one again changes nothing; taking it back drops it, and the row goes when nothing is left
    assert client.post(f"{URL}/{pods1}/react", json={"emoji": "👍"}, headers=hr).json()["reactions"][0]["count"] == 2
    assert db.one("SELECT count() AS n FROM event_reaction GROUP ALL")["n"] == 2
    got = client.post(f"{URL}/{pods1}/react", json={"emoji": "👍", "on": False}, headers=hr).json()["reactions"]
    assert got == [{"emoji": "👍", "count": 1, "mine": False}, {"emoji": "❤️", "count": 1, "mine": False}]
    assert db.values("SELECT VALUE account FROM event_reaction") == [env["vi"]]
    # each new reaction is a reaction.added activity on the stream (noisy: asked for by kind), by the person
    liked = client.get(URL, params={"kinds": "reaction.added"}, headers=hr).json()["items"]
    assert [(a["type"], a["actor"]["id"], a["object"]["emoji"], a["object"]["activity"]) for a in liked] == [
        ("Like", f"lens:account/{env['vi']}", "❤️", pods1),
        ("Like", f"lens:account/{env['vi']}", "👍", pods1),
        ("Like", f"lens:account/{env['root']}", "👍", pods1),
    ]
    assert liked[0]["object"]["id"] == f"lens:activity/{pods1}" and "reacted ❤️ to" in liked[0]["summary"]
    assert liked[0]["lens"]["untrusted"] == ["object.name"]
    # the viewer reacts only to what they see; a bad emoji, an unknown activity and a read-only token are refused
    assert client.post(f"{URL}/{call}/react", json={"emoji": "👍"}, headers=hv).status_code == 404
    assert client.post(f"{URL}/{pods2}/react", json={"emoji": "🦄"}, headers=hv).status_code == 400
    assert client.post(f"{URL}/create-recording-999/react", json={"emoji": "👍"}, headers=hr).status_code == 404
    tok = client.post("/api/v1/tokens", json={"name": "ro", "scope": "read"}, headers=hv).json()["token"]
    ro = {"Authorization": f"Bearer {tok}"}
    assert client.get(URL, headers=ro).json()["reactions"][pods1][0]["count"] == 1
    assert client.post(f"{URL}/{pods1}/react", json={"emoji": "👍"}, headers=ro).status_code == 403
    assert client.post(f"{URL}/{pods1}/react", json={"emoji": "👍"}).status_code == 401
    # a person removed takes their reactions with them
    auth.remove_account(db, env["vi"])
    assert db.rows("SELECT * FROM event_reaction") == []


def test_comments_on_an_activity_are_a_thread_everyone_who_sees_it_joins(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    hr, hv = env["hr"], env["hv"]
    rid = env["rids"][0]
    assert client.get(f"{URL}/{pods1}/comments", headers=hv).json() == []
    # a comment on an asset's arrival is the asset's own: its page shows it, and the timeline shows the page's
    c = client.post(f"{URL}/{pods1}/comments", json={"text": "Worth a listen"}, headers=hv).json()
    assert c["recording"] == rid and c["activity"] == pods1 and c["mine"] and c["can_delete"] and c["t0"] is None
    r = client.post(f"{URL}/{pods1}/comments", json={"text": "Agreed", "parent": c["id"]}, headers=hr).json()
    assert r["parent"] == c["id"] and r["created_by"] == "root@x.io"
    on_page = client.post(f"/api/v1/recordings/{rid}/comments", json={"text": "From the page", "t0": 1000}, headers=hr).json()
    assert on_page["activity"] is None
    got = client.get(f"{URL}/{pods1}/comments", headers=hv).json()
    assert [(x["text"], x["parent"]) for x in got] == [("Worth a listen", None), ("Agreed", c["id"]), ("From the page", None)]
    assert [x["text"] for x in client.get(f"/api/v1/recordings/{rid}/comments", headers=hv).json()] == [x["text"] for x in got]
    page = client.get(URL, params=ASSETS, headers=hr).json()
    assert page["comments"] == {pods1: 3}
    # each comment is a comment.added activity: a reply says so, and the asset is its target
    added = client.get(URL, params={"kinds": "comment.added"}, headers=hr).json()["items"]
    assert [a["object"]["content"] for a in added] == ["From the page", "Agreed", "Worth a listen"]
    assert added[1]["object"]["inReplyTo"] == f"lens:comment/{c['id']}" and added[1]["target"]["id"] == f"lens:recording/{rid}"
    # only its writer changes the text; the viewer can't resolve the admin's thread, the admin resolves anyone's
    assert client.patch(f"{URL}/{pods1}/comments/{c['id']}", json={"text": "Worth a listen!"}, headers=hr).status_code == 403
    assert client.patch(f"{URL}/{pods1}/comments/{c['id']}", json={"text": "Worth a listen!"}, headers=hv).json()["edited_at"]
    assert client.patch(f"{URL}/{pods1}/comments/{on_page['id']}", json={"resolved": True}, headers=hv).status_code == 403
    assert client.patch(f"{URL}/{pods1}/comments/{c['id']}", json={"resolved": True}, headers=hr).json()["resolved"] is True
    assert client.patch(f"{URL}/{pods1}/comments/{c['id']}", json={}, headers=hr).status_code == 400
    # a comment on another activity isn't on this one; the viewer sees no comments on what they can't see
    assert client.patch(f"{URL}/{pods2}/comments/{c['id']}", json={"text": "x"}, headers=hv).status_code == 404
    assert client.get(f"{URL}/{call}/comments", headers=hv).status_code == 404
    assert client.post(f"{URL}/{call}/comments", json={"text": "x"}, headers=hv).status_code == 404
    assert client.post(f"{URL}/{pods2}/comments", json={"text": "x", "parent": c["id"]}, headers=hv).status_code == 400
    # deleting: the writer, or an owner; a thread takes its replies
    assert client.delete(f"{URL}/{pods1}/comments/{on_page['id']}", headers=hv).status_code == 403
    assert client.delete(f"{URL}/{pods1}/comments/{c['id']}", headers=hv).status_code == 200
    assert [x["text"] for x in client.get(f"{URL}/{pods1}/comments", headers=hr).json()] == ["From the page"]
    assert client.get(URL, params=ASSETS, headers=hr).json()["comments"] == {pods1: 1}
    assert client.post(f"{URL}/{pods1}/comments", json={"text": "x"}).status_code == 401


def test_comments_on_a_logged_activity_follow_its_audience(client, db, cfg, folder, monkeypatch):
    monkeypatch.setattr(events, "_KINDS", dict(events._KINDS))
    events.kind("test_thing.happened", label="Thing happened", source="log", as_type="Announce", object_type="Note")
    env = _env(client, db, cfg, folder)
    hr, hv = env["hr"], env["hv"]
    pods = store.ns_id(db, "pods")
    shared = events.emit(
        db, "test_thing.happened", actor=events.LENS, obj={"type": "Note", "name": "x"}, space=pods, summary="Lens noted x"
    )
    private = events.emit(
        db,
        "test_thing.happened",
        actor=events.LENS,
        obj={"type": "Note", "name": "y"},
        space=pods,
        summary="Lens told the admin y",
        audience="owner",
        owner=env["root"],
    )
    # a comment on a logged activity has no asset: its own activity names the activity it is on
    c = client.post(f"{URL}/{shared}/comments", json={"text": "Noted"}, headers=hv).json()
    assert c["recording"] is None and c["activity"] == shared
    assert client.get(f"{URL}/{shared}/comments", headers=hr).json()[0]["text"] == "Noted"
    assert client.get(URL, params={"kinds": "test_thing.happened"}, headers=hv).json()["comments"] == {shared: 1}
    added = client.get(URL, params={"kinds": "comment.added"}, headers=hv).json()["items"][0]
    assert added["target"] == {"type": "Activity", "id": f"lens:activity/{shared}", "name": "Lens noted x"}
    assert added["object"]["url"] == "/timeline"
    # a comment on an activity only its owner sees is seen by the owner alone, as a comment and on the stream
    assert client.get(f"{URL}/{private}/comments", headers=hv).status_code == 404
    client.post(f"{URL}/{private}/comments", json={"text": "Just us"}, headers=hr)
    assert [a["object"]["content"] for a in client.get(URL, params={"kinds": "comment.added"}, headers=hv).json()["items"]] == ["Noted"]
    assert [a["object"]["content"] for a in client.get(URL, params={"kinds": "comment.added"}, headers=hr).json()["items"]] == [
        "Just us",
        "Noted",
    ]
    # reactions on it the same way
    client.post(f"{URL}/{private}/react", json={"emoji": "👀"}, headers=hr)
    assert client.get(URL, params={"kinds": "reaction.added"}, headers=hv).json()["items"] == []
    assert client.get(URL, params={"kinds": "reaction.added"}, headers=hr).json()["items"][0]["object"]["emoji"] == "👀"


def test_agents_react_and_comment_too(client, db, cfg, folder):
    from tests.api.test_mcp import tool, tool_error

    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    hv = env["hv"]
    got = tool(client, hv, "react_to_activity", id=pods1, emoji="🎉")
    assert got == {"reactions": [{"emoji": "🎉", "count": 1, "mine": True}]}
    assert tool(client, hv, "react_to_activity", id=pods1, emoji="🎉", on=False) == {"reactions": []}
    assert "not found" in tool_error(client, hv, "react_to_activity", id=call, emoji="🎉")
    c = tool(client, hv, "comment_on_activity", id=pods1, text="Listen to this one")
    assert c["activity"] == pods1 and c["mine"] is True
    r = tool(client, hv, "comment_on_activity", id=pods1, text="And again", parent=c["id"])
    assert r["parent"] == c["id"]
    listed = tool(client, hv, "activity_comments", id=pods1)["comments"]
    assert [x["text"] for x in listed] == ["Listen to this one", "And again"]
    assert "text is required" in tool_error(client, hv, "comment_on_activity", id=pods1, text=" ")
    assert tool(client, hv, "timeline", kinds=["asset.added"])["comments"] == {pods1: 2}
    assert event_reactions.check("👍") == "👍"
