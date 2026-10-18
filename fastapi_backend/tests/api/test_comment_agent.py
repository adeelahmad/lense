"""The agent in a comment thread (docs/timeline.md, Asking the agent in a comment): a comment that tags @lens gets the
agent's answer on the thread, as the person who asked, with their permissions; what it proposes waits for their
approval, the exchange is a conversation of theirs, and the answer is a comment by Lens everyone on the thread reads."""

from __future__ import annotations

from app.domain import activity, chat, comment_agent, comments, store
from tests import fake_llm
from tests.api._assist import start_llm
from tests.api.test_setup_assistant import call
from tests.helpers import login, make_user, seed

URL = "/api/v1/activities"


def _env(client, db, cfg, folder, monkeypatch):
    ids = seed(db, cfg, folder)
    me = make_user(db, "me@x.io", "my password 1", roles={"pods": "editor"})
    other = make_user(db, "other@x.io", "other password 1", roles={"pods": "viewer"})
    answered = []
    # the agent answers on a thread of its own; here at once, so the test reads the reply
    monkeypatch.setattr(comment_agent, "later", lambda db_, cfg_, cid: answered.append(comment_agent.answer(db_, cfg_, cid)) or True)
    return {
        "ids": [f"create-recording-{i}" for i in ids],
        "rids": ids,
        "me": me,
        "other": other,
        "h": login(client, "me@x.io", "my password 1"),
        "ho": login(client, "other@x.io", "other password 1"),
        "answered": answered,
    }


def test_the_agent_answers_a_tagged_comment_in_the_thread(client, db, cfg, folder, monkeypatch):
    srv = start_llm(cfg)
    try:
        llm = fake_llm.Handler
        env = _env(client, db, cfg, folder, monkeypatch)
        pods1 = env["ids"][0]
        h = env["h"]
        llm.tool_script = [
            {"content": "", "tool_calls": [call(1, "search_transcripts", {"query": "trip"})]},
            {"content": "", "tool_calls": [call(2, "write_note", {"namespace": "pods", "title": "Trip", "body": "Friday"})]},
            {"content": "It's about the trip; I proposed a note."},
        ]
        # a plain comment wakes nothing; one that tags @lens gets an answer on its thread, by Lens
        client.post(f"{URL}/{pods1}/comments", json={"text": "Worth a listen"}, headers=h)
        assert env["answered"] == []
        c = client.post(f"{URL}/{pods1}/comments", json={"text": "@Lens what is this one about?"}, headers=h).json()
        assert len(env["answered"]) == 1
        got = client.get(f"{URL}/{pods1}/comments", headers=env["ho"]).json()
        reply = got[-1]
        assert reply["parent"] == c["id"] and reply["by_agent"] is True and reply["created_by_name"] == "Lens"
        assert reply["created_by"] is None and reply["mine"] is False and reply["can_delete"] is False
        assert reply["text"].startswith("It's about the trip; I proposed a note.")
        assert "Waiting for approval in Lens: Write the note “Trip” in pods" in reply["text"]
        # the person it answered may delete it; nobody edits it
        mine = client.get(f"{URL}/{pods1}/comments", headers=h).json()[-1]
        assert mine["can_delete"] is True and mine["mine"] is False
        assert client.patch(f"{URL}/{pods1}/comments/{reply['id']}", json={"text": "x"}, headers=h).status_code == 403
        # the exchange is a conversation of the person's, titled after the item, that the thread keeps
        root = comments.get(db, c["id"])
        conv = chat.get(db, root["chat"], env["me"])
        assert conv["title"].startswith("Timeline: ") and conv["scope"] == {"namespaces": ["pods"]}
        q, a = chat.history(db, root["chat"])
        assert q["content"] == "what is this one about?" and q["meta"] == {"via": "comment", "options": {"comment": c["id"]}}
        assert q["context"]["url"] == f"/resources/{env['rids'][0]}"
        assert [s["tool"] for s in a["steps"]] == ["search_transcripts", "write_note"]
        assert activity.totals(db, f"chat_message:{a['id']}")["calls"] == 3  # its model calls are in the ledger
        appr = db.one("SELECT tool, account, chat FROM approval")
        assert appr == {"tool": "write_note", "account": env["me"], "chat": root["chat"]}
        # the model was told where it was tagged, and read the thread so far and the item as data
        first = next(b for b in llm.seen if b.get("tools"))
        system, asked = first["messages"][0]["content"], first["messages"][-1]["content"]
        assert "tagged in a comment on the timeline in Lens by me;" in system
        assert asked.startswith("what is this one about?") and "The comments on it so far:\n- me: Worth a listen" in asked
        assert "What happened, as the automation saw it" in asked
        # the answer is a comment.added by Lens, and tagging again carries the same conversation on
        added = client.get(URL, params={"kinds": "comment.added"}, headers=h).json()["items"]
        assert added[0]["actor"]["id"] == "lens:assistant" and added[0]["object"]["inReplyTo"] == f"lens:comment/{c['id']}"
        llm.tool_script = [{"content": "Friday, it says."}]
        client.post(f"{URL}/{pods1}/comments", json={"text": "@lens when?", "parent": c["id"]}, headers=h)
        assert len(chat.history(db, root["chat"])) == 4
        assert [x["text"] for x in client.get(f"{URL}/{pods1}/comments", headers=h).json()][-2:] == ["@lens when?", "Friday, it says."]
        assert "- Lens: It's about the trip" in llm.seen[-1]["messages"][-1]["content"]
        # off in Settings: nothing answers; the agent never answers itself
        cfg["ai"]["answer_comments"] = False
        monkeypatch.undo()
        assert comment_agent.wake(db, cfg, c["id"], "@lens again") is False
        cfg["ai"]["answer_comments"] = True
        assert comment_agent.answer(db, cfg, reply["id"]) is None
    finally:
        fake_llm.Handler.tool_script = []
        srv.shutdown()


def test_without_a_model_or_within_a_used_up_budget_the_reply_says_so(client, db, cfg, folder, monkeypatch):
    env = _env(client, db, cfg, folder, monkeypatch)
    rid = env["rids"][0]
    h = env["h"]
    # on the asset's page too, with the asset as the conversation's page
    cfg["llm"]["model"] = None
    c = client.post(f"/api/v1/recordings/{rid}/comments", json={"text": "hey @lens, summarise?"}, headers=h).json()
    got = client.get(f"/api/v1/recordings/{rid}/comments", headers=h).json()
    assert got[-1]["by_agent"] and got[-1]["parent"] == c["id"] and "no model is set up" in got[-1]["text"]
    assert comment_agent.mentioned(cfg, "ask @lens.") and not comment_agent.mentioned(cfg, "lens@x.io or @lensing")
    cfg["bridge"]["name"] = "Jarvis"
    assert comment_agent.mentioned(cfg, "@jarvis?") and comment_agent.question(cfg, "@Jarvis  when is it? ") == "when is it?"
    assert comment_agent.question(cfg, "@lens") == "What do you make of this?"
    assert store.R("comment", c["id"])  # the thread stays the person's
