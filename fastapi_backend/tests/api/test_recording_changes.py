"""Changing recordings from the library: rename."""

from __future__ import annotations

import pathlib

from app.domain import metadata, render, store
from tests.helpers import drain, login, make_user, quiet, seed


def test_rename(client, db, cfg, folder):
    a, _b, call = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    he = login(client, "ed@x.io", "editor password 1")
    reports = pathlib.Path(cfg["data_dir"]) / "reports" / "pods"
    render.build_reports(db, cfg, ns="pods", log=quiet)
    assert (reports / f"ep1-{a}.html").exists()
    metadata.save(db, cfg, a, {"access": "public"})
    updates = len(db.values("SELECT VALUE id FROM iiif_activity WHERE recording = $r AND type = 'Update'", r=a))

    r = client.patch(f"/api/v1/recordings/{a}", headers=he, json={"title": "  Capsid   design\nepisode "})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["title"] == "Capsid design episode" and d["id"] == a
    # the report page moves with the title (so its link keeps working) and a report job rewrites it
    assert not (reports / f"ep1-{a}.html").exists()
    assert (reports / f"capsid-design-episode-{a}.html").exists()
    assert d["report_url"].startswith(f"/reports/pods/capsid-design-episode-{a}.html?")
    assert [j["steps"] for j in d["jobs"] if j["status"] == "queued"] == [[{"type": "report"}]]
    # IIIF harvesters hear about it straight away (and again when the report is rebuilt)
    assert len(db.values("SELECT VALUE id FROM iiif_activity WHERE recording = $r AND type = 'Update'", r=a)) == updates + 1
    drain(db, cfg)
    assert "Capsid design episode" in (reports / f"capsid-design-episode-{a}.html").read_text()
    rows = client.get("/api/v1/recordings", params={"q": "capsid"}, headers=he).json()
    assert [x["title"] for x in rows] == ["Capsid design episode"]
    entry = db.one("SELECT action, target, detail FROM audit_log WHERE action = 'recording.rename'")
    assert entry == {"action": "recording.rename", "target": f"recording:{a}", "detail": {"from": "ep1", "to": "Capsid design episode"}}

    # the same title again changes nothing
    assert client.patch(f"/api/v1/recordings/{a}", headers=he, json={"title": "Capsid design episode"}).status_code == 200
    assert len(db.values("SELECT VALUE id FROM audit_log WHERE action = 'recording.rename'")) == 1
    assert not db.values("SELECT VALUE id FROM job WHERE recording = $r AND status = 'queued'", r=a)

    # a recording that isn't analysed yet has no report to rebuild
    db.q("UPDATE $r SET analyzed_at = NONE", r=store.R("recording", a))
    assert client.patch(f"/api/v1/recordings/{a}", headers=he, json={"title": "Draft"}).json()["jobs"][0]["status"] == "succeeded"


def test_rename_rules(client, db, cfg, folder):
    a, _b, call = seed(db, cfg, folder)
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    hv, he = login(client, "vi@x.io", "viewer password 1"), login(client, "ed@x.io", "editor password 1")
    assert client.patch(f"/api/v1/recordings/{a}", headers=hv, json={"title": "x"}).status_code == 403
    assert client.patch(f"/api/v1/recordings/{call}", headers=he, json={"title": "x"}).status_code == 404
    assert client.patch("/api/v1/recordings/999", headers=he, json={"title": "x"}).status_code == 404
    assert client.patch(f"/api/v1/recordings/{a}", json={"title": "x"}).status_code == 401
    tok = client.post("/api/v1/tokens", json={"name": "ci", "scope": "read"}, headers=he).json()["token"]
    assert client.patch(f"/api/v1/recordings/{a}", headers={"Authorization": f"Bearer {tok}"}, json={"title": "x"}).status_code == 403
    for bad, code in (({"title": ""}, 422), ({"title": "x" * 201}, 422), ({"name": "x"}, 422), ({"title": " \n "}, 400), ({}, 400)):
        assert client.patch(f"/api/v1/recordings/{a}", headers=he, json=bad).status_code == code, bad
    assert db.one("SELECT title FROM $r", r=store.R("recording", a))["title"] == "ep1"
