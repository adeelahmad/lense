"""For you (docs/timeline.md, For you): the timeline reordered by each person's own reactions, comments and marks,
with why each item is where it is; Latest stays the default; agents ask for it over MCP and in the assistant."""

from __future__ import annotations

import json

from app.domain import ai_tools, for_you, store
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"
ASSETS = {"kinds": "asset.added"}


def _env(client, db, cfg, folder):
    ids = seed(db, cfg, folder)
    root = make_user(db, "root@x.io", "root password 1", admin=True)
    vi = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    # the two episodes were brought in by Vi, the call by Lens itself
    for rid in ids[:2]:
        db.q("UPDATE $r SET created_by = $who", r=store.R("recording", rid), who=vi)
    return {
        "ids": [f"create-recording-{i}" for i in ids],
        "root": root,
        "vi": vi,
        "hr": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def _ids(page):
    return [a["id"] for a in page["items"]]


def test_for_you_learns_from_reactions_comments_and_marks_and_says_why(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    h = env["hr"]
    latest = client.get(URL, params=ASSETS, headers=h).json()
    assert _ids(latest) == [call, pods2, pods1] and "why" not in latest or latest.get("why") == {}
    # with no signals yet, For you is Latest, with nothing to explain
    page = client.get(URL, params={**ASSETS, "order": "for_you"}, headers=h).json()
    assert _ids(page) == [call, pods2, pods1] and page["why"] == {}
    # a 👍 on the first episode lifts what Vi brought in: the second episode comes before the newer call
    client.post(f"{URL}/{pods1}/react", json={"emoji": "👍"}, headers=h)
    page = client.get(URL, params={**ASSETS, "order": "for_you"}, headers=h).json()
    assert _ids(page) == [pods2, pods1, call]
    assert page["why"][pods2] == "You often react to vi" and page["why"][pods1] == page["why"][pods2]
    assert page["why"][call] == "You often react to import received items"  # the kind: the strongest signal it shares
    # the same with a comment and an important mark; the order is the person's own: Vi's own view is untouched
    assert _ids(client.get(URL, params={**ASSETS, "order": "for_you"}, headers=env["hv"]).json()) == [pods2, pods1]
    # hiding the first episode outweighs the 👍: what Vi brought in goes last, and why says so
    client.post(f"{URL}/{pods1}/mark", json={"hidden": True}, headers=h)
    page = client.get(URL, params={**ASSETS, "order": "for_you"}, headers=h).json()
    assert _ids(page) == [call, pods2, pods1] and page["why"][pods2] == "You hid items from vi before"
    # the page's other fields are there as in Latest, and paging moves past the window
    assert page["marks"][pods1]["hidden"] is True and page["reactions"][pods1][0]["emoji"] == "👍"
    one = client.get(URL, params={**ASSETS, "order": "for_you", "limit": 1}, headers=h).json()
    assert _ids(one) == [call] and one["next"] is None
    assert client.get(URL, params={**ASSETS, "order": "newest"}, headers=h).status_code == 422
    # the pieces: signals, affinity and scores
    aff = for_you.affinity(db, cfg, {"account": env["root"], "spaces": None, "admin": True})
    assert aff["n"] == 2 and aff["actor"][f"lens:account/{env['vi']}"] == -2.0 and list(aff["kind"].values()) == [-2.0]
    s, why = for_you.score(
        {"published": "2026-01-01T00:00:00.000Z", "kind": "x", "actor": {}, "tag": []},
        aff,
        for_you.events._parse("2026-01-03T00:00:00.000Z"),
    )
    assert abs(s - 0.5) < 1e-9 and why is None


def test_opening_an_item_counts_for_you_a_little_each_time_up_to_a_few(client, db, cfg, folder):
    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    h = env["hr"]
    # one open of the first episode lifts what Vi brought in, as a 👍 does, for the one who opened it alone
    assert client.post(f"{URL}/{pods1}/open", headers=h).json() == {"ok": True}
    page = client.get(URL, params={**ASSETS, "order": "for_you"}, headers=h).json()
    assert _ids(page) == [pods2, pods1, call] and page["why"][pods2] == "You often react to vi"
    assert _ids(client.get(URL, params={**ASSETS, "order": "for_you"}, headers=env["hv"]).json()) == [pods2, pods1]
    # each open counts, up to OPENS of one item; a reader may say they opened what they see, and nothing else
    for _ in range(for_you.OPENS + 2):
        client.post(f"{URL}/{pods1}/open", headers=h)
    assert dict(for_you.signals(db, env["root"]))[pods1] == for_you.WEIGHTS["open"] * for_you.OPENS
    assert client.post(f"{URL}/{call}/open", headers=env["hv"]).status_code == 404
    assert client.post(f"{URL}/{pods2}/open", headers=env["hv"]).status_code == 200
    assert client.post(f"{URL}/no-such-thing/open", headers=h).status_code == 404


def test_agents_read_for_you_too(client, db, cfg, folder):
    from tests.api.test_mcp import tool

    env = _env(client, db, cfg, folder)
    pods1, pods2, call = env["ids"]
    h = env["hr"]
    client.post(f"{URL}/{pods2}/comments", json={"text": "Keep these coming"}, headers=h)
    got = tool(client, h, "timeline", kinds=["asset.added"], order="for_you")
    assert [a["id"] for a in got["items"]] == [pods2, pods1, call] and got["why"][pods1] == "You often react to vi"
    # the assistant's tool says why on each item it placed
    spaces = {store.ns_id(db, "pods"), store.ns_id(db, "calls")}
    box = ai_tools.Toolbox(db, cfg, {"id": env["root"], "email": "root@x.io"}, spaces, set(), {}, None, admin=True)
    out, said = box.call("timeline", {"kinds": ["asset.added"], "order": "for_you"})
    acts = json.loads(out)["activities"]
    assert [a["id"] for a in acts] == [pods2, pods1, call]
    assert acts[0]["why"] == "You often react to vi" and acts[2]["why"] == "You often react to import received items"
    assert said.startswith("Read 3")
    assert "order is latest or for_you" in box.call("timeline", {"order": "best"})[0]
