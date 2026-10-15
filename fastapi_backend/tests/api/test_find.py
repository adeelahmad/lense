"""Finding resources by any attribute (domain/find.py): faces, voices, entities, topics, kinds, content types, dates and
the rest, by id or by name, through the API, the library list, the MCP server and the assistant, each result saying why
it matched."""

from __future__ import annotations

import pytest

from app.domain import ai_tools, content_types, faces, find, store, topics
from tests.api.test_mcp import rpc
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def env(client, db, cfg, folder):
    """ep1 and ep2 in pods, call in calls; Adeel's face on ep2 and on the call; topics Health > Gene therapy, ep1 about
    the narrower one; the call chosen as an email; dates across two years."""
    ep1, ep2, call = seed(db, cfg, folder)
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    for rid, day in ((ep1, "2026-09-30T09:00:00"), (ep2, "2026-03-02T10:00:00"), (call, "2025-11-20T08:00:00")):
        db.q("UPDATE $r SET recorded_at = $d", r=R("recording", rid), d=day)
    adeel = faces.new_face(db, pods, name="Adeel")
    adeel_too = faces.new_face(db, calls, name="Adeel")
    stranger = faces.new_face(db, pods)
    db.q(
        "INSERT INTO face_track $rows",
        rows=[
            {
                "recording": ep2,
                "space": pods,
                "local": "P1",
                "face": adeel,
                "spans": [[5000, 9000], [20000, 25000]],
                "screen_ms": 9000,
                "first_ms": 5000,
            },
            {"recording": ep1, "space": pods, "local": "P1", "face": stranger, "spans": [[0, 1000]], "screen_ms": 1000, "first_ms": 0},
            {
                "recording": call,
                "space": calls,
                "local": "P1",
                "face": adeel_too,
                "spans": [[1000, 4000]],
                "screen_ms": 3000,
                "first_ms": 1000,
            },
        ],
    )
    health = topics.create(db, pods, "Health")
    gene = topics.create(db, pods, "Gene therapy", alt=["AAV"], broader=[health])
    topics.tag(db, gene, [ep1])
    content_types.choose(db, call, "email")
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ep1": ep1,
        "ep2": ep2,
        "call": call,
        "pods": pods,
        "calls": calls,
        "adeel": adeel,
        "adeel_too": adeel_too,
        "gene": gene,
        "health": health,
        "h": login(client, "root@x.io", "root password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def found(client, h, **body):
    r = client.post("/api/v1/find", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def ids(res):
    return [x["id"] for x in res["items"]]


def brief(why):
    """Reasons without the fields they leave empty."""
    return [
        {
            k: v
            for k, v in {**w, "moments": [{k: v for k, v in m.items() if v is not None} for m in w["moments"]]}.items()
            if v not in (None, [])
        }
        for w in why
    ]


def test_by_face_voice_entity_and_date(client, db, env):
    h, ep1, ep2, call = env["h"], env["ep1"], env["ep2"], env["call"]
    # a face by name matches that name in every namespace you read; each result says when it's on screen
    res = found(client, h, faces=["adeel"])
    assert ids(res) == [ep2, call]  # newest first
    assert {x["namespace"] for x in res["resolved"]["faces"]} == {"pods", "calls"}
    why = brief(res["items"][0]["why"])
    assert why == [{"by": "face", "name": "Adeel", "moments": [{"t0": 5000, "t1": 9000}, {"t0": 20000, "t1": 25000}]}]
    # by id, too, and an unnamed face by its label
    assert ids(found(client, h, faces=[env["adeel"]])) == [ep2]
    assert ids(found(client, h, faces=["face 2"])) == [ep1]
    # a voice: the speaker's first lines are the moments
    res = found(client, h, speakers=["Carol"])
    assert ids(res) == [ep2]
    (w,) = res["items"][0]["why"]
    assert w["by"] == "voice" and w["name"] == "Carol" and "exploit benchmark" in w["moments"][0]["text"]
    # an entity by any of its names; the lines it's mentioned in
    res = found(client, h, entities=["dyno therapeutics"])
    assert set(ids(res)) == {ep1, ep2, call}
    first = next(x for x in res["items"] if x["id"] == ep1)["why"][0]
    assert first["by"] == "entity" and len(first["moments"]) == 3 and "Dyno Therapeutics" in first["moments"][0]["text"]
    # AND across filters, OR inside one; dates as a day, a month or a year
    assert ids(found(client, h, entities=["Dyno Therapeutics"], speakers=["Dave"])) == [call]
    assert ids(found(client, h, faces=["Adeel"], date_from="2026")) == [ep2]
    assert ids(found(client, h, date_from="2026-03", date_to="2026-03")) == [ep2]
    assert ids(found(client, h, date_to="2025")) == [call]
    assert ids(found(client, h, speakers=["Bob", "Dave"], sort="date")) == [call, ep1]


def test_by_topic_type_content_type_and_text(client, db, env):
    h, ep1, ep2, call = env["h"], env["ep1"], env["ep2"], env["call"]
    # a topic brings its narrower topics; any label works
    res = found(client, h, topics=["health"])
    assert ids(res) == [ep1] and res["resolved"]["narrower_topics"] == 1
    assert brief(res["items"][0]["why"]) == [{"by": "topic", "name": "Gene therapy"}]
    assert ids(found(client, h, topics=["aav"])) == [ep1]
    # content types by key or label; kinds as a list
    res = found(client, h, content_types=["Email"])
    assert ids(res) == [call] and brief(res["items"][0]["why"]) == [{"by": "content_type", "name": "Email"}]
    assert ids(found(client, h, content_types=["text"])) == [ep1, ep2]
    db.q("UPDATE $r SET source = 'audio'", r=R("recording", ep2))
    assert ids(found(client, h, types=["audio", "video"])) == [ep2]
    # words said in them: best matching first, with the moments
    res = found(client, h, text="capsid")
    best = client.get("/api/v1/search", params={"q": "capsid"}, headers=h).json()["hits"][0]["recording_id"]
    assert ids(res)[0] == best and set(ids(res)) == {ep1, call}  # the best matching moment's resource first
    w = next(x for x in res["items"] if x["id"] == ep1)["why"][0]
    assert w["by"] == "words" and w["name"] == "capsid" and len(w["moments"]) == 3 and "capsid" in w["moments"][0]["text"]
    assert res["text"]["mode"] == "keyword" and res["text"]["capped"] is False
    # words and attributes together
    assert ids(found(client, h, text="capsid", namespace="calls")) == [call]
    assert ids(found(client, h, text="capsid", faces=["Adeel"])) == [call]
    # emotions heard
    assert ids(found(client, h, emotions=["fear"])) == [ep2]


def test_names_that_match_nothing_and_scope(client, db, env):
    h, hv = env["h"], env["hv"]
    r = client.post("/api/v1/find", json={"speakers": ["Carl"]}, headers=h)
    assert r.status_code == 400 and 'no speaker called "Carl"' in r.json()["detail"] and "carol" in r.json()["detail"]
    r = client.post("/api/v1/find", json={"entities": ["Dino Therapeutics"]}, headers=h)
    assert r.status_code == 400 and "no entity called" in r.json()["detail"]
    r = client.post("/api/v1/find", json={"content_types": ["vlog"]}, headers=h)
    assert r.status_code == 400 and "podcast" in r.json()["detail"]
    r = client.post("/api/v1/find", json={"whatever": 1}, headers=h)
    assert r.status_code == 422
    # a viewer of pods finds nothing of calls, even by a name only calls has
    assert ids(found(client, hv, faces=["Adeel"])) == [env["ep2"]]
    r = client.post("/api/v1/find", json={"speakers": ["Dave"]}, headers=hv)
    assert r.status_code == 400
    r = client.post("/api/v1/find", json={"faces": [env["adeel_too"]]}, headers=hv)
    assert r.status_code == 400 and "isn't in a namespace you can read" in r.json()["detail"]
    assert client.post("/api/v1/find", json={"namespace": "calls"}, headers=hv).status_code == 404


def test_library_list_filters_by_the_same(client, db, env):
    h, ep1, ep2, call = env["h"], env["ep1"], env["ep2"], env["call"]

    def listing(**params):
        r = client.get("/api/v1/recordings", params=params, headers=h)
        assert r.status_code == 200, r.text
        return [x["id"] for x in r.json()]

    assert listing(face=[env["adeel"], env["adeel_too"]]) == [ep1, ep2, call][1:]
    dyno = db.values("SELECT VALUE record::id(id) FROM entity WHERE key = 'dyno therapeutics' AND space = $s", s=env["calls"])
    assert listing(entity=dyno) == [call]
    assert listing(topic=[env["gene"]]) == [ep1]
    assert listing(topic=[env["health"]]) == []  # the list takes a topic as it is; find brings the narrower ones
    assert listing(content_type="email") == [call]
    assert listing(emotion="Happy") == [ep1]


def test_find_for_agents(client, db, cfg, env):
    # MCP: names, results with links to each moment
    hv = env["hv"]
    _, tokens = _grant(client, hv, scope="read")
    res = rpc(client, _bearer(tokens), "tools/call", {"name": "find", "arguments": {"faces": ["Adeel"], "types": ["transcript"]}})["result"]
    assert res["isError"] is False
    out = res["structuredContent"]
    assert out["total"] == 1 and out["results"][0]["recording_id"] == env["ep2"]
    (w,) = out["results"][0]["why"]
    assert w["by"] == "face" and w["moments"][0] == {
        "time": "0:05",
        "until": "0:09",
        "url": f"http://localhost:3000/resources/{env['ep2']}?t=5",
    }
    bad = rpc(client, _bearer(tokens), "tools/call", {"name": "find", "arguments": {"topics": ["Wealth"]}})["result"]
    assert bad["isError"] is True and "no topic called" in bad["content"][0]["text"]
    # the assistant: numbered moments to cite, within the conversation's scope
    me = {"id": 1, "email": "root@x.io"}
    box = ai_tools.Toolbox(db, cfg, me, {env["pods"], env["calls"]}, set(), {"recordings": [env["ep1"], env["call"]]}, None)
    assert "find_resources" in {t["function"]["name"] for t in box.specs()}
    got, summary = box.t_find_resources(entities=["Dyno Therapeutics"])
    assert summary == "Found 2 resource(s)"
    assert {x["recording_id"] for x in got["results"]} == {env["ep1"], env["call"]}
    refs = [m["ref"] for x in got["results"] for w in x["why"] for m in w["moments"]]
    assert refs == list(range(1, len(refs) + 1)) and len(box.refs) == len(refs)


def test_clean_checks_the_filter():
    assert find.day("2024-02", end=True).isoformat() == "2024-02-29"
    assert find.day("2026", end=True).isoformat() == "2026-12-31"
    with pytest.raises(ValueError, match="a day"):
        find.day("last week")
    with pytest.raises(ValueError, match="not colour"):
        find.clean({"colour": "red"})
    with pytest.raises(ValueError, match="at most 20"):
        find.clean({"tags": [str(i) for i in range(21)]})
