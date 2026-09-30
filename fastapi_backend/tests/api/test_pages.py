"""The pages the API serves itself: the embeddable player, static reports (signed links) and the redirect at /."""

from __future__ import annotations

import pathlib
import re

import pytest

from app.config import settings
from app.core.security import sign_path
from app.domain import analyze, ingest, render
from tests.helpers import login, make_user, quiet, seed, write_wav


@pytest.fixture
def env(db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.")
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "wav": wav,
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
    }


def test_index_redirects_to_the_frontend(client):
    r = client.get("/", params={"iiif-content": "abc"}, follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == settings.FRONTEND_URL.rstrip("/") + "/?iiif-content=abc"


def test_embed_needs_a_share_or_signed_link(client, new_client, env):
    a, clip, he = env["a"], env["clip"], env["he"]
    anon = new_client()
    assert anon.get(f"/embed/{a}").status_code == 401
    tok = client.post(f"/api/v1/recordings/{a}/share", json={"days": 1}, headers=he).json()["token"]
    r = anon.get(f"/embed/{a}", params={"s": tok})
    assert r.status_code == 200
    assert "frame-ancestors 'self'" in r.headers["content-security-policy"]
    assert anon.get(f"/embed/{env['b']}", params={"s": tok}).status_code == 401  # one recording only
    client.delete(f"/api/v1/recordings/{a}/share", headers=he)
    assert anon.get(f"/embed/{a}", params={"s": tok}).status_code == 401  # revoked

    # a signed link from the API; the audio in the page is signed too, so it plays without a sign-in or share link
    link = client.get(f"/api/v1/recordings/{clip}/embed-link", params={"t": 1.5}, headers=he).json()["url"]
    page = anon.get(link)
    assert page.status_code == 200
    audio = re.search(r'"audio":"([^"]+)"', page.text).group(1).replace("\\u0026", "&")
    assert audio.startswith(f"/api/v1/recordings/{clip}/audio?") and "sig=" in audio
    r = anon.get(audio, headers={"Range": "bytes=0-9"})
    assert (r.status_code, r.content) == (206, env["wav"].read_bytes()[:10])
    assert anon.get(link.replace("sig=", "sig=x")).status_code == 401  # tampered
    assert anon.get(f"/embed/{env['call']}", headers=he).status_code == 404  # a bearer token still works, within its roles


def test_reports_are_served_by_signed_link(client, new_client, env, db, cfg):
    render.build_reports(db, cfg, log=quiet)
    hv, anon = env["hv"], new_client()
    # with a bearer token, a role in the namespace is enough; other namespaces look absent
    assert client.get("/reports/pods/index.html", headers=hv).status_code == 200
    assert client.get("/reports/pods/", headers=hv).status_code == 200
    assert client.get("/reports/calls/index.html", headers=hv).status_code == 404
    assert anon.get("/reports/pods/index.html").status_code == 401
    assert client.get("/reports/pods/nope.html", headers=hv).status_code == 404
    assert client.get("/reports/pods/..%2F..%2Fsecret.html", headers=hv).status_code == 404

    # the API hands out a signed report_url; links inside the page are signed as it is served
    rec = client.get(f"/api/v1/recordings/{env['clip']}", headers=hv).json()
    assert "sig=" in rec["report_url"]
    page = anon.get(rec["report_url"])
    assert page.status_code == 200
    assert "script-src 'self' 'unsafe-inline'" in page.headers["content-security-policy"]
    assert "ArchivePlayer.mount" in page.text and "<!--<script>" not in page.text
    back = re.search(r'href="(/reports/pods/index\.html\?[^"]+)"', page.text).group(1).replace("&amp;", "&")
    assert anon.get(back).status_code == 200  # the link back to the overview works on its own
    audio = re.search(rf'(/api/v1/recordings/{env["clip"]}/audio\?[^"\\]+)', page.text).group(1)
    r = anon.get(audio, headers={"Range": "bytes=0-9"})
    assert (r.status_code, r.content) == (206, env["wav"].read_bytes()[:10])

    index = anon.get(sign_path("/reports/pods/index.html"))
    links = [x.replace("&amp;", "&") for x in re.findall(r'href="(/reports/pods/[\w.-]+\.html\?[^"]+)"', index.text)]
    assert len(links) == 3 and all(anon.get(x).status_code == 200 for x in links)
    assert anon.get(sign_path("/reports/pods/index.html").replace("pods", "calls")).status_code == 401  # signed for one path
    assert anon.get(sign_path("/reports/nowhere/index.html")).status_code == 404

    # reports rendered from people's templates may not run scripts
    (pathlib.Path(cfg["data_dir"]) / "reports" / "pods" / "brief--1.html").write_text("<p>hi</p>")
    r = anon.get(sign_path("/reports/pods/brief--1.html"))
    assert r.status_code == 200 and "script-src 'none'" in r.headers["content-security-policy"]
