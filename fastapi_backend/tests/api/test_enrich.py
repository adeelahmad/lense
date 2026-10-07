"""Places and dates from anytopdf as hints for analysis (enrich.py, analysis.anytopdf): off by default; on, the places
a text names and the dates it gives become entities like the extractor's, a photo's GPS fix is named, and what was
found is kept on the recording for its members only."""

from __future__ import annotations

import io
import os

import pytest
from PIL import Image

from app.core import security
from app.domain import components, enrich, settings, store
from tests import fake_anytopdf
from tests.api.test_documents import _png, _upload
from tests.helpers import drain, login, make_user

R = store.R
REAL = os.environ.get("LENS_TEST_ANYTOPDF")
TEXT = b"Invoice 4471 from Northwind Traders\n\nMeeting in Berlin on 2026-10-09, paid 3 March 2026.\n\nMobile phones next Friday.\n"


@pytest.fixture
def app(cfg, db):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def env(client, db):
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "own@x.io", "owner password 1", roles={"pods": "owner"})
    return {"he": login(client, "ed@x.io", "editor password 1"), "ho": login(client, "own@x.io", "owner password 1")}


@pytest.fixture
def here(cfg, tmp_path):
    log = tmp_path / "anytopdf.log"
    cfg["documents"]["anytopdf"] = fake_anytopdf.make(tmp_path / "anytopdf", log)
    return log


def _entities(db, rid):
    rows = db.rows("SELECT out.name AS name, out.type AS type FROM mentions WHERE recording = $r", r=rid)
    return {(m["name"], m["type"]) for m in rows}


def _enrichment(db, rid):
    return db.one("SELECT enrichment FROM $r", r=R("recording", rid)).get("enrichment")


def test_off_by_default_nothing_is_run(here, client, env, db, cfg):
    assert store.DEFAULTS["analysis"]["anytopdf"] is False
    up = _upload(client, env["he"], TEXT, "notes.txt")
    drain(db, settings.effective(db, cfg))
    assert not any("--dump-graph" in r["args"] for r in fake_anytopdf.runs(here))
    assert _enrichment(db, up["recording"]) is None
    assert ("Berlin", "PLACE") not in _entities(db, up["recording"])
    assert client.get(f"/api/v1/recordings/{up['recording']}", headers=env["he"]).json().get("enrichment") is None


def test_places_and_dates_become_hints(here, client, env, db, cfg):
    cfg["analysis"]["anytopdf"] = True
    up = _upload(client, env["he"], TEXT, "notes.txt")
    drain(db, settings.effective(db, cfg))
    rid = up["recording"]
    got = _entities(db, rid)
    assert ("Berlin", "PLACE") in got  # the gazetteer's place, a name the rules alone don't know as a place
    assert ("2026-10-09", "DATE") in got
    assert not any(n == "Mobile" and t == "PLACE" for n, t in got)  # anytopdf wasn't sure: left out
    found = _enrichment(db, rid)
    assert found["by"] == "anytopdf 0.4.0"
    assert [p["name"] for p in found["places"]] == ["Berlin, Germany"]
    assert {(d["text"], d.get("iso")) for d in found["dates"]} == {
        ("2026-10-09", "2026-10-09"),
        ("3 March 2026", "2026-03-03"),
        ("next Friday", None),
    }
    # run offline, with no plugins; the text read for places and dates is Lens's own, so anytopdf does no OCR
    run = next(r for r in fake_anytopdf.runs(here) if "--dump-graph" in r["args"])
    assert {"--no-plugins", "--no-config"} <= set(run["args"]) and run["args"][run["args"].index("--ocr") + 1] == "off"
    assert run["proxy"] and "127.0.0.1:9" in run["proxy"]  # netguard's nowhere
    # members see what was found
    rec = client.get(f"/api/v1/recordings/{rid}", headers=env["he"]).json()
    assert {k: v for k, v in rec["enrichment"]["places"][0].items() if v is not None} == {
        "name": "Berlin, Germany",
        "city": "Berlin",
        "country": "Germany",
        "country_code": "DE",
        "lat": 52.524,
        "lon": 13.411,
        "from": "text",
        "said": "Berlin",
    }
    assert "photo_fp" not in rec["enrichment"]


def test_where_a_photo_was_taken_for_members_only(here, client, new_client, env, db, cfg):
    cfg["analysis"]["anytopdf"] = True
    photo = _png(Image.new("RGB", (64, 48), "white")) + b"GPS"  # the fake reads a GPS fix from these bytes
    up = _upload(client, env["he"], photo, "beach.png")
    drain(db, settings.effective(db, cfg))
    rid = up["recording"]
    place = _enrichment(db, rid)["place"]
    assert (place["name"], place["from"], place["km"]) == ("Paris 16 Passy, Ile-de-France, France", "gps", 1.4)
    runs = [r for r in fake_anytopdf.runs(here) if r["args"][r["args"].index("convert") + 1].endswith(".png")]
    assert len(runs) == 1 and runs[0]["args"][runs[0]["args"].index("--location") + 1] == "gps"
    # analysed again: the photo isn't read again, and its place stays
    enrich.enrich(db, settings.effective(db, cfg), rid, [])
    assert len([r for r in fake_anytopdf.runs(here) if r["args"][r["args"].index("convert") + 1].endswith(".png")]) == 1
    assert _enrichment(db, rid)["place"]["city"] == "Paris 16 Passy"
    # a link signed for a visitor (an embed, a public page) opens it, but where it was taken stays with its members
    seen = new_client().get(security.sign_path(f"/api/v1/recordings/{rid}"))
    assert seen.status_code == 200, seen.text
    assert seen.json().get("enrichment") is None
    assert new_client().get(security.sign_path(f"/api/v1/recordings/{rid}", full=True)).json()["enrichment"]["place"]
    assert client.get(f"/api/v1/recordings/{rid}", headers=env["he"]).json()["enrichment"]["place"]["from"] == "gps"


def test_a_failing_anytopdf_never_stops_analysis(here, client, env, db, cfg, monkeypatch):
    cfg["analysis"]["anytopdf"] = True

    def broken(cfg, src, args):
        raise ValueError("anytopdf couldn't read it (exit 3)")

    monkeypatch.setattr(enrich.anytopdf, "graph", broken)
    up = _upload(client, env["he"], TEXT, "notes.txt")
    drain(db, settings.effective(db, cfg))
    rec = db.one("SELECT status FROM $r", r=R("recording", up["recording"]))
    assert rec["status"] == "analyzed"
    assert _enrichment(db, up["recording"]) is None


def test_fetched_for_analysis_and_only_waits_analysis(cfg):
    cfg["analysis"]["anytopdf"] = True
    c = components.BY_ID["anytopdf"]
    assert c.needed(cfg, None) is bool(enrich.anytopdf.archive())
    assert c.serves(cfg, None) == {"analyze"}
    cfg["documents"]["converter"] = "anytopdf"
    assert c.serves(cfg, None) == {"transcribe", "analyze"}


@pytest.mark.skipif(not REAL, reason="LENS_TEST_ANYTOPDF names a real anytopdf 0.4.0")
def test_the_real_anytopdf(cfg, tmp_path):
    cfg["documents"]["anytopdf"] = REAL
    src = tmp_path / "t.txt"
    src.write_bytes(TEXT)
    places, dates = enrich.text_finds(enrich.anytopdf.graph(cfg, src, enrich.TEXT_ARGS))
    assert any(p["city"] == "Berlin" and p["said"] == "Berlin" for p in places)
    assert {"2026-10-09", "2026-03-03"} <= {d.get("iso") for d in dates}
    img = Image.new("RGB", (64, 48), "white")
    exif = Image.Exif()
    exif.get_ifd(0x8825).update({1: "N", 2: (48.0, 51.0, 29.0), 3: "E", 4: (2.0, 17.0, 40.0)})
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    (tmp_path / "p.jpg").write_bytes(out.getvalue())
    place = enrich.photo_place(enrich.anytopdf.graph(cfg, tmp_path / "p.jpg", enrich.PHOTO_ARGS))
    if place is not None:  # anytopdf reads GPS with exiftool, which may not be here
        assert (place["country_code"], place["from"]) == ("FR", "gps")
