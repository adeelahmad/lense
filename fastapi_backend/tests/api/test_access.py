"""Roles isolate namespaces; share links and signed media links open one recording without signing in."""

from __future__ import annotations

import time
import urllib.parse

import pytest

from app.core import security
from app.domain import analyze, render
from tests.helpers import drain, login, make_user, quiet, seed, write_wav


def test_roles_isolate_namespaces(client, new_client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    render.build_reports(db, cfg, log=quiet)
    cv, ce, anon = client, new_client(), new_client()
    hv, he = login(cv, "vi@x.io", "viewer password 1"), login(ce, "ed@x.io", "editor password 1")
    assert [n["name"] for n in cv.get("/api/v1/namespaces", headers=hv).json()] == ["pods"]
    assert cv.get(f"/api/v1/recordings/{call}", headers=hv).status_code == 404  # other namespaces look absent
    assert cv.get(f"/api/v1/recordings/{a}", headers=hv).status_code == 200
    hits = cv.get("/api/v1/search", params={"q": "Dyno"}, headers=hv).json()
    assert hits["total"] >= 1 and all(h["namespace"] == "pods" for h in hits["hits"])
    assert cv.get("/api/v1/graph", headers=hv).json()["namespaces"] == ["pods"]
    assert cv.get("/api/v1/graph", params={"scope": "ns:calls"}, headers=hv).status_code == 404
    rep = cv.get(f"/api/v1/recordings/{a}", headers=hv).json()["report_url"]
    assert rep and rep.startswith("/reports/pods/") and "sig=" in rep  # reports: see test_roles_isolate_pages_and_admin
    sid = db.values("SELECT VALUE record::id(id) FROM speaker WHERE name = 'Bob'")[0]
    assert cv.post(f"/api/v1/speakers/{sid}", json={"name": "Robert"}, headers=hv).status_code == 403
    assert cv.post("/api/v1/import", json={"namespace": "pods", "text": "A: b\nC: d\nA: e"}, headers=hv).status_code == 403
    assert ce.post(f"/api/v1/speakers/{sid}", json={"name": "Robert"}, headers=he).status_code == 200
    assert ce.post("/api/v1/import", json={"namespace": "calls", "text": "A: b\nC: d\nA: e"}, headers=he).status_code == 404
    assert ce.post("/api/v1/import", json={"namespace": "brand-new", "text": "A: b\nC: d\nA: e"}, headers=he).status_code == 403
    r = ce.post(
        "/api/v1/import", json={"namespace": "pods", "title": "Standup", "text": "Ann: capsid update.\nBen: noted.\nAnn: done."}, headers=he
    )
    assert r.status_code == 200, r.text
    rid, jid = r.json()["id"], r.json()["job"]
    assert ce.get(f"/api/v1/jobs/{jid}", headers=he).json()["status"] == "queued"  # imports return at once
    assert drain(db, cfg) == 1
    assert (
        ce.get(f"/api/v1/recordings/{rid}", headers=he).json()["status"],
        ce.get(f"/api/v1/jobs/{jid}", headers=he).json()["status"],
    ) == (
        "analyzed",
        "succeeded",
    )
    tok = ce.post(f"/api/v1/recordings/{a}/share", json={"days": 1}, headers=he).json()["token"]
    assert anon.get(f"/api/v1/recordings/{a}/player").status_code == 401
    assert anon.get(f"/api/v1/recordings/{a}/player", params={"s": tok}).status_code == 200
    assert anon.get(f"/api/v1/recordings/{b}/player", params={"s": tok}).status_code == 401  # one recording only
    ce.delete(f"/api/v1/recordings/{a}/share", headers=he)
    assert anon.get(f"/api/v1/recordings/{a}/player", params={"s": tok}).status_code == 401  # revoked


def _mounted(app, *paths):
    # FastAPI 0.13x+ lists an included router in app.routes as one entry; its effective routes carry the full paths
    have = set()
    for r in app.routes:
        if hasattr(r, "effective_route_contexts"):
            have |= {getattr(c, "path_format", None) for c in r.effective_route_contexts()}
        else:
            have.add(getattr(r, "path", None))
    missing = [p for p in paths if p not in have]
    if missing:
        pytest.skip(f"not mounted yet (another area): {', '.join(missing)}")


def test_roles_isolate_pages_and_admin(app, client, new_client, db, cfg, folder):
    """The rest of the legacy roles test, against routes owned by the pages, users and admin areas."""
    _mounted(app, "/reports/{ns}/{name}", "/embed/{rid}", "/api/v1/namespaces/{name}/members", "/api/v1/settings")
    a, _b, _call = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    render.build_reports(db, cfg, log=quiet)
    cv, ce, anon = client, new_client(), new_client()
    hv, he = login(cv, "vi@x.io", "viewer password 1"), login(ce, "ed@x.io", "editor password 1")
    assert cv.get("/reports/pods/index.html", headers=hv).status_code == 200
    assert cv.get("/reports/calls/index.html", headers=hv).status_code == 404
    rep = cv.get(f"/api/v1/recordings/{a}", headers=hv).json()["report_url"]
    assert anon.get(rep).status_code == 200  # the signed report link works on its own
    tok = ce.post(f"/api/v1/recordings/{a}/share", json={"days": 1}, headers=he).json()["token"]
    r = anon.get(f"/embed/{a}", params={"s": tok})
    assert r.status_code == 200
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert ce.get("/api/v1/namespaces/pods/members", headers=he).status_code == 403
    assert cv.get("/api/v1/settings", headers=hv).status_code == 403


def _audio_recording(db, cfg, folder):
    from app.domain import ingest

    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.\n[00:02] Alice: Bye.")
    return ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet), wav


def _split(url):
    path, _, query = url.partition("?")
    return path, dict(urllib.parse.parse_qsl(query))


def test_signed_media_links(client, new_client, db, cfg, folder):
    rid, wav = _audio_recording(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    anon = new_client()

    # the player and the recording list hand out signed links; they work without a bearer token
    p = client.get(f"/api/v1/recordings/{rid}/player", headers=h).json()
    path, q = _split(p["audio"])
    assert path == f"/api/v1/recordings/{rid}/audio" and q["exp"] and q["sig"]
    r = anon.get(p["audio"], headers={"Range": "bytes=10-19"})
    assert (r.status_code, r.content) == (206, wav.read_bytes()[10:20])

    # tampered, expired, missing or moved signatures are refused
    assert anon.get(path).status_code == 401
    assert anon.get(path, params={**q, "sig": q["sig"][:-2] + ("AA" if q["sig"][-2:] != "AA" else "BB")}).status_code == 401
    assert anon.get(path, params={**q, "exp": str(int(q["exp"]) + 60)}).status_code == 401
    old = security.sign_path(path, ttl=-10)
    assert anon.get(old).status_code == 401
    assert anon.get(f"/api/v1/recordings/{rid}/wordcloud.svg", params=q).status_code == 401  # signed for another path

    # a signed link reads media only: the JSON endpoints still want a person
    signed_detail = security.sign_path(f"/api/v1/recordings/{rid}/player")
    assert anon.get(signed_detail).status_code == 200  # Access.recording accepts a link signed for exactly this path
    assert anon.get(f"/api/v1/recordings/{rid}").status_code == 401

    # bearer tokens keep working for media
    assert client.get(path, headers=h).status_code == 200

    # the recording's word cloud through a signed link
    cloud = security.sign_path(f"/api/v1/recordings/{rid}/wordcloud.svg")
    r = anon.get(cloud)
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml")

    # the embed link for viewers is signed and opens the embed page without a cookie or token
    e = client.get(f"/api/v1/recordings/{rid}/embed-link", headers=h).json()["url"]
    epath, eq = _split(e)
    assert epath == f"/embed/{rid}" and eq["sig"]
    assert anon.get(f"/api/v1/recordings/{rid}/embed-link").status_code == 401

    # namespace word clouds: signed in the namespace list
    ns = client.get("/api/v1/namespaces", headers=h).json()[0]
    assert "sig=" in ns["wordcloud"]
    assert anon.get(ns["wordcloud"]).status_code == 200
    assert anon.get(_split(ns["wordcloud"])[0]).status_code == 401
    assert anon.get("/api/v1/namespaces/calls/wordcloud.svg", params=_split(ns["wordcloud"])[1]).status_code == 401


def test_signed_links_expire(client, new_client, db, cfg, folder, monkeypatch):
    rid, _ = _audio_recording(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    url = client.get(f"/api/v1/recordings/{rid}/player", headers=h).json()["audio"]
    anon = new_client()
    assert anon.get(url).status_code == 200
    later = time.time() + security.settings.MEDIA_URL_EXPIRE_SECONDS + 60
    monkeypatch.setattr(security.time, "time", lambda: later)
    assert anon.get(url).status_code == 401


def test_recording_responses_sign_links(client, db, cfg, folder):
    rid, _ = _audio_recording(db, cfg, folder)
    analyze.analyze_pending(db, cfg, log=quiet)
    render.build_reports(db, cfg, log=quiet)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    h = login(client, "vi@x.io", "viewer password 1")
    d = client.get(f"/api/v1/recordings/{rid}", headers=h).json()
    assert d["role"] == "viewer" and d["namespace"] == "pods"
    path, q = _split(d["report_url"])
    assert path.startswith("/reports/pods/") and q["sig"]
    # a poster frame in the list is signed too
    db.q("CREATE shot CONTENT $d", d={"recording": rid, "idx": 0, "t0": 0, "t1": 1000, "frame": "f0.jpg"})
    row = [r for r in client.get("/api/v1/recordings", headers=h).json() if r["id"] == rid][0]
    path, q = _split(row["poster"])
    assert path == f"/api/v1/recordings/{rid}/frames/f0.jpg" and q["sig"]


def test_only_the_servers_links_are_signed(client, new_client, db, cfg, folder):
    """Text shaped like a media link (a title, a transcript line) is never signed: it could name any recording."""
    rid, _ = _audio_recording(db, cfg, folder)
    _a, _b, call = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    h = login(client, "ed@x.io", "editor password 1")
    victim = f"/api/v1/recordings/{call}/audio"  # in calls, where this editor has no role
    r = client.patch(f"/api/v1/recordings/{rid}", json={"title": victim}, headers=h)
    assert r.status_code == 200 and r.json()["title"] == victim
    assert next(x for x in client.get("/api/v1/recordings", headers=h).json() if x["id"] == rid)["title"] == victim
    assert client.get(f"/api/v1/recordings/{rid}", headers=h).json()["title"] == victim
    assert client.patch(f"/api/v1/recordings/{rid}/segments/0", json={"text": victim}, headers=h).status_code == 200
    p = client.get(f"/api/v1/recordings/{rid}/player", headers=h).json()
    assert p["title"] == victim and p["segments"][0]["text"] == victim
    assert "sig=" in p["audio"]  # the recording's own audio link is still signed
    assert new_client().get(victim).status_code == 401
