"""The assistant in Telegram through a bot of Lens's own: set up in the app with the bot's token, it answers private
chats and what's said to it in groups, as the account it was given, hears voice notes and holds files for the
assistant to import, with no Matterbridge in between."""

from __future__ import annotations

import pytest

from app.domain import channels, settings, telegram, uploads, voice
from tests import fake_llm, fake_telegram
from tests.api._assist import start_llm
from tests.helpers import login, make_user, seed

update = fake_telegram.update


@pytest.fixture
def tg():
    srv, url = fake_telegram.start()
    telegram._BOTS.clear()
    yield fake_telegram.Handler, url
    srv.shutdown()


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


@pytest.fixture
def bot(app, db, cfg, folder, tg):
    """Seeded recordings, a viewer of pods for the bot to answer as, and the bot set up for them."""
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "bot@x.io", "bot password 1", roles={"pods": "viewer"})
    h, url = tg
    cfg["telegram"] = {
        **cfg["telegram"],
        "enabled": True,
        "api_url": url,
        "token": fake_telegram.TOKEN,
        "account": "bot@x.io",
        "users": ["alice", "bob", "carol", "10"],
    }
    cfg["ai"]["tools"] = False
    return h


def run(db, cfg, me="api-1"):
    return channels.tick(db, cfg, cfg, me, telegram.TELEGRAM)


def test_the_bot_is_set_up_and_checked_in_the_app(client, db, cfg, tg):
    h, url = tg
    make_user(db, "root@x.io", "root password 1", admin=True)
    a = login(client, "root@x.io", "root password 1")
    assert client.get("/api/v1/settings/telegram", headers=a).json()["state"] == "off"
    assert client.post("/api/v1/settings/telegram/test", headers=a).json() == {
        "ok": False,
        "error": "paste the bot's token from @BotFather first",
    }
    assert client.put("/api/v1/settings/telegram", headers=a, json={"token": "not a token"}).status_code == 400
    wrong = "123456789:" + "B" * 35
    r = client.put("/api/v1/settings/telegram", headers=a, json={"token": wrong, "api_url": url + "/", "enabled": True})
    assert r.status_code == 200, r.text
    saved = settings.Settings(db, cfg).current()["telegram"]
    assert saved["token"] == wrong and saved["api_url"] == url and saved["account"] == "root@x.io"  # who turned it on
    assert wrong not in client.get("/api/v1/settings", headers=a).text  # the token is a secret
    assert "doesn't know that bot token" in client.post("/api/v1/settings/telegram/test", headers=a).json()["error"]
    client.put("/api/v1/settings/telegram", headers=a, json={"token": fake_telegram.TOKEN})
    assert client.post("/api/v1/settings/telegram/test", headers=a).json() == {"ok": True, "error": None}
    assert client.get("/api/v1/settings/telegram", headers=a).json()["state"] == "starting"
    r = client.put("/api/v1/settings/telegram", headers=a, json={"users": ["@Alice", " ", "12345"], "rooms": ["-100123 = Pods"]})
    assert r.status_code == 200, r.text
    saved = settings.Settings(db, cfg).current()["telegram"]
    assert saved["users"] == ["Alice", "12345"] and saved["rooms"] == ["-100123 = pods"]
    for bad in ({"account": "bot"}, {"answer": "sometimes"}, {"approve": "always"}, {"poll_seconds": 2}, {"api_url": "telegram"}):
        assert client.put("/api/v1/settings/telegram", headers=a, json=bad).status_code == 400, bad


def test_matterbridge_reading_the_same_bot_is_caught(db, cfg, bot):
    cfg["matterbridge"].update(run=True, telegram_token=fake_telegram.TOKEN)
    assert "Matterbridge reads this bot too" in telegram.TELEGRAM.check(db, cfg)
    cfg["matterbridge"].update(run=False)
    assert telegram.TELEGRAM.check(db, cfg) is None


def test_it_answers_private_chats_and_what_is_said_to_it_in_groups(db, cfg, bot, llm):
    bot.updates = [
        update("what leaves on Friday?"),  # a private chat: always for Lens
        update("lunch anyone?", chat_id=-100, chat_type="group", title="Team", user="bob", user_id=8),
        update("@lens_test_bot when does the shipment leave?", chat_id=-100, chat_type="group", title="Team", user="bob", user_id=8),
        update("Lens, hello", chat_id=-100, chat_type="group", title="Team", user="carol", user_id=9),
        update(
            "and on Monday?",
            chat_id=-100,
            chat_type="group",
            title="Team",
            user="carol",
            user_id=9,
            reply_to_message={"message_id": 1, "from": fake_telegram.BOT, "chat": {"id": -100}},
        ),
        update("/start", user="dave", user_id=10, chat_id=43),
    ]
    assert run(db, cfg) == 5
    assert [s["chat_id"] for s in bot.sent] == ["42", "-100", "-100", "-100", "43"]
    assert all(s["text"] == "OK" for s in bot.sent)  # the fake model's answer
    assert "reply_parameters" not in bot.sent[0] and bot.sent[1]["reply_parameters"]["message_id"]  # groups: as a reply
    asked = [m["messages"][-1]["content"] for m in llm.seen if m.get("messages")]
    assert any(c.endswith("Question: what leaves on Friday?") for c in asked)
    assert any(c.endswith("Question: when does the shipment leave?") for c in asked)
    assert any(c.endswith("Question: Hello! What can you do?") for c in asked)
    rows = db.rows("SELECT title, account FROM chat WHERE kind = 'bridge' ORDER BY title")
    assert [r["title"] for r in rows] == ["Telegram · Team · bob", "Telegram · Team · carol", "Telegram · alice", "Telegram · dave"]
    # what was read is confirmed on the next look, and isn't answered twice
    last = max(u["update_id"] for u in bot.updates)
    bot.sent = []
    assert run(db, cfg) == 0 and not bot.sent and bot.offsets[-1] == last + 1 and not bot.updates
    # the chats it has seen, for Settings (their ids are what telegram.rooms names)
    s = telegram.status(db, cfg)
    assert s["state"] == "running" and s["answered"] == 5 and s["bot"] == "lens_test_bot"
    assert {"id": "-100", "title": "Team"} in s["chats"] and {"id": "42", "title": "private chat with alice"} in s["chats"]
    assert {"id": "10", "name": "dave", "allowed": True} in s["people"]


def test_only_people_let_in_are_answered(db, cfg, bot, llm):
    """Anyone can find a bot: someone not in telegram.users is told once, and listed in Settings to let in."""
    bot.updates = [update("hi", user="eve", user_id=66), update("Lens, hi", user="eve", user_id=66, chat_id=-100, chat_type="group")]
    assert run(db, cfg) == 0
    assert [s["text"] for s in bot.sent] == [telegram.STRANGER]  # once, whatever else they say
    assert not db.rows("SELECT id FROM chat WHERE kind = 'bridge'")
    assert {"id": "66", "name": "eve", "allowed": False} in telegram.status(db, cfg)["people"]
    bot.updates = [update("lunch?", user="mallory", user_id=67, chat_id=-100, chat_type="group")]
    assert run(db, cfg) == 0 and len(bot.sent) == 1  # not said to Lens: not answered at all
    cfg["telegram"]["users"] = ["66"]  # let in by user id
    bot.updates = [update("hi", user="eve", user_id=66)]
    assert run(db, cfg) == 1 and bot.sent[-1]["text"] == "OK"
    cfg["telegram"]["users"] = []  # nobody let in: nobody answered
    bot.updates = [update("hi", user="eve", user_id=66)]
    assert run(db, cfg) == 0 and len(bot.sent) == 2


def test_clear_works_in_telegram(db, cfg, bot, llm):
    bot.updates = [update("hi")]
    run(db, cfg)
    bot.updates = [update("/clear@lens_test_bot")]
    assert run(db, cfg) == 1
    assert bot.sent[-1]["text"].startswith("Cleared.")


def test_a_chat_given_to_a_namespace_assistant(db, cfg, bot, llm):
    cfg["telegram"]["rooms"] = ["-100 = pods"]
    bot.updates = [update("Lens, when do episodes ship?", chat_id=-100, chat_type="group", title="Team")]
    assert run(db, cfg) == 1
    assert db.one("SELECT VALUE scope FROM chat WHERE kind = 'bridge' LIMIT 1") == {"namespaces": ["pods"]}


def test_voice_notes_are_heard(db, cfg, bot, llm, monkeypatch):
    bot.files["voice-1"] = b"OggS..."
    monkeypatch.setattr(voice, "can_transcribe", lambda cfg: None)
    bot.updates = [update(voice={"file_id": "voice-1", "duration": 2})]
    assert run(db, cfg) == 1
    assert "no speech-to-text engine" in bot.sent[-1]["text"]
    heard = []
    monkeypatch.setattr(voice, "can_transcribe", lambda cfg: "sensevoice")
    monkeypatch.setattr(voice, "transcribe", lambda cfg, data: heard.append(data) or {"text": "what leaves on Friday?"})
    bot.updates = [update(voice={"file_id": "voice-1", "duration": 2})]
    assert run(db, cfg) == 1
    assert heard == [b"OggS..."]
    assert bot.sent[-1]["text"] == "(Heard: “what leaves on Friday?”)\n\nOK"
    asked = [m["messages"][-1]["content"] for m in llm.seen if m.get("messages")]
    assert asked[-1].endswith("Question: what leaves on Friday?")
    # in a group, a voice note that isn't a reply to the bot isn't listened to
    bot.updates = [update(voice={"file_id": "voice-1"}, chat_id=-100, chat_type="group", title="Team")]
    assert run(db, cfg) == 0 and len(heard) == 1


def test_files_are_held_for_the_assistant_to_import(db, cfg, bot, llm):
    bot.files["doc-1"] = b"Episode notes.\n"
    bot.updates = [update(document={"file_id": "doc-1", "file_name": "notes.txt", "file_size": 15}, caption="file this in pods")]
    assert run(db, cfg) == 1
    held = db.rows("SELECT * FROM upload WHERE state = 'held'")
    assert len(held) == 1 and held[0]["filename"] == "notes.txt" and held[0]["email"] == "bot@x.io"
    uid = str(held[0]["id"]).split(":")[-1].strip("⟨⟩`")
    assert uploads.get(db, uid)["size"] == 15
    asked = [m["messages"][-1]["content"] for m in llm.seen if m.get("messages")][-1]
    assert "file this in pods" in asked and "Attached files, not in the archive yet" in asked and "notes.txt" in asked
    # one that can't be taken is said, and the question is still answered
    bot.files["bad"] = b"MZ"
    bot.updates = [update(document={"file_id": "bad", "file_name": "tool.exe", "file_size": 2})]
    assert run(db, cfg) == 1
    assert bot.sent[-1]["text"].startswith("Couldn't take tool.exe: ") and bot.sent[-1]["text"].endswith("OK")


def test_one_process_reads_the_bot_and_long_answers_are_split(db, cfg, bot, llm):
    bot.updates = [update("hi")]
    assert run(db, cfg, "api-1") == 1
    bot.updates = [update("hi again")]
    assert run(db, cfg, "worker-2") == 0  # api-1 holds it
    assert run(db, cfg, "api-1") == 1
    cfg["telegram"]["enabled"] = False
    run(db, cfg, "api-1")  # turned off: it lets go, and the offset stays
    assert channels.state(db, telegram.TELEGRAM).get("offset")
    cfg["telegram"]["enabled"] = True
    telegram.TELEGRAM.send(cfg, {"gateway": "42"}, "y" * 9000)
    assert [len(s["text"]) for s in bot.sent[-3:]] == [4000, 4000, 1000]
