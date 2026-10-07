"""Chat exports read as transcripts: one segment per message, its sender the speaker, when it was sent kept."""

from __future__ import annotations

import json

from app.domain import chats, content_types, ingest, store

WHATSAPP_ANDROID = """31/12/2023, 21:40 - Messages and calls are end-to-end encrypted. No one outside of this chat can read them.
31/12/2023, 21:41 - Alice: Happy new year!
31/12/2023, 21:42 - Bob: Same to you
see you tomorrow
01/01/2024, 09:05 - Alice: <Media omitted>
"""

WHATSAPP_IPHONE = "[1/2/24, 9:41:05 PM] Alice: Lunch on the 13th?\n[1/13/24, 10:00:00 AM] ‎Bob: Yes\n"

IMESSAGE = """Mar 01, 2023  2:05:31 PM
+15558675309
Are we still on?

Mar 01, 2023  2:06:00 PM (Read by them after 3 seconds)
Me
Yes, at six.
Bring the cake.
"""


def test_whatsapp_android_and_iphone():
    t = chats.read(WHATSAPP_ANDROID, "WhatsApp Chat with Alice.txt")
    assert (t["app"], t["form"], t["timed"]) == ("whatsapp", "chat", False)
    assert [(s["speaker"], s["text"]) for s in t["segments"]] == [
        ("Alice", "Happy new year!"),
        ("Bob", "Same to you\nsee you tomorrow"),
        ("Alice", "<Media omitted>"),
    ]
    assert [s["at"] for s in t["segments"]] == ["2023-12-31T21:41:00", "2023-12-31T21:42:00", "2024-01-01T09:05:00"]
    assert t["recorded_at"] == "2023-12-31T21:41:00"
    assert all(a["t1"] <= b["t0"] for a, b in zip(t["segments"], t["segments"][1:], strict=False))  # a reading pace, in order

    t = chats.read(WHATSAPP_IPHONE)
    # 1/13 says the month comes first; PM is afternoon; marks WhatsApp puts before names go
    assert [(s["speaker"], s["at"]) for s in t["segments"]] == [("Alice", "2024-01-02T21:41:05"), ("Bob", "2024-01-13T10:00:00")]


def test_telegram_slack_and_imessage():
    tg = {
        "name": "Book club",
        "type": "private_group",
        "messages": [
            {"id": 1, "type": "service", "action": "create_group", "actor": "Ann", "date": "2023-05-01T10:00:00"},
            {"id": 2, "type": "message", "from": "Ann", "date": "2023-05-01T10:01:00", "text": "Chapter 3 tonight"},
            {"id": 3, "type": "message", "from": "Ben", "date": "2023-05-01T10:02:00", "text": ["See ", {"type": "bold", "text": "you"}]},
        ],
    }
    t = chats.read(json.dumps(tg))
    assert (t["app"], t["title"]) == ("telegram", "Book club")
    assert [(s["speaker"], s["text"], s["at"]) for s in t["segments"]] == [
        ("Ann", "Chapter 3 tonight", "2023-05-01T10:01:00"),
        ("Ben", "See you", "2023-05-01T10:02:00"),
    ]

    slack = [
        {"type": "message", "subtype": "channel_join", "user": "U1", "text": "<@U1> has joined", "ts": "1700000000.000100"},
        {"type": "message", "user": "U1", "user_profile": {"real_name": "Cara"}, "text": "Deploy is done", "ts": "1700000060.000200"},
        {"type": "message", "user": "U2", "text": "thanks", "ts": "1700000120.000300"},
    ]
    t = chats.read(json.dumps(slack))
    assert [(s["speaker"], s["text"], s["at"]) for s in t["segments"]] == [
        ("Cara", "Deploy is done", "2023-11-14T22:14:20"),
        ("U2", "thanks", "2023-11-14T22:15:20"),
    ]

    t = chats.read(IMESSAGE)
    assert t["app"] == "imessage"
    assert [(s["speaker"], s["text"], s["at"]) for s in t["segments"]] == [
        ("+15558675309", "Are we still on?", "2023-03-01T14:05:31"),
        ("Me", "Yes, at six.\nBring the cake.", "2023-03-01T14:06:00"),
    ]


def test_what_isnt_a_chat():
    assert chats.read("Alice: Hi.\nBob: Hello.") is None  # a transcript with speakers, not an export
    assert chats.read('{"segments": [{"start": 0, "end": 1, "text": "hi"}]}') is None
    assert chats.read('[{"start": 0, "end": 1, "text": "hi"}]') is None
    assert chats.read("31/12/2023, 21:41 - Alice: just one") is None  # one message isn't a chat
    assert chats.read("{not json") is None


def test_a_chat_imported_is_a_chat_export(db, cfg, tmp_path):
    f = tmp_path / "WhatsApp Chat with Alice.txt"
    f.write_text(WHATSAPP_ANDROID, encoding="utf-8")
    rid = ingest.import_transcript(db, cfg, "home", f)
    rec = db.one("SELECT title, form, recorded_at, source FROM $r", r=store.R("recording", rid))
    assert (rec["title"], rec["form"], rec["source"]) == ("WhatsApp Chat with Alice", "chat", "transcript")
    assert str(rec["recorded_at"]).startswith("2023-12-31T21:41")
    segs = db.rows("SELECT idx, local_speaker, at, text FROM segment WHERE recording = $r ORDER BY idx", r=rid)
    assert [(s["local_speaker"], s["at"]) for s in segs][:2] == [("Alice", "2023-12-31T21:41:00"), ("Bob", "2023-12-31T21:42:00")]
    assert content_types.of_recording(db, rid)[0]["key"] == "chat_export"

    # a Telegram export is named after its chat, and a .json chat isn't taken for a transcript
    g = tmp_path / "result.json"
    g.write_text(json.dumps({"name": "Family", "messages": [
        {"type": "message", "from": "Mum", "date": "2023-01-01T08:00:00", "text": "Morning"},
        {"type": "message", "from": "Dad", "date": "2023-01-01T08:01:00", "text": "Morning!"},
    ]}), encoding="utf-8")  # fmt: skip
    rid = ingest.import_transcript(db, cfg, "home", g)
    assert db.one("SELECT title FROM $r", r=store.R("recording", rid))["title"] == "Family"
    assert content_types.of_recording(db, rid)[0]["key"] == "chat_export"


def test_forms_rule(db):
    assert content_types.get(db, "chat_export")["rules"] == {"forms": ["chat"]}
    import pytest

    with pytest.raises(ValueError):
        content_types.create(db, "text", "Odd", rules={"forms": ["poem"]})
    key = content_types.create(db, "text", "Team chat", rules={"forms": ["chat"], "pattern": "team"})
    rec = {"source": "transcript", "path": "/in/team.txt", "title": "team", "form": "chat"}
    assert content_types.recognise(db, rec)["key"] in ("chat_export", key)
    assert content_types.recognise(db, {**rec, "form": None})["key"] == "text"
