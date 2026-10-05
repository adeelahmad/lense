"""IIIF Presentation 3.0, Content Search 2.0, Content State, Change Discovery, Authorization Flow 2.0 and import."""

from __future__ import annotations

import http.server
import json
import re
import shutil
import threading
import urllib.parse
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from app.domain import analyze, auth, iiif, iiif_auth, ingest, metadata
from tests.helpers import drain, login, make_user, manifests, quiet, seed, write_wav

BASE = "https://127.0.0.1"
VIEWER = "https://viewer.example"
CLIP = "[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.\n[00:02] Alice: Bye."


def md_first(lmap):
    return next(iter(lmap.values()))[0]


class Env:
    def __init__(self, app, db, cfg, folder):
        self.app, self.db, self.cfg, self.folder = app, db, cfg, folder
        self.pub, self.locked, self.call = seed(db, cfg, folder)
        self.wav, tr = folder / "clip.wav", folder / "clip.txt"
        write_wav(self.wav)
        tr.write_text(CLIP)
        self.clip = ingest.import_transcript(db, cfg, "pods", tr, audio=self.wav, log=quiet)
        analyze.analyze_pending(db, cfg, log=quiet)
        rights = "https://creativecommons.org/licenses/by/4.0/"
        metadata.save(db, cfg, self.clip, {"access": "public", "rights": rights, "attribution": "Courtesy of the lab"})
        metadata.save(db, cfg, self.pub, {"access": "public", "open": ["transcript", "index"]})  # media closed
        metadata.save(db, cfg, self.call, {"access": "restricted"})  # self.locked stays private
        make_user(db, "root@x.io", "root password 1", admin=True)
        self.vi = make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})

    def client(self) -> TestClient:
        return TestClient(self.app, base_url=BASE)

    def admin(self) -> tuple[TestClient, dict[str, str]]:
        c = self.client()
        return c, login(c, "root@x.io", "root password 1")


@pytest.fixture
def env(app, db, cfg, folder):
    return Env(app, db, cfg, folder)


def csrf_of(page) -> str:
    m = re.search(r'name="csrf" value="([^"]+)"', page.text)
    assert m, page.text
    return m.group(1)


def sign_in(c: TestClient, email: str, password: str, origin: str = ""):
    """Open the access service's sign-in page, as a viewer would, and submit it."""
    page = c.get("/iiif/auth/access", params={"origin": origin} if origin else None)
    return c.post("/iiif/auth/access", data={"email": email, "password": password, "origin": origin, "csrf": csrf_of(page)})


def test_duration_without_ffprobe(folder):
    """WAV durations come from the file header when ffprobe is missing, not from the transcript's (estimated) end."""
    wav = folder / "x.wav"
    write_wav(wav, seconds=3.0)
    with mock.patch("subprocess.run", side_effect=FileNotFoundError("ffprobe")):
        assert ingest.probe(wav) == (3000, 1)
    assert ingest.probe(wav) == (3000, 1)


def test_manifests_collections_search_state_discovery(env):
    anon = env.client()
    r = anon.get(f"/iiif/{env.clip}/manifest")
    assert r.status_code == 200
    assert "presentation/3/context.json" in r.headers["content-type"]
    assert r.headers["access-control-allow-origin"] == "*"
    man = r.json()
    assert iiif.validate(man) == []  # the official IIIF JSON Schema
    body = man["items"][0]["items"][0]["items"][0]["body"]
    assert (body["type"], body["duration"], body["id"]) == ("Sound", 3.0, f"{BASE}/iiif/{env.clip}/audio")
    assert man["rights"] == "http://creativecommons.org/licenses/by/4.0/"
    assert man["items"][0]["annotations"][0]["items"][0]["body"]["format"] == "text/vtt"
    assert man["service"][0]["type"] == "SearchService2"
    assert anon.get(f"/iiif/{env.locked}/manifest").status_code == 404  # private
    assert anon.get(f"/iiif/{env.call}/manifest").status_code == 404  # restricted: not published
    # public with every part closed: the manifest, its content behind the IIIF sign-in, no chapters
    metadata.save(env.db, env.cfg, env.call, {"access": "public", "open": []})
    locked = anon.get(f"/iiif/{env.call}/manifest").json()
    assert iiif.validate(locked) == []
    assert locked["@context"][0] == iiif.AUTH2
    assert locked["items"][0]["annotations"][0]["items"][0]["body"]["service"][0]["type"] == "AuthProbeService2"
    assert "service" not in locked  # no open search on a closed transcript
    assert "structures" not in locked and "structures" in anon.get(f"/iiif/{env.pub}/manifest").json()
    coll = anon.get("/iiif/collection/pods").json()
    assert iiif.validate(coll) == []
    # the namespace lists its collections (General holds them all); each lists its manifests
    assert [(x["type"], x["label"]) for x in coll["items"]] == [("Collection", {"none": ["General"]})]
    general = anon.get(urllib.parse.urlsplit(coll["items"][0]["id"]).path).json()
    assert iiif.validate(general) == [] and general["partOf"][0]["id"] == f"{BASE}/iiif/collection/pods"
    assert set(manifests(anon.get, "/iiif/collection/pods")) == {f"{BASE}/iiif/{env.pub}/manifest", f"{BASE}/iiif/{env.clip}/manifest"}
    assert anon.get(f"/iiif/{env.pub}/manifest").json()["partOf"][0]["id"] == general["id"]
    assert f"{BASE}/iiif/collection/pods" in [x["id"] for x in anon.get("/iiif/collection").json()["items"]]
    assert len(anon.get(f"/iiif/{env.pub}/annotations/transcript").json()["items"]) == 6
    assert anon.get(f"/iiif/{env.call}/annotations/transcript").status_code == 404
    hits = anon.get(f"/iiif/{env.pub}/search", params={"q": "capsid"}).json()
    assert hits["items"]
    assert hits["annotations"][0]["items"][0]["target"]["selector"][0]["exact"].lower() == "capsid"
    assert "capsid" in [t["value"] for t in anon.get(f"/iiif/{env.pub}/autocomplete", params={"q": "cap"}).json()["items"]]
    assert anon.get("/iiif/collection/pods/search", params={"q": "capsid"}).json()["items"]
    assert anon.get(f"/iiif/{env.clip}/record.json").json()["@type"] == "AudioObject"
    assert "<dc:rights>http://creativecommons.org/licenses/by/4.0/</dc:rights>" in anon.get(f"/iiif/{env.clip}/dc.xml").text
    assert anon.get(f"/iiif/{env.pub}/transcript.vtt").text.startswith("WEBVTT")
    assert anon.get(f"/iiif/{env.call}/transcript.vtt").status_code == 401
    assert anon.get(f"/iiif/{env.pub}/audio").status_code == 401  # media closed: audio needs sign-in
    r = anon.get(f"/iiif/{env.clip}/audio", headers={"Range": "bytes=0-9"})
    assert (r.status_code, r.content) == (206, env.wav.read_bytes()[:10])

    c, h = env.admin()
    st = c.get(f"/api/v1/recordings/{env.clip}/content-state", params={"t0": 1.5, "t1": 3}, headers=h).json()
    assert iiif.decode_state(st["encoded"]) == st["content_state"]
    assert st["content_state"]["target"]["id"] == f"{BASE}/iiif/{env.clip}/canvas/1#t=1.5,3"
    panel = c.get(f"/api/v1/recordings/{env.clip}/iiif", headers=h).json()
    assert (panel["validation"], panel["access"]) == ({"checked": True, "problems": []}, "public")
    assert panel["json"]["type"] == "Manifest"
    assert c.get(f"/api/v1/recordings/{env.clip}/iiif").status_code == 401  # the panel needs a sign-in

    stream = anon.get("/iiif/discovery/activity").json()
    page = anon.get(stream["first"]["id"].replace(BASE, "")).json()
    assert [i["type"] for i in page["orderedItems"]] == ["Create", "Create", "Create"]
    assert c.put(f"/api/v1/recordings/{env.clip}/metadata", headers=h, json={"set": {"access": "private"}}).status_code == 200
    assert anon.get(stream["first"]["id"].replace(BASE, "")).json()["orderedItems"][-1]["type"] == "Delete"
    assert anon.get(f"/iiif/{env.clip}/manifest").status_code == 404
    # a role in the namespace still sees the private manifest
    vh = login(c, "vi@x.io", "viewer password 1")
    assert c.get(f"/iiif/{env.clip}/manifest", headers=vh).status_code == 200


def test_authorization_flow(env):
    metadata.save(env.db, env.cfg, env.clip, {"access": "public", "open": ["transcript", "index"]})
    anon = env.client()
    probe = f"/iiif/auth/probe/{env.clip}/audio"
    r = anon.get(probe)
    assert (r.status_code, r.json()["status"], r.json()["type"]) == (200, 401, "AuthProbeResult2")
    assert anon.options(probe).headers["access-control-allow-headers"] == "Authorization"

    page = anon.get("/iiif/auth/access", params={"origin": VIEWER})
    assert 'name="password"' in page.text
    assert "frame-ancestors 'none'" in page.headers["content-security-policy"]
    assert "access-control-allow-origin" not in page.headers
    guard = [x for x in page.headers.get_list("set-cookie") if x.startswith("la_iiif_csrf=")][0].lower()
    assert "samesite=strict" in guard and "path=/iiif/auth" in guard and "httponly" in guard
    # the form needs its CSRF token (a double-submit cookie plus the hidden field)
    forged = anon.post("/iiif/auth/access", data={"email": "root@x.io", "password": "root password 1", "origin": VIEWER})
    assert "form expired" in forged.text and "window.close()" not in forged.text
    assert not [x for x in forged.headers.get_list("set-cookie") if x.startswith(f"{iiif_auth.COOKIE}=")]
    stranger = env.client()
    stolen = stranger.post("/iiif/auth/access", data={"email": "root@x.io", "password": "root password 1", "csrf": csrf_of(page)})
    assert "form expired" in stolen.text  # the field alone, without the matching cookie, isn't enough

    assert "Wrong email or password" in sign_in(anon, "root@x.io", "nope nope nope").text
    done = sign_in(anon, "root@x.io", "root password 1", VIEWER)
    assert "window.close()" in done.text
    cookie = [x for x in done.headers.get_list("set-cookie") if x.startswith(f"{iiif_auth.COOKIE}=")][0].lower()
    assert "secure" in cookie and "samesite=none" in cookie and "httponly" in cookie and "path=/iiif/" in cookie
    assert 'name="continue"' in anon.get("/iiif/auth/access", params={"origin": VIEWER}).text  # already signed in: confirm

    tok = anon.get("/iiif/auth/token", params={"messageId": "m1", "origin": VIEWER})
    assert "frame-ancestors *" in tok.headers["content-security-policy"]
    msg = json.loads(re.search(r"postMessage\((\{.*?\}), \"https://viewer.example\"\)", tok.text).group(1))
    assert (msg["type"], msg["messageId"]) == ("AuthAccessToken2", "m1")

    fresh = env.client()  # the viewer: no cookies, just the token
    res = fresh.get(probe, headers={"Authorization": f"Bearer {msg['accessToken']}"}).json()
    assert res["status"] == 302
    media = fresh.get(res["location"]["id"].replace(BASE, ""), headers={"Range": "bytes=0-9"})
    assert (media.status_code, media.content) == (206, env.wav.read_bytes()[:10])
    assert fresh.get(res["location"]["id"].replace(BASE, "")[:-4] + "0000").status_code == 401  # tampered
    assert fresh.get(f"/iiif/{env.clip}/audio").status_code == 401
    assert "postMessage" not in env.client().get("/iiif/auth/token", params={"messageId": "m2", "origin": "javascript:alert(1)"}).text

    vi = env.client()
    sign_in(vi, "vi@x.io", "viewer password 1", VIEWER)
    vtok = vi.get("/iiif/auth/token", params={"messageId": "v", "origin": VIEWER}).text
    vmsg = json.loads(re.search(r"postMessage\((\{.*?\}), ", vtok).group(1))
    vres = fresh.get(f"/iiif/auth/probe/{env.call}/transcript", headers={"Authorization": f"Bearer {vmsg['accessToken']}"}).json()
    assert vres["status"] == 403

    c, h = env.admin()
    assert c.put("/api/v1/settings/iiif", headers=h, json={"allowed_origins": ["https://allowed.example"]}).status_code == 200
    err = anon.get("/iiif/auth/token", params={"messageId": "m3", "origin": VIEWER}).text
    assert '"profile": "invalidOrigin"' in err
    anon.get("/iiif/auth/logout")
    assert fresh.get(probe, headers={"Authorization": f"Bearer {msg['accessToken']}"}).json()["status"] == 401


def test_import_from_iiif(env):
    site = env.folder / "site"
    site.mkdir()
    shutil.copy(env.wav, site / "clip.wav")
    (site / "captions.vtt").write_text(
        "WEBVTT\n\n00:00:00.000 --> 00:00:01.500\n<v Ann>Hello from another archive.\n\n"
        "00:00:01.500 --> 00:00:03.000\n<v Ben>It came in over IIIF.\n"
    )
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), lambda *a: http.server.SimpleHTTPRequestHandler(*a, directory=str(site)))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    painting = {
        "id": f"{base}/c1/p/a",
        "type": "Annotation",
        "motivation": "painting",
        "body": {"id": f"{base}/clip.wav", "type": "Sound", "format": "audio/wav", "duration": 3.0},
        "target": f"{base}/c1",
    }
    captions = {
        "id": f"{base}/c1/s/a",
        "type": "Annotation",
        "motivation": "supplementing",
        "body": {"id": f"{base}/captions.vtt", "type": "Text", "format": "text/vtt", "language": "en"},
        "target": f"{base}/c1",
    }
    manifest = {
        "@context": iiif.P3,
        "id": f"{base}/manifest.json",
        "type": "Manifest",
        "label": {"en": ["Oral history 7"]},
        "summary": {"en": ["An interview."]},
        "rights": "http://creativecommons.org/licenses/by-nc/4.0/",
        "navDate": "2021-05-01T00:00:00Z",
        "requiredStatement": {"label": {"en": ["Attribution"]}, "value": {"en": ["Other Archive"]}},
        "metadata": [{"label": {"en": ["Interviewer"]}, "value": {"none": ["Ann"]}}],
        "items": [
            {
                "id": f"{base}/c1",
                "type": "Canvas",
                "duration": 3.0,
                "items": [{"id": f"{base}/c1/p", "type": "AnnotationPage", "items": [painting]}],
                "annotations": [{"id": f"{base}/c1/s", "type": "AnnotationPage", "items": [captions]}],
            }
        ],
    }
    (site / "manifest.json").write_text(json.dumps(manifest))
    v2 = {"@context": "http://iiif.io/api/presentation/2/context.json", "@id": f"{base}/v2.json", "@type": "sc:Manifest"}
    (site / "v2.json").write_text(json.dumps(v2))
    try:
        c, h = env.admin()
        pv = c.post("/api/v1/import/iiif/preview", headers=h, json={"url": f"{base}/manifest.json"}).json()
        assert (pv["label"], pv["items"][0]["audio"]["type"], len(pv["items"][0]["captions"])) == ("Oral history 7", "Sound", 1)
        assert "version 2" in c.post("/api/v1/import/iiif/preview", headers=h, json={"url": f"{base}/v2.json"}).json()["detail"]
        vh = login(c, "vi@x.io", "viewer password 1")
        assert c.post("/api/v1/import/iiif/preview", headers=vh, json={"url": f"{base}/manifest.json"}).status_code == 403  # admins only
        bad = c.post("/api/v1/import/iiif", headers=h, json={"url": f"{base}/manifest.json", "namespace": "Bad Name", "wait": True})
        assert bad.status_code == 400
        r = c.post("/api/v1/import/iiif", headers=h, json={"url": f"{base}/manifest.json", "namespace": "oral", "wait": True})
        assert r.status_code == 200, r.text
        rid = r.json()["recordings"][0]
        job = env.db.one("SELECT steps FROM job WHERE recording = $r", r=rid)
        assert "transcribe" not in [s["type"] for s in job["steps"]]  # kept the published transcript
        drain(env.db, env.cfg)
        p = c.get(f"/api/v1/recordings/{rid}/player", headers=h).json()
        assert [s["text"] for s in p["segments"]] == ["Hello from another archive.", "It came in over IIIF."]
        assert {s["name"] for s in p["speakers"]} == {"Ann", "Ben"}
        meta = c.get(f"/api/v1/recordings/{rid}/metadata", headers=h).json()["meta"]
        got = (meta["rights"], meta["navDate"], md_first(meta["attribution"]))
        assert got == ("http://creativecommons.org/licenses/by-nc/4.0/", "2021-05-01T00:00:00Z", "Other Archive")
        assert meta["related"][0]["id"] == f"{base}/manifest.json"
        again = c.post("/api/v1/import/iiif", headers=h, json={"url": f"{base}/manifest.json", "namespace": "oral", "wait": True})
        assert again.json()["recordings"] == []  # no duplicates
        # a Collection's Manifests are found in the Collections inside it too (Lens nests its own), referenced or embedded
        talks = {"@context": iiif.P3, "id": f"{base}/talks.json", "type": "Collection", "label": {"en": ["Talks"]}}
        talks["items"] = [{"id": f"{base}/manifest.json", "type": "Manifest", "label": {"en": ["Oral history 7"]}}]
        top = {"@context": iiif.P3, "id": f"{base}/top.json", "type": "Collection", "label": {"en": ["Everything"]}}
        top["items"] = [
            {"id": f"{base}/talks.json", "type": "Collection", "label": {"en": ["Talks"]}},
            {"id": f"{base}/gone.json", "type": "Collection", "label": {"en": ["Gone"]}},  # can't be read: skipped
            {"id": f"{base}/inline", "type": "Collection", "label": {"en": ["Inline"]}, "items": []},
        ]
        (site / "talks.json").write_text(json.dumps(talks))
        (site / "top.json").write_text(json.dumps(top))
        pv = c.post("/api/v1/import/iiif/preview", headers=h, json={"url": f"{base}/top.json"}).json()
        assert (pv["total"], pv["collections"], pv["more"]) == (1, 3, False)
        assert pv["items"] == [{"id": f"{base}/manifest.json", "type": "Manifest", "label": "Oral history 7", "path": ["Talks"]}]
        assert c.post("/api/v1/namespaces", headers=h, json={"name": "oral2"}).status_code == 200
        shelf = c.post("/api/v1/namespaces/oral2/collections", headers=h, json={"name": "From elsewhere"}).json()["id"]
        wrong = {"url": f"{base}/top.json", "namespace": "oral", "collection": shelf, "wait": True}
        assert c.post("/api/v1/import/iiif", headers=h, json=wrong).status_code == 404  # a collection of another namespace
        r = c.post("/api/v1/import/iiif", headers=h, json={**wrong, "namespace": "oral2"})
        assert r.status_code == 200, r.text
        [rid2] = r.json()["recordings"]
        assert c.get(f"/api/v1/recordings/{rid2}", headers=h).json()["collection"] == shelf
    finally:
        srv.shutdown()


def test_access_form_refuses_passwords_when_they_are_off(env):
    """With auth.passwords off, the IIIF sign-in page offers no password form and a submitted one answers 403."""
    anon = env.client()
    csrf = csrf_of(anon.get("/iiif/auth/access", params={"origin": VIEWER}))  # a form opened while passwords were on
    with mock.patch.object(auth, "passwords_on", return_value=False):  # as on an install that turned them off
        r = anon.post("/iiif/auth/access", data={"email": "root@x.io", "password": "root password 1", "origin": VIEWER, "csrf": csrf})
        page = anon.get("/iiif/auth/access", params={"origin": VIEWER})
    assert r.status_code == 403 and "window.close()" not in r.text
    assert not [x for x in r.headers.get_list("set-cookie") if x.startswith(f"{iiif_auth.COOKIE}=")]
    assert 'name="password"' not in page.text and "passkey" in page.text


def test_tokens_go_only_to_viewers_the_person_signed_in_for(env):
    """With iiif.allowed_origins at its default (*), signing in for one viewer doesn't hand tokens to every other site:
    another origin gets "sign in" until the person confirms that viewer on the access page."""
    anon = env.client()
    sign_in(anon, "root@x.io", "root password 1", VIEWER)

    def token(origin):
        page = anon.get("/iiif/auth/token", params={"messageId": "m", "origin": origin}).text
        return json.loads(re.search(r"postMessage\((\{.*?\}), ", page).group(1))

    assert token(VIEWER)["type"] == "AuthAccessToken2"
    evil = "https://evil.example"
    assert (token(evil)["type"], token(evil)["profile"]) == ("AuthAccessTokenError2", "missingAspect")
    page = anon.get("/iiif/auth/access", params={"origin": evil})
    assert 'name="continue"' in page.text and "evil.example" in page.text  # the person sees which site is asking
    done = anon.post("/iiif/auth/access", data={"continue": "1", "origin": evil, "csrf": csrf_of(page)})
    assert "window.close()" in done.text
    assert token(evil)["type"] == "AuthAccessToken2"
    assert token(VIEWER)["type"] == "AuthAccessToken2"  # confirming a second viewer keeps the first
