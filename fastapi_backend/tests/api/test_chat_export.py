"""Copy and export (docs/assistant.md): a conversation, or one message of it, as Markdown with its citations as
footnotes, as JSON (OpenAI chat messages in a tree, with Dublin Core terms) or as a PDF, over the API and MCP, holding
only the citations the person can still read."""

from __future__ import annotations

import json
import pathlib

import jsonschema
import pytest

from app.domain import anytopdf, auth, chat, chat_export, convert, store
from tests.api.test_mcp import tool, tool_error
from tests.api.test_oauth import _bearer, _grant
from tests.helpers import login, make_user, seed

SCHEMA = json.loads((pathlib.Path(__file__).parents[3] / "docs" / "schemas" / "chat-export.schema.json").read_text())
WEB = "http://localhost:3000"  # FRONTEND_URL: where links point when a request didn't come through the web app


def _passage(rid, n, text, t0, title, ns, **more):
    return {"n": n, "recording_id": rid, "title": title, "namespace": ns, "t0": t0, "time": store.tc(t0), "text": text, **more}


@pytest.fixture
def conv(client, db, cfg, folder):
    """A conversation with two versions of its first answer: one that used a tool and cites two namespaces' assets,
    and one regenerated beside it, which the conversation goes on from."""
    ids = seed(db, cfg, folder)
    uid = make_user(db, "ed@x.io", "editor password 1", roles={"pods": "viewer", "calls": "viewer"})
    make_user(db, "other@x.io", "other password 1", roles={"pods": "viewer"})
    cid = chat.create(db, uid, "Shipping questions", {"namespaces": ["pods", "calls"], "from": "2026-01-01"})
    q1 = chat.add(db, cid, "user", "When does the shipment leave?", parent=None, meta=chat.question_record("web"))
    a1 = chat.add(
        db,
        cid,
        "assistant",
        "It leaves on Friday [1], and the ship is late [2, 9]. See [the plan](https://example.org/plan) [9].",
        passages=[
            _passage(ids[0], 1, "Ana: The shipment leaves\non Friday.", 75_000, "Episode one", "pods", speaker="Ana", used=True),
            _passage(ids[2], 2, "The ship is late.", 5_000, "The call", "calls", used=True),
            _passage(ids[1], 3, "Nothing about ships.", 0, "Episode two", "pods"),
        ],
        steps=[{"tool": "search_transcripts", "args": {"query": "shipment"}, "summary": "3 moments"}],
        model="fake-small",
        parent=q1,
        meta={"model": "fake-small", "outcome": "answered", "tokens": {"in": 120, "out": 30}},
    )
    b1 = chat.add(db, cid, "assistant", "On Friday.", model="fake-large", parent=q1)
    q2 = chat.add(db, cid, "user", "Who ships it?", attachments=[{"id": "u1", "filename": "manifest.pdf", "size": 10}])
    a2 = chat.add(db, cid, "assistant", "", error="the model didn't answer")
    return {
        "cid": cid,
        "ids": ids,
        "uid": uid,
        "m": (q1, a1, b1, q2, a2),
        "h": login(client, "ed@x.io", "editor password 1"),
        "other": login(client, "other@x.io", "other password 1"),
    }


def export(client, conv, fmt, h=None, **params):
    return client.get(f"/api/v1/chats/{conv['cid']}/export.{fmt}", headers=h or conv["h"], params=params)


def test_markdown(client, db, conv):
    q1, a1, b1, q2, a2 = conv["m"]
    r = export(client, conv, "md")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert r.headers["content-disposition"] == 'attachment; filename="shipping-questions.md"'
    md = r.text
    assert md.startswith("# Shipping questions\n") and f"<{WEB}/chat/{conv['cid']}>" in md
    assert "Draws on namespaces pods, calls; from 2026-01-01" in md
    assert "the conversation has other versions" in md
    # the version showing: the regenerated answer, then the next question and its error
    assert "On Friday." in md and "It leaves on Friday" not in md and ", version 2 of 2" in md
    assert "## Lens, fake-large," in md and "Attached: manifest.pdf" in md and "> Error: the model didn't answer" in md

    # one answer, with its question: citations as footnotes linking back to the moment, tools as "Looked up"
    md = export(client, conv, "md", message=a1).text
    assert "When does the shipment leave?" in md and "On Friday." not in md and "Who ships it?" not in md
    assert f"It leaves on Friday [^{a1}-1], and the ship is late [^{a1}-2][9]. See [the plan](https://example.org/plan) [9]." in md
    assert f"[^{a1}-1]: Episode one, 1:15, Ana: “Ana: The shipment leaves on Friday.” <{WEB}/resources/{conv['ids'][0]}?t=75>" in md
    assert f"[^{a1}-2]: The call, 0:05: “The ship is late.” <{WEB}/resources/{conv['ids'][2]}?t=5>" in md
    assert "Episode two" not in md  # given to the model, not cited
    assert "Looked up:\n\n- `search_transcripts`: 3 moments" in md and "## Lens, fake-small," in md
    assert export(client, conv, "md", message=a1).headers["content-disposition"].endswith(f'shipping-questions-message-{a1}.md"')

    # citations follow the reader's access now: no longer in calls, its moment is left out
    auth.set_role(db, conv["uid"], store.ns_id(db, "calls"), None)
    md = export(client, conv, "md", message=a1).text
    assert f"[^{a1}-1]" in md and "late [2, 9]." in md and f"[^{a1}-2]" not in md and "The ship is late." not in md


def test_json(client, conv):
    q1, a1, b1, q2, a2 = conv["m"]
    r = export(client, conv, "json", all="true")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    doc = r.json()
    jsonschema.validate(doc, SCHEMA)
    assert doc["@context"] == {"dc": "http://purl.org/dc/terms/"} and doc["format"] == "lens.chat-export/1"
    assert doc["dc:title"] == "Shipping questions" and doc["dc:identifier"] == f"{WEB}/chat/{conv['cid']}"
    assert doc["dc:creator"] == "ed" and doc["dc:format"] == "application/json"
    assert doc["current_node"] == str(a2) and set(doc["mapping"]) == {str(i) for i in conv["m"]}
    root = doc["mapping"][str(q1)]
    assert root["parent"] is None and root["children"] == [str(a1), str(b1)]
    assert root["message"] == {"role": "user", "content": "When does the shipment leave?"} and root["lens"]["meta"]["via"] == "web"
    first = doc["mapping"][str(a1)]
    assert first["message"]["role"] == "assistant" and first["lens"]["versions"] == {"index": 1, "count": 2, "ids": [a1, b1]}
    call = first["tool_messages"][0]["tool_calls"][0]
    assert call["function"] == {"name": "search_transcripts", "arguments": '{"query": "shipment"}'}
    assert first["tool_messages"][1] == {"role": "tool", "tool_call_id": call["id"], "content": "3 moments"}
    cites = first["lens"]["citations"]
    assert [c["n"] for c in cites] == [1, 2, 3] and cites[0]["source"] == "said"
    assert cites[0]["url"] == f"{WEB}/resources/{conv['ids'][0]}?t=75" and cites[0]["speaker"] == "Ana"
    assert first["lens"]["meta"]["tokens"] == {"in": 120, "out": 30}
    assert doc["mapping"][str(q2)]["lens"]["attachments"] == [{"filename": "manifest.pdf", "size": 10}]
    assert doc["mapping"][str(a2)]["lens"]["error"] == "the model didn't answer"

    # without `all`: the version showing alone, its parents and children among those
    doc = export(client, conv, "json").json()
    jsonschema.validate(doc, SCHEMA)
    assert list(doc["mapping"]) == [str(i) for i in (q1, b1, q2, a2)]
    assert doc["mapping"][str(q1)]["children"] == [str(b1)] and doc["mapping"][str(b1)]["parent"] == str(q1)

    # as messages: one line, the shape fine-tuning reads, tool calls before the answer that made them
    r = export(client, conv, "json", format="messages", message=a1)
    assert r.text.count("\n") == 1 and r.text.endswith("\n")
    msgs = r.json()
    jsonschema.validate(msgs, SCHEMA)
    assert [m["role"] for m in msgs["messages"]] == ["user", "assistant", "tool", "assistant"]
    assert msgs["messages"][1]["content"] is None and msgs["messages"][-1]["content"].startswith("It leaves on Friday [1]")


def test_what_isnt_an_export(client, conv):
    q1, a1, *_ = conv["m"]
    assert export(client, conv, "md", all="true").status_code == 400  # only JSON holds every version
    assert export(client, conv, "json", all="true", message=a1).status_code == 400
    assert export(client, conv, "md", format="messages").status_code == 400
    assert export(client, conv, "json", message=999_999).status_code == 404
    assert export(client, conv, "docx").status_code in (404, 422)
    assert export(client, conv, "md", h=conv["other"]).status_code == 404  # conversations are their owner's alone


def test_pdf_is_printed_by_what_the_server_has(client, conv, monkeypatch):
    printed = []
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)
    monkeypatch.setattr(anytopdf, "available", lambda cfg: None)
    monkeypatch.setattr(anytopdf, "archive", lambda: None)

    # nothing can print it here: 409 with the reason, so the web app prints the page instead
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    r = export(client, conv, "pdf")
    assert r.status_code == 409 and "print the conversation from the web app" in r.json()["detail"]
    assert chat_export.printer({"documents": {}}) is None

    # Chromium where it's installed: the Markdown on a page
    def html_pdf(cfg, page, out):
        printed.append(("chromium", page))
        out.write_bytes(b"%PDF-1.7 chromium")
        return "chromium"

    monkeypatch.setattr(convert, "chromium", lambda cfg: "/usr/bin/chromium")
    monkeypatch.setattr(convert, "html_pdf", html_pdf)
    r = export(client, conv, "pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf" and r.content == b"%PDF-1.7 chromium"
    assert r.headers["content-disposition"] == 'attachment; filename="shipping-questions.pdf"'
    assert "<title>Shipping questions</title>" in printed[-1][1] and "On Friday." in printed[-1][1]

    # else anytopdf, given the page as HTML
    def to_pdf(cfg, src, out, scan=False, render=False):
        printed.append(("anytopdf", pathlib.Path(src).suffix))
        pathlib.Path(out).write_bytes(b"%PDF-1.7 anytopdf")
        return "anytopdf"

    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    monkeypatch.setattr(anytopdf, "available", lambda cfg: "here")
    monkeypatch.setattr(anytopdf, "to_pdf", to_pdf)
    r = export(client, conv, "pdf")
    assert r.status_code == 200 and r.content == b"%PDF-1.7 anytopdf" and printed[-1] == ("anytopdf", ".html")


def test_web_citations():
    """A web page an answer cites (docs/assistant.md, Web search) is a footnote with its address, and the same in JSON."""
    c = {"id": 4, "title": "Ships", "scope": {}, "created_at": "2026-10-16T22:43:00+00:00"}
    page = {
        "n": 1,
        "source": "web",
        "url": "https://example.org/ships",
        "title": "Ships at sea",
        "site": "example.org",
        "fetched_at": "2026-10-16T10:00:00+00:00",
        "text": "Most ships are slow.",
    }
    msgs = [
        {"id": 1, "role": "user", "content": "Are ships slow?"},
        {"id": 2, "role": "assistant", "content": "Yes [1].", "parent": 1, "passages": [page]},
    ]
    md = chat_export.markdown(c, msgs, WEB)
    assert "Draws on everything you can read" in md
    assert "[^2-1]: [Ships at sea](https://example.org/ships), example.org, read 2026-10-16: “Most ships are slow.”" in md
    doc = chat_export.document(c, msgs, WEB)
    jsonschema.validate(doc, SCHEMA)
    assert doc["mapping"]["2"]["lens"]["citations"] == [{"source": "web", **{k: v for k, v in page.items() if k != "source"}}]
    assert "dc:creator" not in doc and doc["current_node"] == "2"


def test_web_pages_stay_in_exports(client, db, conv):
    """A cited web page isn't the archive's, so it stays whatever the reader can read now."""
    page = {"n": 1, "source": "web", "url": "https://example.org/ships", "title": "Ships at sea", "site": "example.org", "text": "Slow."}
    q3 = chat.add(db, conv["cid"], "user", "Are ships slow?")
    a3 = chat.add(db, conv["cid"], "assistant", "Yes [1].", passages=[page], parent=q3)
    auth.set_role(db, conv["uid"], store.ns_id(db, "calls"), None)
    md = export(client, conv, "md", message=a3).text
    assert f"Yes [^{a3}-1]." in md and f"[^{a3}-1]: [Ships at sea](https://example.org/ships), example.org: “Slow.”" in md
    doc = export(client, conv, "json").json()
    jsonschema.validate(doc, SCHEMA)
    assert doc["mapping"][str(a3)]["lens"]["citations"][0]["url"] == "https://example.org/ships"


def test_agents_read_and_export_over_mcp(client, conv):
    q1, a1, b1, q2, a2 = conv["m"]
    _, tokens = _grant(client, conv["h"], scope="read")
    h = _bearer(tokens)
    got = tool(client, h, "list_chats")
    assert got["chats"] == [
        {
            "chat_id": conv["cid"],
            "title": "Shipping questions",
            "updated_at": got["chats"][0]["updated_at"],
            "url": f"{WEB}/chat/{conv['cid']}",
        }
    ]
    assert tool(client, h, "list_chats", words="nothing like it")["chats"] == []

    out = tool(client, h, "read_chat", chat_id=conv["cid"])
    assert [m["id"] for m in out["messages"]] == [q1, b1, q2, a2] and "tree" not in out
    assert out["draws_on"] == "namespaces pods, calls; from 2026-01-01" and out["messages"][1]["versions"]["index"] == 2
    assert out["messages"][2]["files"] == ["manifest.pdf"] and out["messages"][3]["error"] == "the model didn't answer"
    other = tool(client, h, "read_chat", chat_id=conv["cid"], message=a1, tree=True)
    first = other["messages"][1]
    assert first["id"] == a1 and first["tools"] == [{"tool": "search_transcripts", "args": {"query": "shipment"}, "found": "3 moments"}]
    assert first["citations"][0]["url"] == f"{WEB}/resources/{conv['ids'][0]}?t=75" and first["record"]["outcome"] == "answered"
    assert [n["id"] for n in other["tree"]] == [q1, a1, b1, q2, a2]
    assert "isn't in this conversation" in tool_error(client, h, "read_chat", chat_id=conv["cid"], message=999_999)

    md = tool(client, h, "export_chat", chat_id=conv["cid"])
    assert md["file_name"] == "shipping-questions.md" and md["markdown"].startswith("# Shipping questions")
    doc = tool(client, h, "export_chat", chat_id=conv["cid"], format="json", all_versions=True)["document"]
    jsonschema.validate(doc, SCHEMA)
    assert len(doc["mapping"]) == 5
    lines = tool(client, h, "export_chat", chat_id=conv["cid"], format="json", as_messages=True, message=a1)["document"]
    assert [m["role"] for m in lines["messages"]] == ["user", "assistant", "tool", "assistant"]

    # another person's conversation isn't there to read
    _, theirs = _grant(client, conv["other"], scope="read")
    assert tool(client, _bearer(theirs), "list_chats")["chats"] == []
    assert "not found" in tool_error(client, _bearer(theirs), "read_chat", chat_id=conv["cid"])
    assert "not found" in tool_error(client, _bearer(theirs), "export_chat", chat_id=conv["cid"])
