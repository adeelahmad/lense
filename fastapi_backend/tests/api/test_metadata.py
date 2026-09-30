"""Descriptive metadata: per recording, per namespace (profile), bulk edits, history and revert, with roles."""

from __future__ import annotations

import pytest

from tests.api.test_iiif import Env
from tests.helpers import login


@pytest.fixture
def env(app, db, cfg, folder):
    return Env(app, db, cfg, folder)


def test_metadata_api(env):
    c, h = env.admin()
    url = f"/api/v1/recordings/{env.pub}/metadata"
    assert c.put(url, headers=h, json={"set": {"rights": "https://example.com/mine"}}).status_code == 400
    subjects = [{"label": "Gene therapy", "uri": "http://www.wikidata.org/entity/Q213901"}]
    m = c.put(url, headers=h, json={"set": {"summary": {"en": ["Capsid episode"], "fr": ["Épisode capside"]}, "subjects": subjects}}).json()
    assert sorted(m["meta"]["summary"]) == ["en", "fr"]
    assert c.put("/api/v1/namespaces/pods/metadata", headers=h, json={"profile": {"required": ["rights"]}}).status_code == 200
    assert c.get("/api/v1/namespaces/pods/metadata", headers=h).json()["profile"]["required"] == ["rights"]
    assert c.get(url, headers=h).json()["problems"] == [{"field": "rights", "message": "required by this namespace"}]
    hist = c.get(f"{url}/history", headers=h).json()
    assert hist[0]["changed"] == ["subjects", "summary"]
    assert c.post(f"/api/v1/metadata/edits/{hist[0]['id']}/revert", headers=h).status_code == 200
    assert "fr" not in c.get(url, headers=h).json()["meta"].get("summary", {})
    inc = {"rights": "http://rightsstatements.org/vocab/InC/1.0/"}
    dry = c.post("/api/v1/metadata/bulk", headers=h, json={"namespace": "pods", "set": inc}).json()
    assert dry["would_change"] == 3
    c.post("/api/v1/metadata/bulk", headers=h, json={"namespace": "pods", "set": inc, "dry_run": False})
    assert c.get(url, headers=h).json()["problems"] == []
    assert "metadata.bulk" in [a["action"] for a in c.get("/api/v1/audit", headers=h).json()]

    vh = login(c, "vi@x.io", "viewer password 1")
    assert c.put(url, headers=vh, json={"set": {"summary": "x"}}).status_code == 403
    assert c.get(url, headers=vh).status_code == 200
    assert c.put("/api/v1/namespaces/pods/metadata", headers=vh, json={"meta": {}}).status_code == 403  # owners only
    assert c.get(f"/api/v1/recordings/{env.call}/metadata", headers=vh).status_code == 404  # calls looks absent
    assert c.get("/api/v1/namespaces/calls/metadata", headers=vh).status_code == 404
    assert c.put(url, json={"set": {"summary": "x"}}).status_code == 401
    assert c.put(url, headers=h, json={"sett": {}}).status_code == 422  # unknown fields fail loudly
