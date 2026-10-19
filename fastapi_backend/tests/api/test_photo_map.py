"""Assets on a map (docs/processing.md, Where an asset was taken): those whose file had a GPS fix are listed with their
`place`, inside a bounding box if asked, through the API and MCP; a video's location is read too; and the map's tiles
are a setting."""

from __future__ import annotations

import pytest

from app.domain import ingest, store
from tests.api.test_documents import _upload
from tests.api.test_file_dates import MODIFIED, _photo, _video
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user
from tests.video_helpers import needs_ffmpeg

R = store.R
EIFFEL = {"lat": 48.858233, "lon": 2.294567}
PARIS = "2.2,48.8,2.4,48.9"


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "root@x.io", "admin password 1", admin=True)
    return {"he": login(client, "ed@x.io", "editor password 1"), "ha": login(client, "root@x.io", "admin password 1")}


def _note(db, cfg, title, **fields):
    """An asset of 'pods' with these fields set (where it was taken)."""
    rid = ingest.import_text(db, cfg, "pods", f"{title}, a note.", title=title)
    if fields:
        db.q("UPDATE $r MERGE $d", r=R("recording", rid), d=fields)
    return rid


def _listed(client, h, **params):
    r = client.get("/api/v1/resources", params=params, headers=h)
    assert r.status_code == 200, r.text
    return {x["title"]: x["place"] for x in r.json()}


def test_located_assets_are_listed_inside_a_box(client, env, db, cfg):
    he = env["he"]
    _upload(client, he, _photo(), "eiffel.jpg", modified=MODIFIED)  # its EXIF GPS fix, read as it arrives
    _upload(client, he, _photo(gps=False), "nowhere.jpg", modified=MODIFIED)
    _note(db, cfg, "fiji", place={"lat": -17.7134, "lon": 178.065})
    _note(db, cfg, "samoa", place={"lat": -13.759, "lon": -172.1046})
    # a photo analysed with anytopdf before its file's place was kept: the GPS fix anytopdf read places it
    _note(db, cfg, "tokyo", enrichment={"by": "anytopdf", "place": {"name": "Tokyo", "lat": 35.6895, "lon": 139.6917, "from": "gps"}})
    _note(db, cfg, "plain")

    every = _listed(client, he)
    assert every["eiffel"] == EIFFEL and every["nowhere"] is None and every["plain"] is None
    assert every["tokyo"] == {"lat": 35.6895, "lon": 139.6917}
    assert set(_listed(client, he, located="true")) == {"eiffel", "fiji", "samoa", "tokyo"}
    assert _listed(client, he, bbox=PARIS) == {"eiffel": EIFFEL}
    # west past east crosses the antimeridian: Fiji and Samoa, not Paris or Tokyo
    assert set(_listed(client, he, bbox="170,-30,-170,0")) == {"fiji", "samoa"}
    # filters combine as ever: with words in the title, with a namespace
    assert set(_listed(client, he, located="true", q="tok")) == {"tokyo"}
    assert _listed(client, he, bbox=PARIS, ns="pods") == {"eiffel": EIFFEL}
    for bad in ("2,48,3", "a,b,c,d", "2,49,3,48", "200,0,10,10"):  # three numbers, words, south past north, off the Earth
        r = client.get("/api/v1/resources", params={"bbox": bad}, headers=he)
        assert r.status_code == 400 and "bbox" in r.text, (bad, r.text)
    # one asset says it too, for its members
    tokyo = db.values("SELECT VALUE record::id(id) FROM recording WHERE title = 'tokyo'")[0]
    assert client.get(f"/api/v1/resources/{tokyo}", headers=he).json()["place"] == {"lat": 35.6895, "lon": 139.6917}


def test_an_agent_finds_what_was_taken_in_a_place(client, env, db, cfg):
    _note(db, cfg, "louvre", place={"lat": 48.8606, "lon": 2.3376})
    _note(db, cfg, "rome", place={"lat": 41.9028, "lon": 12.4964})
    _note(db, cfg, "plain")
    _, tokens = _grant(client, env["he"], scope="read")
    got = tool(client, _bearer(tokens), "list_recordings", bbox=PARIS)
    assert got["total"] == 1 and got["recordings"][0]["title"] == "louvre"
    assert got["recordings"][0]["place"] == {"lat": 48.8606, "lon": 2.3376}
    got = tool(client, _bearer(tokens), "list_recordings", located=True)
    assert {r["title"] for r in got["recordings"]} == {"louvre", "rome"}
    assert "bbox" in tool_error(client, _bearer(tokens), "list_recordings", bbox="north of here")


@needs_ffmpeg
def test_a_video_is_placed_by_its_location(client, env, db, folder):
    # what an Android phone writes (ffmpeg's location tag), and what an iPhone does (QuickTime's ISO 6709 key)
    clip = _video(folder / "clip.mp4", "location=+48.8582+002.2945/")
    assert ingest.taken(clip)[1] == {"lat": 48.8582, "lon": 2.2945}
    mov = _video(folder / "phone.mov", "com.apple.quicktime.location.ISO6709=-33.8568+151.2153+004.000/")
    assert ingest.taken(mov)[1] == {"lat": -33.8568, "lon": 151.2153}
    # no fix (0, 0) and none at all are no place
    assert ingest.taken(_video(folder / "zero.mp4", "location=+00.0000+000.0000/"))[1] is None
    assert ingest.taken(_video(folder / "plain.mp4"))[1] is None
    rid = _upload(client, env["he"], clip.read_bytes(), "clip.mp4", modified=MODIFIED)["recording"]
    assert db.one("SELECT VALUE place FROM $r", r=R("recording", rid)) == {"lat": 48.8582, "lon": 2.2945}


def test_the_map_tiles_are_a_setting(client, env, new_client):
    he, ha = env["he"], env["ha"]
    tiles = client.get("/api/v1/resources/map-tiles", headers=he).json()
    assert tiles == {"url": "https://tile.openstreetmap.org/{z}/{x}/{y}.png", "attribution": "© OpenStreetMap contributors"}
    assert new_client().get("/api/v1/resources/map-tiles").status_code == 401  # signed in only
    # an admin points the map at their own tile server, or draws none
    own = "https://tiles.home.lan/{z}/{x}/{y}.png"
    assert client.put("/api/v1/settings/maps", json={"tile_url": own, "attribution": "Home"}, headers=ha).status_code == 200
    assert client.get("/api/v1/resources/map-tiles", headers=he).json() == {"url": own, "attribution": "Home"}
    assert client.put("/api/v1/settings/maps", json={"tile_url": ""}, headers=ha).status_code == 200
    assert client.get("/api/v1/resources/map-tiles", headers=he).json()["url"] is None
    for bad in ("https://tiles.home.lan/tile.png", "ftp://tiles.home.lan/{z}/{x}/{y}.png", "https://me:pw@t.lan/{z}/{x}/{y}.png"):
        r = client.put("/api/v1/settings/maps", json={"tile_url": bad}, headers=ha)
        assert r.status_code == 400 and "maps.tile_url" in r.text, bad
    assert client.put("/api/v1/settings/maps", json={"tile_url": own}, headers=he).status_code == 403  # admins only
