"""JSON and JSON Lines that aren't transcripts read as records: one segment per record, its fields as text."""

from __future__ import annotations

import json

import pytest

from app.domain import content_types, ingest, records, store


def test_a_record_as_text():
    rec = {"id": 7, "name": "Ada", "paid": True, "tags": ["vip", "eu"], "address": {"city": "Paris"}, "note": None}
    assert records.text_of(rec) == "id: 7\nname: Ada\npaid: true\ntags: vip, eu\naddress.city: Paris"
    assert records.text_of([{"sku": "A1"}, {"sku": "B2"}]) == "[0].sku: A1\n[1].sku: B2"
    assert records.text_of({}) == ""


def test_json_and_json_lines_records():
    t = records.read(json.dumps({"orders": [{"id": 1, "total": 9.5}, {"id": 2, "total": 12}], "count": 2}))
    assert (t["form"], t["timed"]) == ("records", False)
    assert [(s["text"], s["record"], s["offset"]) for s in t["segments"]] == [("id: 1\ntotal: 9.5", 0, None), ("id: 2\ntotal: 12", 1, None)]

    raw = '{"event": "login", "user": "ann"}\n\n{"event": "logout", "user": "émile"}\n'
    t = records.read(raw, lines=True)
    assert [(s["record"], s["offset"]) for s in t["segments"]] == [(0, 0), (1, 35)]
    assert raw.encode()[35:].startswith(b'{"event": "logout"')  # the offset points at the record's line
    with pytest.raises(ValueError):
        records.read('{"a": 1}\nnot json\n', lines=True)
    assert records.read("[]") is None


def test_transcripts_stay_transcripts():
    whisper = {"segments": [{"id": 0, "start": 0.0, "end": 1.5, "text": "Hello."}, {"id": 1, "start": 1.5, "end": 3, "text": "Hi."}]}
    t = ingest.read_text_transcript(json.dumps(whisper), "json")
    assert t.get("form") is None and [s["text"] for s in t["segments"]] == ["Hello.", "Hi."]
    plain = [{"text": "One."}, {"speaker": "A", "text": "Two."}]
    assert ingest.read_text_transcript(json.dumps(plain), "json").get("form") is None
    # posts with a text field are records: they have fields a transcript doesn't
    posts = [{"text": "Launch day!", "likes": 40, "author": "lens"}, {"text": "Thanks all", "likes": 3, "author": "lens"}]
    t = ingest.read_text_transcript(json.dumps(posts), "json")
    assert t["form"] == "records" and t["segments"][0]["text"] == "text: Launch day!\nlikes: 40\nauthor: lens"
    t = ingest.read_text_transcript('{"sensor": "door", "open": true}\n{"sensor": "door", "open": false}\n', "jsonl")
    assert t["form"] == "records" and t["segments"][1]["offset"] == 33


def test_records_imported_are_records(db, cfg, tmp_path):
    f = tmp_path / "orders.jsonl"
    f.write_text('{"id": 1, "item": "tea"}\n{"id": 2, "item": "cake"}\n', encoding="utf-8")
    rid = ingest.import_transcript(db, cfg, "home", f)
    rec = db.one("SELECT title, form, source FROM $r", r=store.R("recording", rid))
    assert (rec["title"], rec["form"], rec["source"]) == ("orders", "records", "transcript")
    segs = db.rows("SELECT idx, text, record, offset FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    assert [(s["text"], s["record"], s["offset"]) for s in segs] == [("id: 1\nitem: tea", 0, 0), ("id: 2\nitem: cake", 1, 25)]
    assert content_types.of_recording(db, rid)[0]["key"] == "structured"
