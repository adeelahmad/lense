"""An asset's downloads switch (docs/access.md#downloads): with downloads off, only its owners save its files (its own
file, the PDF made of it, its supplementary files); everyone else still plays it, sees its pages and reads its text."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.domain import store
from tests.api.test_files import add
from tests.api.test_iiif import BASE
from tests.api.test_permissions import env  # noqa: F401  (the fixture)


def _off(client, h, rid, on=False):
    return client.put(f"/api/v1/recordings/{rid}/access", headers=h, json={"downloads": on})


def test_downloads_off_refuses_a_viewers_download_but_still_plays(client, new_client, env, db):  # noqa: F811
    clip, ho, he, hg = env["clip"], env["ho"], env["he"], env["hg"]
    media, audio = f"/api/v1/recordings/{clip}/media", f"/api/v1/recordings/{clip}/audio"
    wav = env["wav"].read_bytes()
    client.post(f"/api/v1/recordings/{clip}/permissions", headers=ho, json={"email": "guest@x.io"})
    att = add(client, he, clip, "release.txt", "signed release", "attachment").json()["id"]
    # on by default: everyone who sees it saves it
    assert client.get(f"/api/v1/recordings/{clip}/access", headers=he).json()["downloads"] is True
    assert client.get(media, headers=he).content == wav
    anon = new_client()
    before = client.get(f"/api/v1/public/recordings/{clip}", headers=hg).json()  # given permission: the visitors' page
    assert before["downloads"] is True and anon.get(before["files"][0]["url"]).content == b"signed release"

    # only owners turn it off
    assert _off(client, he, clip).status_code == 403
    r = _off(client, ho, clip)
    assert r.status_code == 200 and r.json()["downloads"] is False
    assert db.one("SELECT detail FROM audit_log WHERE action = 'recording.access'")["detail"] == {"downloads": False}

    # an editor: no saving, a clear reason, but it still plays
    r = client.get(media, headers=he)
    assert r.status_code == 403 and "Downloads are turned off" in r.json()["detail"]
    assert client.get(f"/api/v1/recordings/{clip}/files/{att}/download", headers=he).status_code == 403
    played = client.get(audio, headers={**he, "Range": "bytes=0-9"})
    assert (played.status_code, played.content) == (206, wav[:10])
    rec = client.get(f"/api/v1/recordings/{clip}", headers=he).json()
    assert (rec["downloads"], rec["can_save"]) == (False, False)
    listed = client.get(f"/api/v1/recordings/{clip}/files", headers=he).json()
    assert (listed["downloads"], listed["can_save"]) == (False, False)
    assert anon.get(listed["primary"]["download"]).status_code == 403  # a link signed for them doesn't save either
    assert anon.get(listed["files"][0]["download"]).status_code == 403

    # someone given permission, on the pages visitors see: it plays, its files aren't offered, old links stop saving
    page = client.get(f"/api/v1/public/recordings/{clip}", headers=hg).json()
    assert (page["view"], page["downloads"], page["files"], page["files_closed"]) == ("full", False, [], 0)
    assert anon.get(page["media"]["url"], headers={"Range": "bytes=0-9"}).status_code == 206
    assert anon.get(before["files"][0]["url"]).status_code == 403

    # a share link plays it too, and doesn't save it
    link = client.post(f"/api/v1/recordings/{clip}/share", headers=ho).json()["token"]
    assert anon.get(media, params={"s": link}).status_code == 403
    assert anon.get(audio, params={"s": link}, headers={"Range": "bytes=0-9"}).status_code == 206

    # owners still save, with their token or the links signed for them
    assert client.get(media, headers=ho).content == wav
    mine = client.get(f"/api/v1/recordings/{clip}/files", headers=ho).json()
    assert mine["can_save"] is True
    assert anon.get(mine["primary"]["download"]).content == wav
    assert anon.get(mine["files"][0]["download"]).content == b"signed release"
    assert client.get(f"/api/v1/recordings/{clip}", headers=ho).json()["can_save"] is True

    # the file tree leaves its own file out below owners
    ns = "pods/Collections/General"
    names = lambda h: [e["name"] for e in client.get("/api/v1/tree", headers=h, params={"path": ns}).json()["entries"]]  # noqa: E731
    own = [n for n in names(ho) if n.startswith("clip") and n.endswith(".wav")]
    assert own and not [n for n in names(he) if n in own]

    # turned on again, everyone who sees it saves it
    assert _off(client, ho, clip, on=True).json()["downloads"] is True
    assert client.get(media, headers=he).status_code == 200
    assert db.one("SELECT downloads FROM $r", r=store.R("recording", clip)).get("downloads") is None  # on is the default: nothing kept


def test_downloads_off_keeps_iiif_playing(client, env):  # noqa: F811
    clip, ho = env["clip"], env["ho"]
    client.post(f"/api/v1/recordings/{clip}/permissions", headers=ho, json={"email": "guest@x.io"})
    att = add(client, env["he"], clip, "release.txt", "signed release", "attachment").json()["id"]
    c = TestClient(env["app"], base_url=BASE)
    before = c.get(f"/iiif/{clip}/manifest", headers=env["hg"]).json()
    assert any(r["id"].endswith(f"/files/{att}") for r in before.get("rendering") or [])
    _off(client, ho, clip)
    # the manifest no longer offers its files to save; its media plays and its captions load
    after = c.get(f"/iiif/{clip}/manifest", headers=env["hg"]).json()
    assert not any("/files/" in r["id"] for r in after.get("rendering") or [])
    assert c.get(f"/iiif/{clip}/files/{att}", headers=env["hg"]).status_code == 403
    assert c.get(f"/iiif/{clip}/audio", headers={**env["hg"], "Range": "bytes=0-9"}).status_code == 206
    assert c.get(f"/iiif/{clip}/transcript.vtt", headers=env["hg"]).status_code == 200
    assert c.get(f"/iiif/{clip}/files/{att}", headers=ho).status_code == 200  # owners still do
