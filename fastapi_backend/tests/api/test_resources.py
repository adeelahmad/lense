"""/api/v1/resources is the API's name for recordings; /api/v1/recordings keeps working for existing clients."""

from __future__ import annotations

import pytest

from app.core.middleware import publish_resources
from tests.helpers import login, make_user, seed


@pytest.fixture
def env(client, db, cfg, folder):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    return {"a": a, "b": b, "call": call, "h": login(client, "ed@x.io", "editor password 1")}


def test_resources_and_recordings_are_the_same(client, env):
    h, a = env["h"], env["a"]
    listed = client.get("/api/v1/resources", headers=h)
    assert listed.status_code == 200 and listed.headers["X-Total-Count"] == "2"
    assert listed.json() == client.get("/api/v1/recordings", headers=h).json()
    assert client.get(f"/api/v1/resources/{a}", headers=h).json()["id"] == a
    assert client.patch(f"/api/v1/resources/{a}", headers=h, json={"title": "Renamed as a resource"}).status_code == 200
    assert client.get(f"/api/v1/recordings/{a}", headers=h).json()["title"] == "Renamed as a resource"
    # what hangs off one: its notes, player, edits
    note = client.post(f"/api/v1/resources/{a}/notes", headers=h, json={"text": "Through /resources."})
    assert note.status_code == 200, note.text
    assert [n["text"] for n in client.get(f"/api/v1/recordings/{a}/notes", headers=h).json()] == ["Through /resources."]
    assert client.get(f"/api/v1/resources/{a}/player", headers=h).status_code == 200
    # the same rules: another namespace's is not found either way
    assert client.get(f"/api/v1/resources/{env['call']}", headers=h).status_code == 404
    # only the prefix itself is renamed
    assert client.get("/api/v1/resourcesx", headers=h).status_code == 404


def test_the_schema_publishes_resources(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert not [p for p in paths if p.startswith("/api/v1/recordings")]
    assert paths["/api/v1/resources/{rid}"]["get"]["tags"] == ["resources"]
    assert paths["/api/v1/resources/{rid}/notes"]["get"]["tags"] == ["notes"]  # other areas keep their tag
    schema = {"paths": {"/api/v1/recordings": {"get": {"tags": ["recordings"], "operationId": "recordings-list"}}, "/x": {}}}
    out = publish_resources(schema)
    assert out["paths"] == {"/api/v1/resources": {"get": {"tags": ["resources"], "operationId": "resources-list"}}, "/x": {}}
