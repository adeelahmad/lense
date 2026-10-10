"""Similar content: the resources most like a text, a resource, a topic or an entity, by meaning when it's set up and
always by key words, shared topics and shared entities, each citing why."""

from __future__ import annotations

import pytest

from app.domain import semantic, similar, store, topics
from tests import fake_llm
from tests.helpers import login, make_user, quiet, seed

R = store.R
MONEY = """Erin|N|I have to be honest, we can't afford the rent this month.
Frank|Sad|Cash is tight for everyone, the bills keep coming.
Erin|N|Maybe we should ask about a smaller budget for the studio.
Frank|N|Anyway, did you see the match last night?"""


def _all(db):
    return set(store.space_names(db))


def _archive(db, cfg, folder):
    from app.domain import analyze, ingest

    ids = seed(db, cfg, folder)
    p = folder / "money.txt"
    p.write_text(MONEY)
    ids.append(ingest.import_transcript(db, cfg, "pods", p, log=quiet))
    analyze.analyze_pending(db, cfg, log=quiet)
    return ids


@pytest.fixture
def llm(cfg):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    fake_llm.Handler.embeddings, fake_llm.Handler.embed_fail = True, False
    fake_llm.Handler.embedded.clear()
    semantic._QUERIES.clear()
    semantic._DOWN.clear()
    yield fake_llm.Handler
    fake_llm.Handler.embeddings = False
    srv.shutdown()


def _ids(res):
    return [x["recording_id"] for x in res["items"]]


def _entity(db, ns, name):
    return db.one("SELECT record::id(id) AS id FROM entity WHERE space = $s AND name = $n", s=store.ns_id(db, ns), n=name)["id"]


def test_like_a_text_by_its_key_words(db, cfg, folder):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    res = similar.similar(db, cfg, text="Designing a capsid with machine learning for gene therapy", spaces=_all(db))
    # no embeddings server: by the words alone, the ones the archive says least first
    assert res["basis"] == "text" and res["mode"] == "words" and not res["semantic"] and res["meaning"] is None
    assert {"capsid", "gene", "therapy"} <= set(res["terms"]) and "with" not in res["terms"]
    assert _ids(res) == [ep1, call]  # ep1 says more of them than the call; the money talk none
    top = res["items"][0]
    assert top["match"] == ["words"] and top["namespace"] == "pods" and top["media_kind"] == "transcript"
    assert {"capsid", "gene", "therapy"} <= set(top["words"]) and len(top["cites"]) == 3
    # each cites a moment to open, its words marked
    c = top["cites"][0]
    assert c["match"] == "words" and c["source"] == "said" and isinstance(c["idx"], int) and c["t0"] is not None
    assert "<mark>" in c["snippet"]
    # only the namespaces given, and never the resource it was selected in
    assert _ids(similar.similar(db, cfg, text="capsid samples", spaces={store.ns_id(db, "pods")})) == [ep1]
    assert call not in _ids(similar.similar(db, cfg, text="capsid samples", spaces=_all(db), exclude=call))
    assert similar.similar(db, cfg, text="capsid", spaces=set())["items"] == []
    # paging
    assert _ids(similar.similar(db, cfg, text="capsid gene therapy", spaces=_all(db), limit=1, offset=1)) == [call]
    with pytest.raises(ValueError):
        similar.similar(db, cfg, spaces=_all(db))
    with pytest.raises(ValueError):
        similar.similar(db, cfg, text="capsid", recording=ep1, spaces=_all(db))


def test_like_a_resource_by_words_topics_and_entities(db, cfg, folder):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    tid = topics.create(db, pods, "Gene therapy")
    topics.tag(db, tid, [ep1, ep2])
    res = similar.similar(db, cfg, recording=ep1, spaces=_all(db))
    assert res["basis"] == "recording" and res["label"] == "ep1" and res["terms"]
    assert ep1 not in _ids(res)  # never like itself
    by = {x["recording_id"]: x for x in res["items"]}
    # ep2 is about the same topic and mentions Dyno Therapeutics too; the call (another namespace) shares words
    assert _ids(res)[0] == ep2
    assert {"topics", "entities"} <= set(by[ep2]["match"])
    assert by[ep2]["topics"] == [{"id": tid, "label": "Gene therapy"}]
    assert "Dyno Therapeutics" in [e["name"] for e in by[ep2]["entities"]]
    assert "words" in by[call]["match"] and by[call]["cites"]
    assert money not in by  # nothing in common


def test_like_a_topic_and_an_entity(db, cfg, folder):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    pods = store.ns_id(db, "pods")
    tid = topics.create(db, pods, "Capsid", alt=["viral capsid"], definition="The protein shell of a virus.")
    topics.tag(db, tid, [ep1])
    res = similar.similar(db, cfg, topic=tid, spaces=_all(db))
    assert res["basis"] == "topic" and res["label"] == "Capsid" and "protein shell" in res["text"]
    by = {x["recording_id"]: x for x in res["items"]}
    assert _ids(res)[0] == ep1 and set(by[ep1]["match"]) == {"words", "topics"}
    assert by[call]["match"] == ["words"]  # says capsid, but isn't tagged
    with pytest.raises(KeyError):  # a topic of a namespace not given
        similar.similar(db, cfg, topic=tid, spaces={store.ns_id(db, "calls")})

    dyno = _entity(db, "pods", "Dyno Therapeutics")
    res = similar.similar(db, cfg, entity=dyno, spaces=_all(db))
    assert res["basis"] == "entity" and res["label"] == "Dyno Therapeutics"
    by = {x["recording_id"]: x for x in res["items"]}
    assert {ep1, ep2, call} <= set(by) and money not in by
    assert "entities" in by[ep1]["match"] and "entities" not in by[call]["match"]  # the call's is another namespace's


def test_by_meaning_when_set_up(db, cfg, folder, llm):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    semantic.index_pending(db, cfg, log=quiet)
    res = similar.similar(db, cfg, text="we are worried about our finances", spaces=_all(db))
    assert res["mode"] == "meaning" and res["semantic"]
    top = res["items"][0]
    assert top["recording_id"] == money and top["match"][0] == "meaning" and top["similarity"] >= 0.5
    assert top["cites"][0]["match"] == "meaning" and "afford the rent" in top["cites"][0]["snippet"]
    # a resource: the centre of its own passages
    assert semantic.centre(db, cfg, ep1) and semantic.centre(db, cfg, 999) is None
    res = similar.similar(db, cfg, recording=ep1, spaces=_all(db))
    by = {x["recording_id"]: x for x in res["items"]}
    assert ep1 not in by and by[ep2]["match"][0] == "meaning" and by[ep2]["similarity"] > 0.9
    assert by[ep2]["cites"][0]["match"] == "meaning" and by[ep2]["cites"][0]["idx"] == 0
    assert by[call]["match"] == ["words"]  # not alike enough in meaning, but it says capsid and Dyno Therapeutics
    # the server goes away: the words still answer, saying why
    llm.embed_fail = True
    semantic._QUERIES.clear()
    res = similar.similar(db, cfg, text="capsid samples from the lab", spaces=_all(db))
    assert res["mode"] == "words" and "unavailable" in res["meaning"] and _ids(res)


def test_similar_api(client, db, cfg, folder):
    ep1, ep2, call, money = _archive(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    hv = login(client, "vi@x.io", "viewer password 1")
    r = client.get("/api/v1/similar", params={"text": "capsid samples"}, headers=hv)
    assert r.status_code == 200, r.text
    assert [x["recording_id"] for x in r.json()["items"]] == [ep1]  # the call is in a namespace they can't read
    r = client.get("/api/v1/similar", params={"recording": ep1}, headers=hv).json()
    assert ep1 not in [x["recording_id"] for x in r["items"]] and call not in [x["recording_id"] for x in r["items"]]
    assert client.get("/api/v1/similar", params={"recording": call}, headers=hv).status_code == 404
    calls_dyno = _entity(db, "calls", "Dyno Therapeutics")
    assert client.get("/api/v1/similar", params={"entity": calls_dyno}, headers=hv).status_code == 404
    assert client.get("/api/v1/similar", params={"topic": 999}, headers=hv).status_code == 404
    assert client.get("/api/v1/similar", headers=hv).status_code == 422
    assert client.get("/api/v1/similar", params={"text": "  "}, headers=hv).status_code == 422
    assert client.get("/api/v1/similar", params={"text": "x", "recording": ep1}, headers=hv).status_code == 422
    assert client.get("/api/v1/similar", params={"text": "capsid"}).status_code == 401


def test_the_assistant_finds_similar_within_its_scope(db, cfg, folder):
    import json

    from app.domain import ai_tools

    ep1, ep2, call, money = _archive(db, cfg, folder)
    pods, calls = store.ns_id(db, "pods"), store.ns_id(db, "calls")
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, {pods, calls}, set(), {}, None)
    assert "find_similar" in [t["function"]["name"] for t in box.specs()]
    out, summary = box.call("find_similar", {"text": "send the capsid samples"})
    got = json.loads(out)
    assert {r["recording_id"] for r in got["results"]} == {ep1, call} and summary.startswith("Found 2")
    # each moment is numbered to cite, its text plain
    m = got["results"][0]["moments"][0]
    assert m["ref"] == 1 and "<mark>" not in m["text"] and "&#x27;" not in json.dumps(got)
    assert box.refs[0]["recording_id"] == got["results"][0]["recording_id"]
    # a conversation about one namespace finds nothing beyond it
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "e"}, {pods, calls}, set(), {"namespaces": ["pods"]}, None)
    assert {r["recording_id"] for r in json.loads(box.call("find_similar", {"text": "capsid samples"})[0])["results"]} == {ep1}
    assert "isn't available" in json.loads(box.call("find_similar", {"recording_id": call})[0])["error"]
