"""Splitting and merging transcript lines: the lines after move with their corrections, chapters and history; words
keep their timings; analysis finds the mentions again."""

from __future__ import annotations

import json

import pytest

from app.domain import ingest, search, store, transcript
from tests.helpers import drain, login, make_user, seed

R = store.R
TIMED = {
    "segments": [
        {
            "start": 0.0,
            "end": 4.0,
            "text": "Hello there, this is Lens speaking.",
            "speaker": "Ann",
            "words": [
                {"word": " Hello", "start": 0.0, "end": 0.4},
                {"word": " there,", "start": 0.5, "end": 0.9},
                {"word": " this", "start": 1.6, "end": 1.8},
                {"word": " is", "start": 1.9, "end": 2.0},
                {"word": " Lens", "start": 2.1, "end": 2.6},
                {"word": " speaking.", "start": 2.7, "end": 3.6},
            ],
        },
        {"start": 4.5, "end": 6.0, "text": "Nice to meet you.", "speaker": "Ben"},
        {"start": 6.5, "end": 8.0, "text": "Likewise, Ann.", "speaker": "Ann"},
    ]
}


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    return {
        "a": a,
        "call": call,
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hx": login(client, "out@x.io", "outsider password 1"),
    }


def _lines(client, rid, h):
    return client.get(f"/api/v1/recordings/{rid}/player", headers=h).json()["segments"]


def _seg_ids(db, rid):
    return sorted(db.values("SELECT VALUE record::id(id) FROM segment WHERE recording = $r", r=rid))


def test_split_and_merge_back(client, env, db, cfg):
    a, he = env["a"], env["he"]
    url = f"/api/v1/recordings/{a}/segments"
    before = _lines(client, a, he)
    first = before[0]["text"]
    at = first.index("Today")
    # what points at line numbers: a correction of line 3, people's entity corrections on lines 0 and 2, chapters
    assert client.patch(f"{url}/3", headers=he, json={"text": before[3]["text"] + " Indeed."}).status_code == 200
    drain(db, cfg)
    base, pods = a * store.SEG, store.ns_id(db, "pods")
    dyno_id = db.one("SELECT record::id(id) AS id FROM entity WHERE name = 'Dyno Therapeutics' AND space = $s", s=pods)["id"]
    for idx, key, target in ((0, "dyno therapeutics", dyno_id), (2, "capsid", 77)):
        o = {"segment": base + idx, "key": key, "recording": a, "space": pods, "target": target}
        db.q("UPSERT $r CONTENT $d", r=R("entity_override", f"{base + idx}:{key}"), d=o)
    sections = db.rows("SELECT idx, seg0, seg1 FROM section WHERE recording = $r ORDER BY idx", r=a)

    # split line 0 inside "Today" too: it goes at the start of the word
    r = client.post(f"{url}/0/split", headers=he, json={"at": at + 2})
    assert r.status_code == 200, r.text
    assert r.json()["job"]
    lines = _lines(client, a, he)
    assert len(lines) == len(before) + 1
    assert (lines[0]["text"], lines[1]["text"]) == (first[:at].rstrip(), first[at:])
    assert lines[0]["t0"] == before[0]["t0"] and lines[1]["t1"] == before[0]["t1"]
    assert lines[0]["t1"] == lines[1]["t0"] and before[0]["t0"] < lines[1]["t0"] < before[0]["t1"]
    assert lines[0]["s"] == lines[1]["s"] == before[0]["s"]  # both keep the speaker
    assert [x["text"] for x in lines[2:]] == [x["text"] for x in before[1:3]] + [before[3]["text"] + " Indeed."] + [
        x["text"] for x in before[4:]
    ]
    assert _seg_ids(db, a) == [base + i for i in range(len(lines))]
    # the correction of old line 3 is now line 4's; the split is in the history at line 0
    edits = client.get(f"/api/v1/recordings/{a}/edits", headers=he).json()
    assert [(e["idx"], e["kind"]) for e in edits] == [(0, "split"), (4, None)]
    assert edits[0]["after"] == {"at": at, "t": lines[1]["t0"]}
    # entity corrections follow their lines; the split line's hold for both parts
    over = db.rows("SELECT segment, key FROM entity_override WHERE recording = $r ORDER BY segment", r=a)
    assert [(o["segment"] - base, o["key"]) for o in over] == [(0, "dyno therapeutics"), (1, "dyno therapeutics"), (3, "capsid")]
    shifted = db.rows("SELECT idx, seg0, seg1 FROM section WHERE recording = $r ORDER BY idx", r=a)
    assert [(s["seg0"] + (s["seg0"] > 0), s["seg1"] + 1) for s in sections] == [(s["seg0"], s["seg1"]) for s in shifted]
    # analysis finds the mentions again, on the right lines; search finds the new line
    drain(db, cfg)
    ents = client.get(f"/api/v1/recordings/{a}/player", headers=he).json()["entities"]
    dyno = next(e for e in ents if e["name"] == "Dyno Therapeutics")
    assert 1 in dyno["segs"] and 0 not in dyno["segs"]
    hit = next(h for h in search.search(db, "machine learning")["hits"] if h["recording_id"] == a)
    assert "Today" in hit["snippet"]

    # merge them back: the text as it was, the lines after move back, the first line's corrections win
    r = client.post(f"{url}/0/merge", headers=he)
    assert r.status_code == 200, r.text
    lines = _lines(client, a, he)
    assert [x["text"] for x in lines[:3]] == [first, before[1]["text"], before[2]["text"]]
    assert (lines[0]["t0"], lines[0]["t1"]) == (before[0]["t0"], before[0]["t1"])
    assert _seg_ids(db, a) == [base + i for i in range(len(before))]
    over = db.rows("SELECT segment, key FROM entity_override WHERE recording = $r ORDER BY segment", r=a)
    assert [(o["segment"] - base, o["key"]) for o in over] == [(0, "dyno therapeutics"), (2, "capsid")]
    edits = client.get(f"/api/v1/recordings/{a}/edits", headers=he).json()
    assert [(e["idx"], e["kind"]) for e in edits] == [(0, "merge"), (0, "split"), (3, None)]
    assert edits[0]["after"] == {"at": at, "t": edits[1]["after"]["t"]}  # where to split it again
    assert edits[0]["before"]["next"] == first[at:]
    actions = db.values("SELECT VALUE action FROM audit_log WHERE string::starts_with(action, 'transcript.')")
    assert sorted(actions) == ["transcript.edit", "transcript.merge", "transcript.split"]


def test_split_gives_the_rest_to_another_speaker(client, env, db):
    a, he = env["a"], env["he"]
    lines = _lines(client, a, he)
    text = lines[1]["text"]  # Bob: "Wow, really? Dyno Therapeutics trained a model …"
    alice = int(lines[0]["s"][1:])
    url = f"/api/v1/recordings/{a}/segments/1/split"
    calls_speaker = db.values("SELECT VALUE record::id(id) FROM speaker WHERE space = $s", s=store.ns_id(db, "calls"))[0]
    assert client.post(url, headers=he, json={"at": text.index("Dyno"), "speaker": calls_speaker}).status_code == 400
    mid = (lines[1]["t0"] + lines[1]["t1"]) // 2
    assert client.post(url, headers=he, json={"at": text.index("Dyno"), "t": lines[1]["t1"]}).status_code == 400  # not inside
    r = client.post(url, headers=he, json={"at": text.index("Dyno"), "t": mid, "speaker": alice})
    assert r.status_code == 200, r.text
    after = _lines(client, a, he)
    assert (after[1]["text"], after[1]["s"], after[2]["s"]) == ("Wow, really?", lines[1]["s"], f"s{alice}")
    assert after[2]["t0"] == mid
    edit = client.get(f"/api/v1/recordings/{a}/edits", headers=he).json()[0]
    assert edit["after"]["speaker"] == alice and edit["before"]["speaker"] == int(lines[1]["s"][1:])
    # unassigned: null
    r = client.post(f"/api/v1/recordings/{a}/segments/2/split", headers=he, json={"at": after[2]["text"].index("trained"), "speaker": None})
    assert r.status_code == 200 and _lines(client, a, he)[3]["s"] is None


def test_who_splits_and_what_is_checked(client, env, db):
    a, he = env["a"], env["he"]
    url = f"/api/v1/recordings/{a}/segments"
    text = _lines(client, a, he)[0]["text"]
    assert client.post(f"{url}/0/split", headers=env["hv"], json={"at": 5}).status_code == 403
    assert client.post(f"{url}/0/merge", headers=env["hv"]).status_code == 403
    assert client.post(f"{url}/0/split", headers=env["hx"], json={"at": 5}).status_code == 404
    assert client.post(f"/api/v1/recordings/{env['call']}/segments/0/merge", headers=he).status_code == 404
    assert client.post(f"{url}/0/split", headers=he, json={"at": 0}).status_code == 422
    assert client.post(f"{url}/0/split", headers=he, json={"at": 3}).status_code == 400  # inside the first word
    assert client.post(f"{url}/0/split", headers=he, json={"at": len(text)}).status_code == 400
    assert client.post(f"{url}/0/split", headers=he, json={"at": 5, "colour": "red"}).status_code == 422
    assert client.post(f"{url}/99/split", headers=he, json={"at": 5}).status_code == 404
    last = len(_lines(client, a, he)) - 1
    assert client.post(f"{url}/{last}/merge", headers=he).status_code == 404  # nothing after it
    assert client.post(f"{url}/99/merge", headers=he).status_code == 404


def test_words_keep_their_timings(client, env, db, cfg):
    rid = ingest.import_text(db, cfg, "pods", json.dumps(TIMED), title="Timed")
    he = env["he"]
    lines = _lines(client, rid, he)
    text = lines[0]["text"]
    assert lines[0]["w"][:2] == [[0, 5, 0, 400], [6, 12, 500, 900]]  # character ranges of the text, ms
    assert len(lines[0]["w"]) == 6 and "w" not in lines[1]
    # a correction keeps the timings of the words the line still has
    client.patch(f"/api/v1/recordings/{rid}/segments/0", headers=he, json={"text": text.replace("Lens", "Lense")})
    w = _lines(client, rid, he)[0]["w"]
    assert len(w) == 5 and [x[2] for x in w] == [0, 500, 1600, 1900, 2700]
    # a split starts the second part when its first word was said, and each part keeps its own words
    r = client.post(f"/api/v1/recordings/{rid}/segments/0/split", headers=he, json={"at": text.index("this")})
    assert r.status_code == 200, r.text
    a, b = _lines(client, rid, he)[:2]
    assert (a["text"], a["t1"], b["t0"]) == ("Hello there,", 1600, 1600)
    assert [x[2] for x in a["w"]] == [0, 500] and [x[2] for x in b["w"]] == [1600, 1900, 2700]
    assert b["w"][0][:2] == [0, 4]  # "this", at the start of the new line
    # merging puts the words together again
    client.post(f"/api/v1/recordings/{rid}/segments/0/merge", headers=he)
    m = _lines(client, rid, he)[0]
    assert m["text"] == "Hello there, this is Lense speaking." and [x[2] for x in m["w"]] == [0, 500, 1600, 1900, 2700]


def test_alignment_and_joining():
    ws = [("Hello", 0, 400), ("world.", 500, 900)]
    assert transcript.align("Hello world.", ws) == [[0, 5, 0, 400], [6, 12, 500, 900]]
    assert transcript.align("Well, hello there world!", ws) == [[6, 11, 0, 400], [18, 24, 500, 900]]
    assert transcript.align("", ws) == [] and transcript.align("Hello", []) == []
    cjk = [("你好", 0, 300), ("世界", 400, 800)]
    assert transcript.align("你好，世界", cjk) == [[0, 2, 0, 300], [3, 5, 400, 800]]
    assert transcript._join("你好，", "世界") == "你好，世界"
    assert transcript._join("Hello ", " world") == "Hello world"
    assert transcript._boundary("Hello world", 8) == 6 and transcript._boundary("你好世界", 2) == 2
    assert transcript.words({"words": "not json"}) == [] and transcript.words({"words": json.dumps([["a"], ["b", 1, 2]])}) == [("b", 1, 2)]
