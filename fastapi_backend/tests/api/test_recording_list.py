"""The library list: server-side filters, sorting, paging and the total count, within the namespaces you can read."""

from __future__ import annotations

import pytest

from app.domain import jobs, library, store
from tests.helpers import login, make_user, seed

R = store.R


@pytest.fixture
def archive(client, db, cfg, folder):
    """Three seeded transcripts (ep1 and ep2 in pods, call in calls) with dates, lengths, statuses and media set."""
    a, b, call = seed(db, cfg, folder)
    for rid, patch in (
        (a, {"recorded_at": "2026-09-30T09:02:00", "duration_ms": 52 * 60000, "status": "analyzed", "summary": {"importance": 2}}),
        (b, {"recorded_at": "2026-09-12T08:41:00", "duration_ms": 22 * 60000, "status": "error", "source": "audio"}),
        (call, {"recorded_at": "2026-05-01T10:00:00", "duration_ms": 65 * 60000, "status": "new", "media": {"kind": "video"}}),
    ):
        db.q("UPDATE $r MERGE $p", r=R("recording", rid), p=patch)
    make_user(db, "root@x.io", "root password 1", admin=True)
    return {"a": a, "b": b, "call": call, "h": login(client, "root@x.io", "root password 1")}


def listing(client, h, **params):
    r = client.get("/api/v1/recordings", params=params, headers=h)
    assert r.status_code == 200, r.text
    return [x["id"] for x in r.json()], int(r.headers["x-total-count"])


def test_filters_combine_and_count(client, db, archive):
    a, b, call, h = archive["a"], archive["b"], archive["call"], archive["h"]
    assert listing(client, h) == ([a, b, call], 3)  # newest first
    # every word must match the title, the namespace or a speaker's name
    assert listing(client, h, q="carol")[0] == [b]
    assert listing(client, h, q="CALLS")[0] == [call]
    assert listing(client, h, q="ep alice")[0] == [a, b]
    assert listing(client, h, q="ep dave")[0] == []
    # statuses: any of them
    assert listing(client, h, status=["error", "new"]) == ([b, call], 2)
    assert listing(client, h, status="analyzed")[0] == [a]
    # speakers: any of the ids
    dave = db.values("SELECT VALUE record::id(id) FROM speaker WHERE name = 'Dave'")[0]
    carol = db.values("SELECT VALUE record::id(id) FROM speaker WHERE name = 'Carol'")[0]
    assert listing(client, h, speaker=[dave, carol])[0] == [b, call]
    # dates are inclusive days; recordings without a date never match a date filter
    db.q("UPDATE $r SET recorded_at = NONE", r=R("recording", call))
    assert listing(client, h, **{"from": "2026-09-12", "to": "2026-09-30"})[0] == [a, b]
    assert listing(client, h, to="2026-09-12")[0] == [b]
    assert listing(client, h, **{"from": "2026-09-13"})[0] == [a]
    # durations in seconds: at least min, shorter than max
    assert listing(client, h, min_duration=30 * 60, max_duration=60 * 60)[0] == [a]
    assert listing(client, h, min_duration=60 * 60)[0] == [call]
    assert listing(client, h, max_duration=10 * 60)[0] == []
    # media: video (probed), audio (an audio source), transcript (neither)
    assert listing(client, h, media="video")[0] == [call]
    assert listing(client, h, media="audio")[0] == [b]
    assert listing(client, h, media="transcript")[0] == [a]
    # filters combine with AND, and the total counts every page
    assert listing(client, h, ns="pods", status=["error", "analyzed"], q="alice") == ([a, b], 2)
    assert listing(client, h, ns="pods", status=["error", "analyzed"], limit=1) == ([a], 2)
    assert listing(client, h, ns="pods", status=["error", "analyzed"], limit=1, offset=1) == ([b], 2)


def test_job_states_and_attention(client, db, archive):
    a, b, h = archive["a"], archive["b"], archive["h"]
    assert listing(client, h, status="processing") == ([], 0)
    jid = jobs.enqueue(db, a, ["analyze"])
    assert listing(client, h, status="processing")[0] == [a]
    assert listing(client, h, processing="true")[0] == [a]
    assert listing(client, h, processing="true", status="error")[0] == []  # AND across filters
    assert listing(client, h, status=["processing", "error"])[0] == [a, b]  # OR inside one
    # a failed job counts until a later job for the same recording takes over
    db.q("UPDATE $j SET status = 'failed'", j=R("job", jid))
    assert listing(client, h, status="failed")[0] == [a]
    assert listing(client, h, attention="true")[0] == [a, b]  # failed job, errored recording
    later = jobs.enqueue(db, a, ["analyze"])
    assert listing(client, h, status="failed")[0] == []
    assert listing(client, h, status="processing")[0] == [a]
    db.q("UPDATE $j SET status = 'succeeded'", j=R("job", later))
    assert listing(client, h, attention="true")[0] == [b]
    # a voice match waiting for review needs a person too
    pods = store.ns_id(db, "pods")
    alice, bob = (
        db.values("SELECT VALUE record::id(id) FROM speaker WHERE space = $s AND name = $n", s=pods, n=n)[0] for n in ("Alice", "Bob")
    )
    db.q("CREATE suggestion CONTENT $d", d={"speaker": bob, "candidate": alice, "score": 0.4, "space": pods})
    assert listing(client, h, attention="true")[0] == [a, b]
    assert library.review_recordings(db, [pods]) == {a}
    db.q("DELETE $s", s=R("speaker", alice))  # a suggestion whose candidate is gone waits for nobody
    assert library.review_recordings(db, [pods]) == set()
    assert listing(client, h, attention="true", ns="calls")[0] == []


def test_sorting_with_missing_values_last(client, db, archive):
    a, b, call, h = archive["a"], archive["b"], archive["call"], archive["h"]
    db.q("UPDATE $r SET duration_ms = NONE, title = 'Zeta call'", r=R("recording", call))
    db.q("UPDATE $r SET title = 'alpha'", r=R("recording", b))
    assert listing(client, h, sort="title")[0] == [b, a, call]  # "alpha", "ep1", "Zeta call": case-insensitive
    assert listing(client, h, sort="-title")[0] == [call, a, b]
    assert listing(client, h, sort="duration")[0] == [b, a, call]
    assert listing(client, h, sort="-duration")[0] == [a, b, call]  # no duration: last either way
    assert listing(client, h, sort="date")[0] == [call, b, a]
    assert listing(client, h, sort="status")[0] == [b, call, a]  # error, new, …, analyzed
    assert listing(client, h, sort="-importance")[0] == [a, call, b]  # only ep1 has a summary; ties by id, same direction
    # speakers: how many different people; each seeded recording has two, so give ep2 a third
    bob = db.values("SELECT VALUE record::id(id) FROM speaker WHERE name = 'Bob'")[0]
    db.q("CREATE appearance CONTENT $d", d={"recording": b, "speaker": bob, "space": store.ns_id(db, "pods")})
    assert listing(client, h, sort="-speakers")[0] == [b, call, a]
    assert listing(client, h, sort="speakers")[0] == [a, call, b]
    assert client.get("/api/v1/recordings", params={"sort": "loudness"}, headers=h).status_code == 422


def test_bad_filters_and_access(client, db, archive):
    a, b, h = archive["a"], archive["b"], archive["h"]
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "nobody@x.io", "nobody password 1")
    hv, hn = login(client, "vi@x.io", "viewer password 1"), login(client, "nobody@x.io", "nobody password 1")
    # other namespaces look absent, in rows and in the count
    assert listing(client, hv) == ([a, b], 2)
    assert listing(client, hv, q="calls") == ([], 0)
    dave = db.values("SELECT VALUE record::id(id) FROM speaker WHERE name = 'Dave'")[0]
    assert listing(client, hv, speaker=dave) == ([], 0)
    assert client.get("/api/v1/recordings", params={"ns": "calls"}, headers=hv).status_code == 404
    assert listing(client, hn) == ([], 0)
    for bad in ({"status": "done"}, {"from": "yesterday"}, {"media": "pdf"}, {"min_duration": -1}, {"max_duration": 0}, {"limit": 0}):
        assert client.get("/api/v1/recordings", params=bad, headers=h).status_code == 422, bad
    assert client.get("/api/v1/recordings").status_code == 401
    with pytest.raises(ValueError):
        library.list_recordings(db, [1], sort="loudness")
    with pytest.raises(ValueError):
        library.list_recordings(db, [1], status=["done"])
    with pytest.raises(ValueError):
        library.list_recordings(db, [1], media="pdf")
