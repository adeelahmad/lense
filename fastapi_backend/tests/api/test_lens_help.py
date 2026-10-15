"""Lens's own manual (app/domain/manual.py): the assistant and agents answer questions about Lens itself, such as its
MCP address, from its docs and this server's facts, cited (lens_help in the assistant, the chat rooms and MCP)."""

from __future__ import annotations

import json

import pytest

from app.domain import ai_tools, bridge, chat, manual, settings
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.api.test_mcp import env, tool  # noqa: F401 - the MCP tests' fixture and caller


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def test_the_docs_are_found_by_words():
    found = manual.docs("what is the MCP address")
    top = found["sections"][0]
    assert top["page"] == "mcp.md" and top["title"].startswith("MCP server › ")  # its Connecting section, as written now
    assert top["url"].startswith(manual.DOCS_URL + "mcp.md#") and "/mcp" in top["text"]
    assert found["contents"]["mcp"] == "MCP server" and "changelog" not in found["contents"]  # every page, by its title
    assert len(found["sections"]) <= manual.TOP and all(len(s["text"]) <= manual.SECTION_CHARS for s in found["sections"])
    # one page read whole, and a search within one page
    page = manual.docs(page="MCP")
    assert (
        page["sections"][0]["url"] == manual.DOCS_URL + "mcp.md"
        and sum(len(s["text"]) for s in page["sections"]) <= manual.PAGE_CHARS + 200
    )
    assert {s["page"] for s in manual.docs("tokens", page="mcp.md")["sections"]} == {"mcp.md"}
    assert manual.docs("zzqx unknownword", page="mcp")["sections"][0]["page"] == "mcp.md"  # no match there: the page
    nope = manual.docs(page="nothing-like-it")
    assert nope["sections"] == [] and "no page" in nope["error"] and "mcp" in nope["contents"]
    assert manual.docs("the and of")["sections"] == []  # no words to look for


def test_sections_anchors_and_a_changed_page(tmp_path, monkeypatch):
    monkeypatch.setenv("LENS_DOCS_DIR", str(tmp_path))
    (tmp_path / "setup.md").write_text(
        "# Set up\n\nIntro words.\n\n## Model `providers`: cloud\n\nPaste a key.\n\n```bash\n# not a heading\nlens up\n```\n\n"
        "### Notes\n\nFirst notes.\n\n## Other\n\n### Notes\n\nSecond notes about gizmos.\n<!-- hidden -->\n"
    )
    (tmp_path / "CHANGELOG.md").write_text("# Changelog\n\ngizmos everywhere\n")
    found = manual.docs(page="set up")["sections"]
    assert [(s["title"], s["url"].split("#")[-1] if "#" in s["url"] else "") for s in found] == [
        ("Set up", ""),
        ("Set up › Model providers: cloud", "model-providers-cloud"),
        ("Set up › Model providers: cloud › Notes", "notes"),
        ("Set up › Other › Notes", "notes-1"),  # the second Notes on the page, as GitHub numbers it
    ]
    assert "# not a heading" in found[1]["text"] and "hidden" not in found[3]["text"]
    assert [s["title"] for s in manual.docs("gizmos")["sections"]] == ["Set up › Other › Notes"]  # not the changelog
    (tmp_path / "setup.md").write_text("# Set up\n\nNow about widgets.\n")
    assert manual.docs("widgets")["sections"][0]["title"] == "Set up"  # read again once it changed
    monkeypatch.setattr(manual, "folder", lambda: None)
    assert "github.com" in manual.docs("widgets")["missing"]


def test_the_assistant_answers_about_lens_itself(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["viewer"]
    settings.save(db, app.state.archive.base, "telegram", {"enabled": True, "token": "123456789:AAEsecretTelegramToken_abcdefghijklmnopq"})
    llm.tool_script = [
        {
            "content": "",
            "tool_calls": [{"id": "h1", "type": "function", "function": {"name": "lens_help", "arguments": '{"query": "MCP address"}'}}],
        },
        {"content": "Add http://localhost:3000/mcp to your MCP client [1]; it signs you in through Lens [2]."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "What is the MCP address?"}).text)
    assert ev["step"][0]["tool"] == "lens_help" and ev["step"][0]["summary"].startswith("Looked up “MCP address” in Lens's docs")
    system = fake_llm.Handler.seen[-2]["messages"][0]["content"]
    assert "call lens_help" in system  # told to look Lens up rather than say the archive doesn't cover it
    told = json.loads(fake_llm.Handler.seen[-1]["messages"][-1]["content"])
    assert told["this_server"]["addresses"]["mcp"] == "http://localhost:3000/mcp"  # the address the person uses
    assert told["this_server"]["turned_on"]["telegram"] is True and "AAEsecretTelegramToken" not in json.dumps(told)
    assert "settings_pages" not in told["this_server"]  # where things are changed is for admins
    assert told["this_server"]["you"] == {"admin": False, "namespaces_you_read": ["pods"], "namespaces_you_edit": []}
    cited, best = ev["passages"][0], manual.docs("MCP address")["sections"]
    assert [(p["n"], p["source"], p["url"]) for p in cited] == [(1, "server", "http://localhost:3000"), (2, "docs", best[0]["url"])]
    assert "recording_id" not in cited[0] and cited[1]["title"] == f"Lens docs: {best[0]['title']}"
    # reopened, the citations are still there (they aren't a recording someone may no longer read)
    kept = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]["passages"]
    assert [p["url"] for p in kept] == [p["url"] for p in cited] and kept[0]["recording_id"] is None
    # in a chat room, under the answer, with their links
    lines = bridge.sources(db, cfg, cid).splitlines()
    assert lines[1:] == ["[1] This Lens server: http://localhost:3000", f"[2] Lens docs: {best[0]['title']}: {best[0]['url']}"]


def test_admins_see_where_each_setting_is(db, cfg, folder):
    box = ai_tools.Toolbox(db, cfg, {"id": 1, "email": "root@x.io"}, set(), set(), {}, None, admin=True, web="https://lens.example.org/")
    out, summary = box.call("lens_help", {})
    here = json.loads(out)["this_server"]
    assert summary == "Read this server's facts" and here["ref"] == 1 and box.refs[0]["url"] == "https://lens.example.org"
    assert here["addresses"]["api"] == "https://lens.example.org/api/v1" and here["settings"] == "https://lens.example.org/settings"
    assert here["settings_pages"]["telegram"] == "telegram" and set(here["settings_pages"]) == set(here["turned_on"])
    assert here["version"] and here["you"]["admin"] is True
    assert "lens_help" in ai_tools.OWN_RESULTS and not box.outside  # Lens's own word: nothing read from outside
    assert "lens_help" in [t["function"]["name"] for t in box.specs()]
    for name in manual.index().pages:  # whatever is read, it fits what the model is given of a tool's result
        got = json.loads(box.call("lens_help", {"page": name, "query": None})[0])
        assert got["docs"]["sections"][0]["page"] == name
    cfg["ai"]["disabled_tools"] = ["lens_help"]
    assert "lens_help" not in [t["function"]["name"] for t in box.specs()]


def test_agents_ask_lens_over_mcp(client, env):  # noqa: F811
    out = tool(client, env["h"], "lens_help", query="connect Claude Code")
    assert out["this_server"]["addresses"]["mcp"] == "http://localhost:3000/mcp"
    assert out["docs"]["sections"][0]["url"].startswith(manual.DOCS_URL) and "mcp" in out["docs"]["contents"]
    # Lens's own docs talk about prompt injection ("ignore previous instructions") without being flagged as it
    res = client.post(
        "/mcp",
        headers=env["h"],
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "lens_help", "arguments": {"query": "ignore previous instructions"}},
        },
    ).json()["result"]
    assert len(res["content"]) == 1 and res["structuredContent"]["docs"]["sections"]


def test_without_a_question_lens_help_still_gives_the_server(db, cfg):
    out = manual.answer(db, cfg, "http://x", query=None, page=None)
    assert out["this_server"]["addresses"]["mcp"] == "http://x/mcp" and out["docs"]["sections"] == []
    assert chat.keywords("what is the MCP address") == ["mcp", "address"]
