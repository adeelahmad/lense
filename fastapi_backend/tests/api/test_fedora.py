"""Fedora as the archive's repository store: what is sent, kept in step, deleted, and how it is set up."""

from __future__ import annotations

import pytest
from rdflib import Graph, Literal, URIRef
from rdflib.namespace import DCTERMS, OWL

from app.domain import deletion, fedora, metadata
from tests import fake_fedora
from tests.api.test_iiif import Env

F = fake_fedora.Handler


@pytest.fixture
def env(app, db, cfg, folder):
    return Env(app, db, cfg, folder)


def turtle(path):
    g = Graph()
    g.parse(data=F.store[path]["body"].decode(), format="turtle")
    return g


def test_off_by_default(env):
    assert not fedora.enabled(env.cfg)
    assert fedora.sync_due(env.db, env.cfg) is None
    c, h = env.admin()
    st = c.get("/api/v1/admin/fedora", headers=h).json()
    assert st["enabled"] is False and st["resources"] == 0
    assert c.post("/api/v1/admin/fedora/sync", headers=h).status_code == 409


def test_sync_sends_changes_and_deletes(env):
    srv = fake_fedora.start(env.cfg)
    try:
        cfg, db = env.cfg, env.db
        out = fedora.sync_due(db, cfg)  # never synced: everything goes
        assert out["failed"] == 0 and out["sent"] > 5 and out["files"] >= 1, out
        rec = f"/rest/lens/pods/recordings/{env.clip}"
        assert {"/rest/lens", "/rest/lens/pods", "/rest/lens/pods/recordings", "/rest/lens/pods/entities"} <= set(F.store)
        g = turtle(rec)
        me = URIRef(cfg["fedora"]["url"] + rec[len("/rest") :])
        assert (me, OWL.sameAs, URIRef(f"http://localhost:3000/id/recording/{env.clip}")) in g
        assert (me, DCTERMS.license, URIRef("http://creativecommons.org/licenses/by/4.0/")) in g
        assert F.store[rec + "/file"]["type"] == "audio/wav" and len(F.store[rec + "/file"]["body"]) > 1000
        assert any(p.startswith("/rest/lens/pods/entities/") for p in F.store)
        assert any(p.startswith("/rest/lens/calls/speakers/") for p in F.store)

        # nothing changed: nothing is sent again
        F.seen.clear()
        fedora.request_full(db)
        again = fedora.sync_due(db, cfg)
        assert again["sent"] == 0 and again["files"] == 0 and again["unchanged"] > 5
        assert not [s for s in F.seen if s[0] == "PUT"]

        # a metadata change is queued and only that recording goes
        metadata.save(db, cfg, env.clip, {"terms": {"spatial": ["Berlin"]}})
        assert fedora.summary(db, cfg)["pending"] == 1
        F.seen.clear()
        one = fedora.sync_due(db, cfg)
        assert one["sent"] == 1 and [s for s in F.seen if s[0] == "PUT"] == [("PUT", rec)]
        assert (me, DCTERMS.spatial, Literal("Berlin")) in turtle(rec)
        assert fedora.summary(db, cfg)["pending"] == 0

        # a deleted recording leaves Fedora at the next full sync, tombstone and all
        deletion.delete(db, cfg, env.locked)
        fedora.request_full(db)
        gone = fedora.sync_due(db, cfg)
        locked = f"/rest/lens/pods/recordings/{env.locked}"
        assert gone["deleted"] >= 1 and locked not in F.store and locked not in F.tombstones

        # a refusal is reported, the rest still goes
        F.refuse = rec
        metadata.save(db, cfg, env.clip, {"terms": {"spatial": ["Paris"]}})
        bad = fedora.sync_due(db, cfg)
        assert bad["failed"] == 1 and "409" in bad["errors"][0]
        assert "409" in fedora.summary(db, cfg)["last_error"]
    finally:
        srv.shutdown()


def test_admin_api_and_settings(env):
    srv = fake_fedora.start(env.cfg)
    try:
        c, h = env.admin()
        r = c.post("/api/v1/admin/fedora/sync", headers=h)
        assert r.status_code == 200 and r.json()["sent"] > 0
        st = c.get("/api/v1/admin/fedora", headers=h).json()
        assert st["enabled"] and st["resources"] > 0 and st["last_full"]
        assert c.get("/api/v1/admin/fedora", headers=env_viewer(c)).status_code == 403
    finally:
        srv.shutdown()


def env_viewer(c):
    from tests.helpers import login

    return login(c, "vi@x.io", "viewer password 1")


def test_settings_validation():
    from app.domain import settings

    assert settings._fedora_setting("url", "http://fedora:8080/fcrepo/rest/") == "http://fedora:8080/fcrepo/rest"
    for bad in ("ftp://x", "http://u:p@x/rest", "not a url"):
        with pytest.raises(ValueError):
            settings._fedora_setting("url", bad)
    with pytest.raises(ValueError):
        settings._fedora_setting("root", "../etc")
    with pytest.raises(ValueError):
        settings._fedora_setting("sync_seconds", 1)
    assert settings._fedora_setting("enabled", True) is True


@pytest.mark.skipif(
    not __import__("os").environ.get("LENS_TEST_FEDORA_URL"), reason="LENS_TEST_FEDORA_URL: a real Fedora 6 to test against"
)
def test_a_real_fedora(env):
    """Against a real Fedora (docker run -p 8080:8080 fcrepo/fcrepo:6.5.1-tomcat9, then
    LENS_TEST_FEDORA_URL=http://127.0.0.1:8080/fcrepo/rest): everything is accepted, kept in step, and deleted."""
    import os
    import urllib.request
    import uuid

    cfg, db = env.cfg, env.db
    cfg["fedora"] = {
        **cfg["fedora"],
        "url": os.environ["LENS_TEST_FEDORA_URL"],
        "user": os.environ.get("LENS_TEST_FEDORA_USER", "fedoraAdmin"),
        "password": os.environ.get("LENS_TEST_FEDORA_PASSWORD", "fedoraAdmin"),
        "root": "lens-test-" + uuid.uuid4().hex[:8],
    }
    c = fedora.Client(cfg)
    try:
        out = fedora.sync(db, cfg)
        assert out["failed"] == 0 and out["sent"] > 5 and out["files"] >= 1, out
        req = urllib.request.Request(c.url(f"pods/recordings/{env.clip}"), headers={"Accept": "text/turtle", "Authorization": c.auth})
        g = Graph()
        g.parse(data=urllib.request.urlopen(req).read().decode(), format="turtle")
        me = URIRef(c.url(f"pods/recordings/{env.clip}"))
        assert (me, DCTERMS.license, URIRef("http://creativecommons.org/licenses/by/4.0/")) in g
        assert (me, OWL.sameAs, URIRef(f"http://localhost:3000/id/recording/{env.clip}")) in g
        assert c.exists(c.url(f"pods/recordings/{env.clip}/file"))

        metadata.save(db, cfg, env.clip, {"terms": {"spatial": ["Berlin"]}, "identifiers": [{"type": "DOI", "value": "10.1/x"}]})
        assert fedora.sync_due(db, cfg)["sent"] == 1  # replaced in place
        g = Graph()
        g.parse(data=urllib.request.urlopen(req).read().decode(), format="turtle")
        assert (me, DCTERMS.spatial, Literal("Berlin")) in g

        deletion.delete(db, cfg, env.locked)
        out = fedora.sync(db, cfg)
        assert out["deleted"] >= 1 and not c.exists(c.url(f"pods/recordings/{env.locked}"))
        assert out["failed"] == 0, out
    finally:
        c.delete(c.url())


def test_set_up_in_settings(env):
    srv = fake_fedora.start(env.cfg)
    url = env.cfg["fedora"]["url"]
    try:
        c, h = env.admin()
        env.app.state.archive.base["fedora"] = {**env.app.state.archive.base["fedora"], "url": None}
        body = {"url": url + "/", "user": "fedoraAdmin", "password": "secret", "root": "archive"}
        assert c.put("/api/v1/settings/fedora", headers=h, json=body).status_code == 200
        view = c.get("/api/v1/settings", headers=h).json()["fedora"]
        assert (
            view["values"]["url"] == url
            and view["values"]["password"]["set"] is True
            and "secret" not in str(view["values"]["password"].get("value"))
        )  # the secret isn't shown
        assert c.put("/api/v1/settings/fedora", headers=h, json={"url": "ftp://x"}).status_code == 400
        out = c.post("/api/v1/admin/fedora/sync", headers=h).json()
        assert out["sent"] > 0 and "/rest/archive/pods" in F.store
    finally:
        srv.shutdown()
