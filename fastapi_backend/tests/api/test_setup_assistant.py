"""The assistant running the server: a setup conversation that makes the changes itself, the same tools asking for
approval elsewhere, and files dropped into a conversation that it imports."""

from __future__ import annotations

import json

import pytest

from app.domain import rss, settings, setup, store
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_news_feed_source import site  # noqa: F401 - a news feed served from this process
from tests.helpers import write_wav


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def held(c, h, data, name="talk.wav"):
    """A file uploaded to attach to a message: held, not in the archive."""
    r = c.post("/api/v1/uploads", headers=h, json={"filename": name, "size": len(data), "hold": True})
    assert r.status_code == 201, r.text
    up = r.json()
    r = c.put(f"/api/v1/uploads/{up['id']}?offset=0", headers={**h, "Content-Type": "application/octet-stream"}, content=data)
    assert r.status_code == 200, r.text
    return r.json()


def test_a_setup_conversation_sets_the_server_up(app, db, cfg, folder, new_client, llm, monkeypatch):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    monkeypatch.setattr(
        setup, "detect_llm", lambda: [{"kind": "Ollama", "base_url": "http://x:11434/v1", "models": ["m"], "suggested": "m"}]
    )
    wav = folder / "memo.wav"
    write_wav(wav, seconds=1.5)
    up = held(c, h, wav.read_bytes(), "memo.wav")
    assert (up["state"], up["recording"], up["namespace"]) == ("held", None, "")
    recordings = len(db.values("SELECT VALUE id FROM recording"))

    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "server_status", {}), call(2, "find_model_servers", {})]},
        {"content": "", "tool_calls": [call(3, "change_settings", {"section": "embeddings", "changes": {"model": "nomic-embed-text"}})]},
        {"content": "", "tool_calls": [call(4, "create_namespace", {"name": "family", "graph": "isolated"})]},
        {"content": "", "tool_calls": [call(5, "import_files", {"upload_ids": [up["id"]], "namespace": "family"})]},
        {"content": "", "tool_calls": [call(6, "change_settings", {"section": "telemetry", "changes": {"enabled": False}})]},
        {"content": "Set up search by meaning, made the family namespace and put your memo in it."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    convo = c.get(f"/api/v1/chats/{cid}", headers=h).json()
    assert (convo["kind"], convo["title"]) == ("setup", "Set up Lens")
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "", "attachments": [up["id"]]}).text)
    assert [x["tool"] for x in ev["step"]] == [
        "server_status",
        "find_model_servers",
        "change_settings",
        "create_namespace",
        "import_files",
        "change_settings",
    ]
    status = json.loads(llm.seen[-1]["messages"][3]["content"])  # what server_status told the model
    assert status["namespaces"] == ["calls", "pods"] and "missing" in status
    found = json.loads(llm.seen[-1]["messages"][4]["content"])  # and find_model_servers: nearby and cloud providers
    assert found["servers"][0]["kind"] == "Ollama" and found["cloud"][0]["base_url"] == "https://api.openai.com/v1"
    asked = llm.seen[-6]["messages"]
    assert "setting up this Lens server" in asked[0]["content"]
    assert f"attachment id {up['id']}" in asked[-1]["content"]  # the model hears about the file
    # made at once, without asking...
    assert settings.effective(db, cfg)["embeddings"]["model"] == "nomic-embed-text"
    assert "family" in store.space_names(db).values()
    assert len(db.values("SELECT VALUE id FROM recording")) == recordings + 1
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["state"] == "done"
    # ...except telemetry, which sends data elsewhere
    assert [a["summary"] for a in ev["approval"]] == ["Set telemetry: enabled = False"]
    msgs = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"]
    assert msgs[0]["attachments"][0]["filename"] == "memo.wav" and msgs[0]["content"] == "I attached this file."
    audit = db.values("SELECT VALUE action FROM audit_log WHERE detail CONTAINS 'assistant'")
    assert {"settings.save", "namespace.create"} <= set(audit)


def test_elsewhere_the_server_tools_ask_first(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "change_settings", {"section": "embeddings", "changes": {"model": "bge-m3"}})]},
        {"content": "I asked to switch the embedding model."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "use bge-m3 for search"}).text)
    appr = ev["approval"][0]
    assert settings.effective(db, cfg)["embeddings"]["model"] != "bge-m3"
    assert c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()["status"] == "done"
    assert settings.effective(db, cfg)["embeddings"]["model"] == "bge-m3"


def test_only_admins_get_the_server_tools(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    ce, he = s.cl["editor"]
    assert ce.post("/api/v1/chats", headers=he, json={"kind": "setup"}).status_code == 403
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "change_settings", {"section": "llm", "changes": {"model": "x"}})]},
        {"content": "Done."},
    ]
    wav = folder / "e.wav"
    write_wav(wav, seconds=1)
    up = held(ce, he, wav.read_bytes(), "e.wav")
    cid = ce.post("/api/v1/chats", headers=he, json={}).json()["id"]
    ev = sse(ce.post(f"/api/v1/chats/{cid}/messages", headers=he, json={"content": "hi", "attachments": [up["id"]]}).text)
    offered = {t["function"]["name"] for t in llm.seen[-2]["tools"]}
    assert "import_files" in offered and not offered & {"change_settings", "server_status", "create_namespace"}
    assert ev["step"][0]["summary"] == "unknown tool change_settings"
    # someone else's file can't be attached
    ca, ha = s.cl["admin"]
    other = ca.post("/api/v1/chats", headers=ha, json={}).json()["id"]
    assert ca.post(f"/api/v1/chats/{other}/messages", headers=ha, json={"content": "x", "attachments": [up["id"]]}).status_code == 404


def test_a_held_upload_stays_out_until_placed(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    wav = folder / "h.wav"
    write_wav(wav, seconds=1)
    data = wav.read_bytes()
    assert (
        c.post("/api/v1/uploads", headers=h, json={"filename": "h.wav", "size": len(data), "hold": True, "pipeline": 1}).status_code == 400
    )
    up = held(c, h, data, "h.wav")
    assert c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()["state"] == "held"
    from app.domain import uploads

    with pytest.raises(KeyError):  # only its uploader places it
        uploads.place(db, cfg, up["id"], "pods", 999)
    with pytest.raises(KeyError):  # not one of the namespace's collections
        uploads.place(
            db, cfg, up["id"], "pods", db.values("SELECT VALUE record::id(id) FROM account WHERE email = 'ed@x.io'")[0], collection=12345
        )


def test_the_assistant_manages_people_with_approval(app, db, cfg, folder, new_client, llm):
    from app.domain import auth

    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "list_people", {})]},
        {"content": "", "tool_calls": [call(2, "invite_person", {"email": "Sam@x.io", "namespace": "pods", "role": "editor"})]},
        {"content": "I asked to invite Sam."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]  # even in setup, people wait for approval
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "invite sam@x.io to pods as an editor"}).text)
    said = llm.seen[-2]["messages"][-1]["content"]  # wrapped as text from a tool
    listed = json.loads(said[said.index("{") : said.rindex("}") + 1])["people"]
    assert any(p["admin"] for p in listed) and all("roles" in p and "signed_in_yet" in p for p in listed)
    appr = ev["approval"][0]
    assert appr["summary"] == "Invite sam@x.io as editor of pods" and not auth.find_account(db, "sam@x.io")
    r = c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()
    assert r["status"] == "done" and r["invited"] == "sam@x.io" and r["emailed"] is False
    sam = auth.find_account(db, "sam@x.io")
    assert auth.roles(db, sam) == {store.ns_id(db, "pods"): "editor"}

    llm.tool_script = [
        {
            "content": "",
            "tool_calls": [call(3, "change_person", {"email": "sam@x.io", "namespace": "pods", "role": "none", "disabled": True})],
        },
        {"content": "Asked."},
    ]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "take sam out"}).text)
    appr = ev["approval"][0]
    assert appr["summary"] == "sam@x.io: no role in pods, disable the account"
    assert c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()["status"] == "done"
    assert auth.roles(db, sam) == {} and auth.get_account(db, sam["id"])["disabled"]


def test_the_assistant_captures_web_pages(app, db, cfg, folder, new_client, llm, monkeypatch):
    from app.domain import convert

    monkeypatch.setattr(convert, "chromium", lambda cfg: "/usr/bin/chromium")  # nothing is captured: no job runs here
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    listed = held(c, h, b"# reading\nhttps://8.8.8.8/tides Tide tables\nhttps://1.1.1.1/lamps\n", "links.txt")
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_urls", {"urls": ["https://8.8.8.8/harbour"], "namespace": "pods"})]},
        {"content": "", "tool_calls": [call(2, "import_urls", {"upload_id": listed["id"], "namespace": "pods"})]},
        {"content": "I asked to capture them."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "keep these", "attachments": [listed["id"]]}).text)
    offered = {t["function"]["name"] for t in llm.seen[-3]["tools"]}
    assert "import_urls" in offered
    appr = ev["approval"]
    assert [a["summary"] for a in appr] == ["Capture https://8.8.8.8/harbour into pods", "Capture 2 web pages into pods"]
    assert not db.values("SELECT VALUE id FROM recording WHERE web != NONE")  # nothing until approved
    for a in appr:
        r = c.post(f"/api/v1/approvals/{a['id']}", headers=h, json={"decision": "approve"})
        assert r.json()["status"] == "done", r.text
    got = db.rows("SELECT id, title, web, space FROM recording WHERE web != NONE ORDER BY id")
    assert [(x["web"]["url"], x["space"]) for x in got] == [
        ("https://8.8.8.8/harbour", s.pods),
        ("https://8.8.8.8/tides", s.pods),
        ("https://1.1.1.1/lamps", s.pods),
    ]
    assert got[1]["title"] == "Tide tables"
    assert c.get(f"/api/v1/uploads/{listed['id']}", headers=h).json()["state"] == "held"  # the list itself isn't imported
    # a viewer can't add pages to the namespace
    cv, hv = s.cl["viewer"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_urls", {"urls": ["https://8.8.8.8/x"], "namespace": "pods"})]},
        {"content": "No."},
    ]
    vid = cv.post("/api/v1/chats", headers=hv, json={}).json()["id"]
    ev = sse(cv.post(f"/api/v1/chats/{vid}/messages", headers=hv, json={"content": "keep https://8.8.8.8/x"}).text)
    assert "approval" not in ev


def test_the_assistant_follows_news_feeds(app, db, cfg, folder, new_client, llm, site):  # noqa: F811 - the fixture
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "subscribe_feed", {"url": "ftp://x/feed.xml", "namespace": "news"})]},
        {"content": "", "tool_calls": [call(2, "subscribe_feed", {"url": f"{site}/blog", "namespace": "news"})]},
        {"content": "", "tool_calls": [call(3, "news_feeds", {})]},
        {"content": "Following Harbour news into news."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "follow the harbour blog in news"}).text)
    assert "https://" in ev["step"][0]["summary"] and "approval" not in ev
    assert ev["step"][1]["summary"] == f"Follow the news feed {site}/blog into news: done"
    done = json.loads(llm.seen[-2]["messages"][-1]["content"])
    assert (done["status"], done["posts"], done["added"], "title" in done) == ("done", 2, True, False)  # not the feed's words
    (feed,) = rss.subscriptions(db)
    assert feed["name"] == "Harbour news" and [w["namespace"] for w in feed["watching"]] == ["news"]
    audit = db.values("SELECT VALUE action FROM audit_log WHERE detail CONTAINS 'assistant'")
    assert {"source.create", "watch.create"} <= set(audit)

    # elsewhere, stopping waits for approval
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "unsubscribe_feed", {"feed": feed["feed"]})]},
        {"content": "I asked to stop following it."},
    ]
    other = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{other}/messages", headers=h, json={"content": "stop the harbour feed"}).text)
    appr = ev["approval"][0]
    assert appr["summary"] == "Stop following the news feed Harbour news" and rss.subscriptions(db)
    assert c.post(f"/api/v1/approvals/{appr['id']}", headers=h, json={"decision": "approve"}).json()["status"] == "done"
    assert rss.subscriptions(db) == []


def test_the_assistant_imports_files_as_the_content_type_said(app, db, cfg, folder, new_client, llm):
    """'These are interviews': the assistant imports attached files as that content type, and one that doesn't fit
    them is refused before anything is placed."""
    from app.domain import content_types

    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["admin"]
    wav = folder / "chat with ann.wav"
    write_wav(wav, seconds=1.5)
    up = held(c, h, wav.read_bytes(), "chat with ann.wav")
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "import_files", {"upload_ids": [up["id"]], "namespace": "pods", "content_type": "scan"})]},
        {
            "content": "",
            "tool_calls": [call(2, "import_files", {"upload_ids": [up["id"]], "namespace": "pods", "content_type": "interview"})],
        },
        {"content": "Imported it as an interview."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={"kind": "setup"}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "an interview", "attachments": [up["id"]]}).text)
    refused = llm.seen[-2]["messages"][-1]["content"]
    assert "this is audio; Scanned page is a image type" in refused and "interview" in refused
    done = c.get(f"/api/v1/uploads/{up['id']}", headers=h).json()
    assert (done["state"], done["content_type"]) == ("done", "interview")
    assert content_types.of_recording(db, done["recording"]) == (content_types.get(db, "interview"), True)
