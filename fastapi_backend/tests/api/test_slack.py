"""The assistant in Slack through an app of Lens's own over Socket Mode: made from a manifest in one click, set up in
the app with its two tokens, it acknowledges every envelope, answers direct messages, what's said to it in channels
and its slash command, as the account it was given, hears voice clips and holds files for the assistant to import."""

from __future__ import annotations

import json
import time
import urllib.parse

import pytest

from app.domain import channels, settings, slack, voice
from tests import fake_llm, fake_slack
from tests.api._assist import start_llm
from tests.helpers import login, make_user, seed

event, command = fake_slack.event, fake_slack.command


@pytest.fixture
def sl(monkeypatch):
    srv, url = fake_slack.start()
    sock = fake_slack.Socket()
    monkeypatch.setattr(slack, "API", url + "/api")
    monkeypatch.setattr(slack, "FILE_HOSTS", ("127.0.0.1",))
    monkeypatch.setattr(slack, "RESPONSE_HOSTS", ("127.0.0.1",))
    monkeypatch.setattr(slack, "_connect", lambda u: sock)
    fake_slack.Handler.url, fake_slack.Handler.socket = url, sock
    yield fake_slack.Handler
    for lst in slack._LISTENERS.values():
        lst.stop.set()
    slack._LISTENERS.clear()
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
def ws(app, db, cfg, folder, sl):
    """Seeded recordings, a viewer of pods to answer as, and the Slack app set up for them."""
    seed(db, cfg, folder)
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "bot@x.io", "bot password 1", roles={"pods": "viewer"})
    cfg["slack"] = {**cfg["slack"], "enabled": True, "bot_token": fake_slack.BOT, "app_token": fake_slack.APP, "account": "bot@x.io"}
    cfg["ai"]["tools"] = False
    return sl


def said(db, cfg, h, *envelopes, me="api-1"):
    """Hand the envelopes to Lens over the socket, and look until each is acknowledged and answered: how many were."""
    lst = slack._LISTENERS.get("socket")
    want = (lst.taken if lst else 0) + len(envelopes)
    for e in envelopes:
        h.socket.envelopes.put(e)
    n, until = 0, time.monotonic() + 10
    while time.monotonic() < until:
        n += channels.tick(db, cfg, cfg, me, slack.SLACK)
        lst = slack._LISTENERS.get("socket")
        if lst and lst.taken >= want and lst.todo.empty():
            return n
    raise AssertionError("Lens didn't take every envelope")


def acked(h):
    return [s["envelope_id"] for s in h.socket.sent]


def test_the_app_is_made_from_a_manifest_in_one_click(cfg):
    m = slack.manifest(cfg)
    assert m["settings"]["socket_mode_enabled"] is True  # no public address
    assert m["features"]["slash_commands"][0]["command"] == "/lens"
    assert {"chat:write", "commands", "im:history", "channels:history", "files:read"} <= set(m["oauth_config"]["scopes"]["bot"])
    url = urllib.parse.urlsplit(slack.create_url(cfg))
    assert url.netloc == "api.slack.com" and json.loads(urllib.parse.parse_qs(url.query)["manifest_json"][0]) == m
    cfg["slack"]["name"] = "Pod Bot"
    assert slack.command(cfg) == "/pod-bot" and slack.manifest(cfg)["features"]["bot_user"]["display_name"] == "Pod Bot"


def test_slack_is_set_up_and_checked_in_the_app(client, db, cfg, sl):
    make_user(db, "root@x.io", "root password 1", admin=True)
    a = login(client, "root@x.io", "root password 1")
    got = client.get("/api/v1/settings/slack", headers=a).json()
    assert got["state"] == "off" and got["command"] == "/lens" and got["create_url"].startswith("https://api.slack.com/apps?new_app=1")
    assert "paste both tokens" in client.post("/api/v1/settings/slack/test", headers=a).json()["error"]
    assert client.put("/api/v1/settings/slack", headers=a, json={"bot_token": "xapp-1"}).status_code == 400
    r = client.put("/api/v1/settings/slack", headers=a, json={"bot_token": " xoxb-test ", "app_token": "xapp-wrong", "enabled": True})
    assert r.status_code == 200, r.text
    saved = settings.Settings(db, cfg).current()["slack"]
    assert saved["bot_token"] == "xoxb-test" and saved["account"] == "root@x.io"  # who turned it on is who it answers as
    assert "xapp-wrong" not in client.get("/api/v1/settings", headers=a).text  # the tokens are secrets
    err = client.post("/api/v1/settings/slack/test", headers=a).json()["error"]
    assert "invalid_auth" in err and "apps.connections.open" in err
    client.put("/api/v1/settings/slack", headers=a, json={"app_token": fake_slack.APP})
    assert client.post("/api/v1/settings/slack/test", headers=a).json() == {"ok": True, "error": None}
    assert client.get("/api/v1/settings/slack", headers=a).json()["state"] == "starting"
    r = client.put("/api/v1/settings/slack", headers=a, json={"users": ["@alice", " ", "U2"], "rooms": ["slack/general = Pods"]})
    assert r.status_code == 200, r.text
    saved = settings.Settings(db, cfg).current()["slack"]
    assert saved["users"] == ["alice", "U2"] and saved["rooms"] == ["slack/general = pods"]
    for bad in ({"account": "bot"}, {"answer": "sometimes"}, {"approve": "always"}, {"poll_seconds": 2}, {"app_token": "xoxb-1"}):
        assert client.put("/api/v1/settings/slack", headers=a, json=bad).status_code == 400, bad


def test_matterbridge_using_the_same_app_is_caught(db, cfg, ws):
    cfg["matterbridge"].update(run=True, slack_token=fake_slack.BOT)
    assert "Matterbridge uses this Slack app too" in slack.SLACK.check(db, cfg)
    cfg["matterbridge"].update(run=False)
    assert slack.SLACK.check(db, cfg) is None


def test_it_answers_direct_messages_mentions_and_the_slash_command(db, cfg, ws, llm):
    n = said(
        db,
        cfg,
        ws,
        {"type": "hello"},
        event("what leaves on Friday?", channel="D1", channel_type="im"),  # a direct message: always for Lens
        event("lunch anyone?", user="U2"),
        event("<@UBOT> when does the shipment leave? <https://x.io|the plan>", user="U2", thread_ts="5.0"),
        event("Lens, hello", user="U3"),
        event("I said that", bot_id="B9"),  # another bot
        event("edited", subtype="message_changed"),
        command("who hosts <@U3|carol>'s show?", response_url=ws.url + "/commands/1"),
    )
    assert n == 4 and len(acked(ws)) == 7  # every envelope acknowledged, answered or not
    assert [p["channel"] for p in ws.posted] == ["D1", "C1", "C1"] and all(p["text"] == "OK" for p in ws.posted)
    assert ws.posted[1]["thread_ts"] == "5.0" and "thread_ts" not in ws.posted[0]  # a thread is answered in the thread
    assert ws.responses == [{"response_type": "in_channel", "text": "OK"}]  # the slash command, where it was typed
    asked = [m["messages"][-1]["content"] for m in llm.seen if m.get("messages")]
    assert any(c.endswith("Question: what leaves on Friday?") for c in asked)
    assert any(c.endswith("Question: when does the shipment leave? the plan (https://x.io)") for c in asked)
    assert any(c.endswith("Question: who hosts @carol's show?") for c in asked)
    titles = db.rows("SELECT VALUE title FROM chat WHERE kind = 'bridge' ORDER BY title")
    assert titles == ["Slack · alice", "Slack · general · bob", "Slack · general · carol"]
    s = slack.status(db, cfg)
    assert s["state"] == "running" and s["answered"] == 4 and (s["team"], s["bot"]) == ("Acme", "lens")


def test_the_slash_command_carries_the_chat_commands(sl):
    names = slack.Names(fake_slack.BOT)
    p = command("compact keep the dates")["payload"]
    assert slack.slash("UBOT", "T1", p, names)["text"] == "/compact keep the dates"  # bridge.py does /clear and /compact
    assert slack.slash("UBOT", "T1", {**p, "text": "Clear"}, names)["text"] == "/clear"
    assert slack.slash("UBOT", "T1", {**p, "text": ""}, names)["text"] == "Hello! What can you do?"


def test_chat_commands_through_the_slash_command(db, cfg, ws, llm):
    said(db, cfg, ws, event("what leaves on Friday?", channel="D1", channel_type="im"))
    asked = len(llm.seen)
    clear = command("clear", user="U1", user_name="alice", channel="D1", channel_name="directmessage", response_url=ws.url + "/commands/2")
    assert said(db, cfg, ws, clear) == 1
    assert len(ws.responses) == 1 and ws.responses[0]["text"] != "OK" and len(llm.seen) == asked  # done by Lens, no model


def test_who_may_talk_to_it_and_rooms_given_to_a_namespace(db, cfg, ws, llm):
    cfg["slack"]["users"] = ["alice"]
    assert said(db, cfg, ws, event("Lens, hi", user="U2")) == 0  # bob isn't let in
    cfg["slack"]["rooms"] = ["slack/general = pods"]
    assert said(db, cfg, ws, event("<@UBOT> when do episodes ship?", user="U1")) == 1
    assert db.one("SELECT VALUE scope FROM chat WHERE kind = 'bridge' LIMIT 1") == {"namespaces": ["pods"]}


def test_voice_clips_are_heard_and_files_held(db, cfg, ws, llm, monkeypatch):
    ws.files["clip.webm"] = b"webm..."
    ws.files["notes.txt"] = b"Episode notes.\n"
    heard = []
    monkeypatch.setattr(voice, "can_transcribe", lambda cfg: "sensevoice")
    monkeypatch.setattr(voice, "transcribe", lambda cfg, data: heard.append(data) or {"text": "what leaves on Friday?"})
    clip = {"name": "clip.webm", "mimetype": "audio/webm", "subtype": "slack_audio", "url_private": ws.url + "/files/clip.webm"}
    assert said(db, cfg, ws, event("", channel="D1", channel_type="im", subtype="file_share", files=[clip])) == 1
    assert heard == [b"webm..."] and ws.posted[-1]["text"] == "(Heard: “what leaves on Friday?”)\n\nOK"
    doc = {"name": "notes.txt", "mimetype": "text/plain", "size": 15, "url_private": ws.url + "/files/notes.txt"}
    assert said(db, cfg, ws, event("file this in pods", channel="D1", channel_type="im", subtype="file_share", files=[doc])) == 1
    held = db.rows("SELECT * FROM upload WHERE state = 'held'")
    assert len(held) == 1 and held[0]["filename"] == "notes.txt" and held[0]["email"] == "bot@x.io"
    with pytest.raises(slack.SlackError):  # the bot token goes only to Slack's file hosts
        slack.SLACK.download(cfg, "https://evil.example/notes.txt")


def test_one_process_listens_and_long_answers_are_split(db, cfg, ws, llm):
    assert said(db, cfg, ws, event("hi", channel="D1", channel_type="im"), me="api-1") == 1
    assert channels.tick(db, cfg, cfg, "worker-2", slack.SLACK) == 0  # api-1 holds it
    slack.SLACK.send(cfg, {"to": {"channel": "C1"}}, "y" * 9000)
    assert [len(p["text"]) for p in ws.posted[-3:]] == [3900, 3900, 1200]
    cfg["slack"]["bot_token"] = "xoxb-wrong"  # a new token: a new connection, and the error said
    with pytest.raises(slack.SlackError):
        channels.tick(db, cfg, cfg, "api-1", slack.SLACK)
    assert "invalid_auth" in slack.status(db, cfg)["error"]
