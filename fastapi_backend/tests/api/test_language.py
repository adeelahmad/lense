"""An asset's language (docs/api.md#an-assets-language): transcription keeps the one it hears, and a person sets or
corrects it as a BCP 47 tag with a region (en-GB, pt-BR), which transcribing again leaves as it is."""

from __future__ import annotations

import pytest

from app.domain import ingest, library, metadata, pipelines
from app.domain.store import R
from tests.api.test_mcp import tool, tool_error
from tests.helpers import login, make_user, seed


@pytest.fixture
def env(app, db, cfg, folder, client):
    a, b, call = seed(db, cfg, folder)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor", "calls": "viewer"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "a": a,
        "b": b,
        "call": call,
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


@pytest.mark.parametrize(
    ("given", "tag"),
    [
        ("en", "en"),
        ("EN", "en"),
        ("en-gb", "en-GB"),
        ("en_US", "en-US"),
        (" pt-br ", "pt-BR"),
        ("pt-PT", "pt-PT"),
        ("zh-hant-tw", "zh-Hant-TW"),
        ("SR-LATN-rs", "sr-Latn-RS"),
        ("es-419", "es-419"),
        ("de-CH-1996", "de-CH-1996"),
        ("en-GB-x-scouse", "en-GB-x-scouse"),
        ("yue", "yue"),
        ("", None),
        (None, None),
    ],
)
def test_tags_are_written_the_usual_way(given, tag):
    assert metadata.language_tag(given) == tag


@pytest.mark.parametrize("bad", ["english", "e", "none", "nospeech", "en-GBR", "en--GB", "en-", "12", "en GB", "pt-BR-"])
def test_what_isnt_a_tag_is_refused(bad):
    with pytest.raises(ValueError, match="isn't a language tag"):
        metadata.language_tag(bad)


def test_editors_set_and_correct_an_assets_language(client, db, env):
    a, he, hv = env["a"], env["he"], env["hv"]
    url = f"/api/v1/resources/{a}"
    db.q("UPDATE $r SET language = 'en'", r=R("recording", a))  # as transcription heard it
    got = client.get(url, headers=hv).json()
    assert (got["language"], got["language_set"]) == ("en", False)

    assert client.patch(url, headers=hv, json={"language": "en-GB"}).status_code == 403  # viewers read
    r = client.patch(url, headers=he, json={"language": "en_gb"})
    assert r.status_code == 200, r.text
    assert (r.json()["language"], r.json()["language_set"]) == ("en-GB", True)
    assert client.get(url, headers=hv).json()["language"] == "en-GB"
    bad = client.patch(url, headers=he, json={"language": "British"})
    assert bad.status_code == 400 and "isn't a language tag" in bad.json()["detail"]
    assert client.patch(url, headers=he, json={"language": "x" * 41}).status_code == 422
    assert client.patch(url, headers=he, json={}).status_code == 400
    # with a title in the same call, and the same tag again changes nothing
    r = client.patch(url, headers=he, json={"title": "Episode one", "language": "en-GB"})
    assert (r.json()["title"], r.json()["language"]) == ("Episode one", "en-GB")
    audit = db.rows("SELECT email, detail FROM audit_log WHERE action = 'recording.language'")
    assert [(x["email"], x["detail"]) for x in audit] == [("ed@x.io", {"from": "en", "to": "en-GB"})]

    # the Language filter counts it apart from en, written the usual way, and finds it whatever the case
    db.q("UPDATE $r SET language = 'en'", r=R("recording", env["b"]))
    langs = client.get("/api/v1/resources/languages", params={"ns": "pods"}, headers=he).json()
    assert langs == [{"language": "en", "recordings": 1}, {"language": "en-GB", "recordings": 1}]
    assert [x["id"] for x in client.get("/api/v1/resources", params={"language": "en-gb"}, headers=he).json()] == [a]
    # the Dublin Core record says it too
    assert metadata.defaults(db, {"iiif": {}}, a)["language"] == ["en-GB"]

    # null forgets it
    r = client.patch(url, headers=he, json={"language": None})
    assert (r.json()["language"], r.json()["language_set"]) == (None, False)


def test_transcribing_again_keeps_the_language_a_person_set(db, cfg, env):
    a = env["a"]
    nid = db.one("SELECT space FROM $r", r=R("recording", a))["space"]
    segs = [{"t0": 0, "t1": 900, "text": "Olá a todos.", "lang": "pt"}]

    def transcribe():
        ingest.write_transcript(db, a, nid, segs, {"language": ingest._language(segs), "status": "transcribed"})
        rec = db.one("SELECT language, language_set FROM $r", r=R("recording", a))
        return rec.get("language"), bool(rec.get("language_set"))

    assert transcribe() == ("pt", False)  # detected, kept
    library.set_language(db, cfg, a, "pt-br")
    assert transcribe() == ("pt-BR", True)  # the person's stays
    library.set_language(db, cfg, a, None)
    assert transcribe() == ("pt", False)  # forgotten: detected again


def test_a_pipeline_condition_on_en_takes_its_regions(db, cfg, env):
    a = env["a"]
    library.set_language(db, cfg, a, "en-GB")
    assert pipelines.condition_ok(db, a, {"languages": ["en"]})
    assert pipelines.condition_ok(db, a, {"languages": ["EN-gb"]})
    assert not pipelines.condition_ok(db, a, {"languages": ["en-US", "fr"]})
    assert not pipelines.condition_ok(db, a, {"languages": ["e"]})


def test_agents_set_it_over_mcp(client, db, env):
    a, he, hv = env["a"], env["he"], env["hv"]
    got = tool(client, he, "set_language", recording_id=a, language="pt-pt")
    assert (got["language"], got["language_set"]) == ("pt-PT", True)
    assert (lambda r: (r["language"], r["language_set"]))(tool(client, hv, "get_recording", recording_id=a)) == ("pt-PT", True)
    assert "isn't a language tag" in tool_error(client, he, "set_language", recording_id=a, language="Portuguese")
    assert tool_error(client, hv, "set_language", recording_id=a, language="fr")  # viewers read
    assert tool(client, he, "set_language", recording_id=a)["language"] is None  # left out: forgotten
    rec = db.one("SELECT language, language_set FROM $r", r=R("recording", a))
    assert (rec.get("language"), rec.get("language_set")) == (None, None)
