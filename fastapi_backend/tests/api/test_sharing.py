"""Share links: revoking one, short /s/ addresses, plays and the sites that embed them, and the page a dead link shows."""

from __future__ import annotations

import re

import pytest

from app.domain import analyze, auth, ingest
from app.domain.store import R
from tests.helpers import login, make_user, quiet, seed, write_wav

FRAMED_ON = {"Sec-Fetch-Dest": "iframe"}


@pytest.fixture
def env(db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    wav, tr = folder / "clip.wav", folder / "clip.txt"
    write_wav(wav)
    tr.write_text("[00:00] Alice: A short clip about the capsid.\n[00:02] Bob: Indeed it is short.")
    clip = ingest.import_transcript(db, cfg, "pods", tr, audio=wav, log=quiet)
    analyze.analyze_pending(db, cfg, log=quiet)
    db.q("UPDATE $r SET title = 'Capsid clip QZX'", r=R("recording", clip))
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "vi@x.io", "viewer password 1", roles={"pods": "viewer"})
    make_user(db, "out@x.io", "outsider password 1", roles={"calls": "editor"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "clip": clip,
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "vi@x.io", "viewer password 1"),
        "ho": login(client, "out@x.io", "outsider password 1"),
    }


def _share(client, rid, h, days=1):
    r = client.post(f"/api/v1/recordings/{rid}/share", json={"days": days}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


def _shares(client, rid, h):
    return {s["id"]: s for s in client.get(f"/api/v1/recordings/{rid}/shares", headers=h).json()}


def _gone(r):
    """The neutral page: 410, and nothing about the recording."""
    assert r.status_code == 410, r.text
    assert r.headers["content-type"].startswith("text/html")
    assert "This link isn’t available" in r.text and "Capsid" not in r.text and "pods" not in r.text
    assert r.headers["cache-control"] == "no-store"
    return r.text


def test_revoke_one_link(client, new_client, env, db):
    clip, he, hv, ho = env["clip"], env["he"], env["hv"], env["ho"]
    anon = new_client()
    first, second = _share(client, clip, he), _share(client, clip, he)
    assert re.fullmatch(r"[0-9a-f]{10}", first["id"]) and first["id"] != second["id"]
    assert first["embed"] == f"/embed/{clip}?s={first['token']}"
    url = f"/api/v1/recordings/{clip}/shares/{first['id']}"

    # editors of its namespace only; other namespaces look absent
    assert client.delete(url, headers=hv).status_code == 403
    assert client.delete(url, headers=ho).status_code == 404
    assert client.delete(f"/api/v1/recordings/{clip}/shares/0123456789", headers=he).status_code == 404
    assert client.delete(f"/api/v1/recordings/{clip}/shares/nothex", headers=he).status_code == 422
    assert client.delete(f"/api/v1/recordings/{env['a']}/shares/{first['id']}", headers=he).status_code == 404  # another recording's
    assert client.delete(url, headers=he).status_code == 200
    assert client.delete(url, headers=he).status_code == 200  # again: nothing changes

    links = _shares(client, clip, he)
    assert (links[first["id"]]["active"], links[first["id"]]["revoked"], links[first["id"]]["revoked_by"]) == (False, True, "ed@x.io")
    assert links[first["id"]]["revoked_at"] and links[second["id"]]["active"] and not links[second["id"]]["revoked"]
    _gone(anon.get(first["embed"]))
    _gone(anon.get(first["short"]))
    assert anon.get(second["embed"]).status_code == 200  # the other link keeps working

    # revoking them all stops the rest, and an expired link stays expired rather than revoked
    third = _share(client, clip, he)
    db.q("UPDATE share_link SET expires_at = '2000-01-01T00:00:00Z' WHERE string::starts_with(record::id(id), $p)", p=third["id"])
    assert client.delete(f"/api/v1/recordings/{clip}/share", headers=he).status_code == 200
    links = _shares(client, clip, he)
    assert [(s["active"], s["revoked"]) for s in (links[first["id"]], links[second["id"]], links[third["id"]])] == [
        (False, True),
        (False, True),
        (False, False),
    ]
    _gone(anon.get(second["embed"]))

    audit = db.rows("SELECT action, target, detail, email FROM audit_log WHERE string::starts_with(action, 'share.')")
    assert sorted((x["action"], str(x["detail"])) for x in audit) == sorted(
        [
            ("share.create", str({"link": first["id"]})),
            ("share.create", str({"link": second["id"]})),
            ("share.create", str({"link": third["id"]})),
            ("share.revoke", str({"link": first["id"]})),
            ("share.revoke", str({"link": first["id"]})),
            ("share.revoke", str({"links": 1})),
        ]
    )
    assert {x["target"] for x in audit} == {f"recording:{clip}"} and {x["email"] for x in audit} == {"ed@x.io"}


def test_short_links(client, new_client, env, db):
    clip, he = env["clip"], env["he"]
    anon = new_client()
    link = _share(client, clip, he)
    code = link["short"].removeprefix("/s/")
    assert re.fullmatch(rf"[{auth.SHORT}]{{{auth.SHORT_LEN}}}", code)
    page = anon.get(link["short"], params={"t": 1})
    assert page.status_code == 200 and "Capsid clip QZX" in page.text
    embed = anon.get(link["embed"])
    assert page.headers["content-security-policy"] == embed.headers["content-security-policy"]
    assert "frame-ancestors 'self'" in page.headers["content-security-policy"]
    # the audio in the page is signed, so it plays without the link
    audio = re.search(r'"audio":"([^"]+)"', page.text).group(1).replace("\\u0026", "&")
    assert "sig=" in audio and anon.get(audio, headers={"Range": "bytes=0-3"}).status_code == 206
    # the code works wherever the token does, for its own recording only
    assert anon.get(f"/embed/{clip}", params={"s": code}).status_code == 200
    assert anon.get(f"/api/v1/recordings/{clip}/player", params={"s": code}).status_code == 200
    assert anon.get(f"/api/v1/recordings/{env['a']}/player", params={"s": code}).status_code == 401
    _gone(anon.get(f"/embed/{env['a']}", params={"s": code}))
    # mistyped or the wrong length
    other = code[:-1] + ("2" if code[-1] != "2" else "3")
    _gone(anon.get(f"/s/{other}"))
    _gone(anon.get(f"/s/{code}x"))
    _gone(anon.get(f"/s/{link['token']}"))  # the long token isn't a short address
    # links made before short links have none
    db.q("UPDATE share_link SET short = NONE")
    assert _shares(client, clip, he)[link["id"]]["short"] is False
    _gone(anon.get(link["short"]))
    assert anon.get(link["embed"]).status_code == 200


def test_plays_and_the_sites_that_embed_a_link(client, new_client, env, db, monkeypatch):
    clip, he = env["clip"], env["he"]
    anon = new_client()
    link, other = _share(client, clip, he), _share(client, clip, he)
    blog = {"Referer": "https://Blog.Example.org/posts/1?x=2", **FRAMED_ON}
    page = anon.get(link["embed"], headers=blog)
    played = re.search(r'data-played="([^"]+)"', page.text).group(1).replace("&amp;", "&")
    assert played == f"/embed/{clip}/played?s={link['token']}"
    anon.get(link["short"], headers={"Referer": "https://intranet.example.net:8443/", **FRAMED_ON})
    anon.get(link["embed"], headers=blog)
    # opened at the top (a link in an email), from Lens itself (the embed builder's preview), or without a Referer
    anon.get(link["embed"], headers={"Referer": "https://mail.example.com/", "Sec-Fetch-Dest": "document"})
    own = anon.get(link["embed"], headers={"Referer": f"http://127.0.0.1/recordings/{clip}", **FRAMED_ON})
    assert own.status_code == 200 and "data-played" not in own.text  # Lens's own previews don't count
    assert "data-played" in anon.get(link["embed"]).text
    signed = client.get(f"/api/v1/recordings/{clip}/embed-link", headers=he).json()["url"]
    assert "data-played" not in anon.get(signed, headers=blog).text  # signed links aren't share links

    for _ in range(3):
        assert anon.post(played).status_code == 204
    short_played = re.search(r'data-played="([^"]+)"', anon.get(link["short"]).text).group(1).replace("&amp;", "&")
    assert anon.post(short_played).status_code == 204
    assert anon.post(f"/embed/{clip}/played", params={"s": "nope"}).status_code == 204  # says nothing, counts nothing
    assert anon.post(f"/embed/{env['a']}/played", params={"s": link["token"]}).status_code == 204

    s = _shares(client, clip, he)
    assert (s[link["id"]]["plays"], s[other["id"]]["plays"]) == (4, 0)
    assert s[link["id"]]["played_at"] and s[other["id"]]["played_at"] is None
    assert sorted((x["origin"], x["opens"], bool(x["last_at"])) for x in s[link["id"]]["embedded_on"]) == [
        ("https://blog.example.org", 2, True),
        ("https://intranet.example.net:8443", 1, True),
    ]
    assert s[other["id"]]["embedded_on"] == []

    # a revoked link counts nothing more
    client.delete(f"/api/v1/recordings/{clip}/shares/{link['id']}", headers=he)
    assert anon.post(played).status_code == 204
    _gone(anon.get(link["embed"], headers={"Referer": "https://new.example.org/", **FRAMED_ON}))
    s = _shares(client, clip, he)[link["id"]]
    assert s["plays"] == 4 and len(s["embedded_on"]) == 2

    # sites per link are capped; the player still opens on the rest
    monkeypatch.setattr(auth, "EMBED_SITES", 1)
    assert anon.get(other["embed"], headers={"Referer": "https://one.example.org/", **FRAMED_ON}).status_code == 200
    assert anon.get(other["embed"], headers={"Referer": "https://two.example.org/", **FRAMED_ON}).status_code == 200
    anon.get(other["embed"], headers={"Referer": "https://one.example.org/", **FRAMED_ON})
    assert [(x["origin"], x["opens"]) for x in _shares(client, clip, he)[other["id"]]["embedded_on"]] == [("https://one.example.org", 2)]


def test_a_dead_link_shows_the_same_page(client, new_client, env, db):
    clip, he = env["clip"], env["he"]
    anon = new_client()
    link = _share(client, clip, he)
    signed = client.get(f"/api/v1/recordings/{clip}/embed-link", headers=he).json()["url"]
    expired = _share(client, clip, he)
    db.q("UPDATE share_link SET expires_at = '2000-01-01T00:00:00Z' WHERE string::starts_with(record::id(id), $p)", p=expired["id"])
    pages = {
        _gone(anon.get(f"/embed/{clip}")),  # no link at all
        _gone(anon.get(f"/embed/{clip}", params={"s": "not-a-token"})),
        _gone(anon.get(f"/embed/{env['a']}", params={"s": link["token"]})),  # another recording
        _gone(anon.get("/embed/999999", params={"s": link["token"]})),  # no such recording
        _gone(anon.get(expired["embed"])),
        _gone(anon.get(expired["short"])),
        _gone(anon.get(signed.replace("sig=", "sig=x"))),  # tampered
        _gone(anon.get(f"/embed/{clip}", headers=env["ho"])),  # a bearer token without a role there
    }
    assert len(pages) == 1
    gone = anon.get(f"/s/{link['short'][3:]}x")
    assert "frame-ancestors 'self'" in gone.headers["content-security-policy"]  # it shows inside the host page's frame
    assert anon.get(f"/embed/{clip}", headers=env["ho"] | {"Accept": "application/json"}).status_code == 410
    assert anon.get(link["embed"]).status_code == 200
    assert anon.get(f"/embed/{clip}", headers=he).status_code == 200  # a bearer token with a role still works
