"""Marketplaces: plugins and skills in Anthropic's format, from a marketplace an admin added, a link or a .zip,
installed as extensions and used in conversations."""

from __future__ import annotations

import base64
import io
import json
import tarfile
import zipfile

import pytest

from app.domain import marketplace
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm

PDF = """---
name: pdf
description: Use this skill whenever the user wants to do anything with PDF files.
license: Proprietary
---

# PDF Processing Guide

For forms, read FORMS.md and follow it. Keep {{this}} as written.
"""

XLSX = """---
name: xlsx
description: Spreadsheets of every kind.
---

Open the workbook first.
"""

SKILLS_REPO = {
    ".claude-plugin/marketplace.json": json.dumps(
        {
            "name": "anthropic-agent-skills",
            "metadata": {"description": "Anthropic example skills"},
            "plugins": [
                {"name": "document-skills", "description": "Documents", "source": "./", "strict": False, "skills": ["./skills/pdf"]},
                {"name": "all-skills", "description": "Every one", "source": "./", "strict": False, "skills": ["./skills"]},
                {
                    "name": "acme-tools",
                    "description": "Acme's tools",
                    "source": {"source": "github", "repo": "acme/tools"},
                    "version": "1.2.0",
                },
                {"name": "from-npm", "source": {"source": "npm", "package": "x"}},
            ],
        }
    ),
    "skills/pdf/SKILL.md": PDF,
    "skills/pdf/FORMS.md": "Fill the form field by field.",
    "skills/pdf/font.ttf": b"\x00\x01binary",
    "skills/xlsx/SKILL.md": XLSX,
    "README.md": "skills",
}

TOOLS_REPO = {
    ".claude-plugin/plugin.json": json.dumps({"name": "acme-tools", "description": "Acme's review kit", "version": "1.2.0"}),
    "commands/review.md": "---\ndescription: Review a change\n---\n\nReview $ARGUMENTS for bugs.\n",
    "agents/critic.md": "---\nname: critic\ndescription: A harsh critic of drafts\ntools: Read, Grep\n---\n\nYou are a critic. Be blunt.\n",
    "skills/notes/SKILL.md": "---\nname: meeting-notes\ndescription: Writing up meeting notes\n---\n\nUse three headings.\n",
    "hooks/hooks.json": json.dumps({"hooks": {"PreToolUse": []}}),
    ".mcp.json": json.dumps({"mcpServers": {"x": {"command": "npx"}}}),
}


def tarball(files, top="repo-main"):
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for path, text in files.items():
            data = text if isinstance(text, bytes) else text.encode()
            info = tarfile.TarInfo(f"{top}/{path}")
            info.size = len(data)
            t.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def zipped(files):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for path, text in files.items():
            z.writestr(path, text)
    return buf.getvalue()


@pytest.fixture
def web(monkeypatch):
    """The web, as far as these tests go: {url: bytes}, and every address asked for."""
    pages = {
        "https://raw.githubusercontent.com/anthropics/skills/HEAD/.claude-plugin/marketplace.json": SKILLS_REPO[
            ".claude-plugin/marketplace.json"
        ].encode(),
        "https://codeload.github.com/anthropics/skills/tar.gz/HEAD": tarball(SKILLS_REPO),
        "https://codeload.github.com/acme/tools/tar.gz/HEAD": tarball(TOOLS_REPO),
        "https://example.com/notes.zip": zipped({"notes/SKILL.md": TOOLS_REPO["skills/notes/SKILL.md"]}),
    }
    asked = []

    def fetch(cfg, url, out, limit):
        asked.append(url)
        if url not in pages:
            raise ValueError("example.com answered 404 Not Found")
        out.write(pages[url])

    monkeypatch.setattr(marketplace, "_fetch", fetch)
    marketplace._cache.clear()
    return asked


@pytest.fixture
def llm(cfg):
    srv = start_llm(cfg)
    yield fake_llm.Handler
    srv.shutdown()


@pytest.fixture
def app(cfg, db, llm):
    from app.main import create_app

    return create_app(cfg, db, background=False)


def test_sources_people_paste():
    p = marketplace.parse_source
    assert p("anthropics/skills") == {"host": "github", "repo": "anthropics/skills"}
    assert p("anthropics/skills@v2") == {"host": "github", "repo": "anthropics/skills", "ref": "v2"}
    assert p("https://github.com/anthropics/skills/tree/main/skills/pdf") == {
        "host": "github",
        "repo": "anthropics/skills",
        "ref": "main",
        "path": "skills/pdf",
    }
    assert p("https://github.com/a/b/blob/main/.claude-plugin/marketplace.json") == {"host": "github", "repo": "a/b", "ref": "main"}
    assert p("https://raw.githubusercontent.com/a/b/main/.claude-plugin/marketplace.json") == {
        "host": "github",
        "repo": "a/b",
        "ref": "main",
    }
    assert p("https://gitlab.com/g/sub/repo/-/tree/dev/x") == {"host": "gitlab", "repo": "g/sub/repo", "ref": "dev", "path": "x"}
    assert p("https://example.com/m.json")["host"] == "url"
    assert p("https://example.com/a.skill")["host"] == "archive"
    for bad in ("ftp://x/y", "just words", "https://example.com/page"):
        with pytest.raises(ValueError):
            p(bad)
    market = {"host": "github", "repo": "o/m", "ref": "main"}
    assert marketplace.plugin_source({"source": "./plugins/x"}, market)["path"] == "plugins/x"
    with pytest.raises(ValueError, match="outside"):
        marketplace.plugin_source({"source": "../../x"}, market)
    git = {"source": "git-subdir", "url": "https://github.com/o/r.git", "path": "plugins/p", "ref": "v1", "sha": "abc"}
    assert marketplace.plugin_source({"source": git}, market) == {"host": "github", "repo": "o/r", "ref": "abc", "path": "plugins/p"}


def test_a_marketplace_an_admin_adds(app, db, cfg, folder, new_client, web):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    ca, ha = s.cl["admin"]
    assert [m["source"] for m in c.get("/api/v1/marketplaces/suggested", headers=h).json()] == [
        "https://github.com/anthropics/skills",
        "https://github.com/anthropics/claude-plugins-official",
    ]
    # adding one is the admin's approval
    assert c.post("/api/v1/marketplaces", headers=h, json={"source": "anthropics/skills"}).status_code == 403
    mid = ca.post("/api/v1/marketplaces", headers=ha, json={"source": "anthropics/skills"}).json()["id"]
    assert ca.post("/api/v1/marketplaces", headers=ha, json={"source": "https://github.com/anthropics/skills"}).status_code == 400
    assert len(c.get("/api/v1/marketplaces/suggested", headers=h).json()) == 1
    got = c.get("/api/v1/marketplaces", headers=h).json()
    assert [(m["name"], m["plugins"], m["description"]) for m in got] == [("anthropic-agent-skills", 4, "Anthropic example skills")]

    plugins = {p["name"]: p for p in c.get(f"/api/v1/marketplaces/{mid}/plugins", headers=h).json()}
    assert plugins["document-skills"]["installable"] and plugins["acme-tools"]["version"] == "1.2.0"
    assert not plugins["from-npm"]["installable"] and "npm" in plugins["from-npm"]["why_not"]

    # previewed: what it adds and leaves out, nothing installed
    pre = c.post(f"/api/v1/marketplaces/{mid}/plugins/document-skills/preview", headers=h).json()
    assert [(i["kind"], i["name"]) for i in pre["manifest"]["items"]] == [("skill", "pdf")]
    pdf = pre["manifest"]["items"][0]["spec"]
    assert pdf["files"] == {"FORMS.md": "Fill the form field by field."} and "Keep {{this}}" in pdf["instructions"]
    assert [x["kind"] for x in pre["skipped"]] == ["file"]
    assert c.get("/api/v1/extensions", headers=h).json() == []

    out = c.post(f"/api/v1/marketplaces/{mid}/plugins/document-skills/install", headers=h).json()
    assert out["version"] == 1
    ext = c.get(f"/api/v1/extensions/{out['id']}", headers=h).json()
    assert (ext["kind"], ext["name"], ext["visibility"], ext["origin"]) == ("plugin", "document_skills", "private", "import")
    assert ext["source"] == {"marketplace": mid, "plugin": "document-skills", "url": "https://github.com/anthropics/skills"}
    assert c.get(f"/api/v1/marketplaces/{mid}/plugins", headers=h).json()[0]["installed"] == out["id"]
    # again: a new version of the same extension
    assert c.post(f"/api/v1/marketplaces/{mid}/plugins/document-skills/install", headers=h).json() == {
        "id": out["id"],
        "version": 2,
        "skipped": [],
    }
    # a plugin from another repository, with what Lens leaves out named
    pre = c.post(f"/api/v1/marketplaces/{mid}/plugins/acme-tools/preview", headers=h).json()
    assert {x["kind"] for x in pre["skipped"]} == {"hooks", "mcp"}
    # a skill another extension already has can't be installed twice
    clash = c.post(f"/api/v1/marketplaces/{mid}/plugins/all-skills/install", headers=h)
    assert clash.status_code == 400 and "already called pdf" in clash.json()["detail"]

    assert ca.delete(f"/api/v1/marketplaces/{mid}", headers=ha).status_code == 200
    assert c.get("/api/v1/marketplaces", headers=h).json() == []
    assert c.get(f"/api/v1/extensions/{out['id']}", headers=h).status_code == 200  # what was installed stays


def test_imported_from_a_link_or_a_file(app, db, cfg, folder, new_client, web):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    pre = c.post("/api/v1/extensions/import", headers=h, json={"url": "acme/tools", "preview": True}).json()
    items = {i["name"]: i for i in pre["manifest"]["items"]}
    assert {n: i["kind"] for n, i in items.items()} == {"meeting_notes": "skill", "review": "skill", "critic": "tool"}
    assert items["review"]["spec"]["when"] == "someone asks for /review, or for this: Review a change"
    critic = items["critic"]["spec"]
    assert critic["run"] == {"type": "prompt", "system": "You are a critic. Be blunt.", "prompt": "{{task}}"}
    assert pre["manifest"]["name"] == "acme_tools" and c.get("/api/v1/extensions", headers=h).json() == []

    out = c.post("/api/v1/extensions/import", headers=h, json={"url": "https://github.com/acme/tools"}).json()
    assert out["version"] == 1 and {x["kind"] for x in out["skipped"]} == {"hooks", "mcp"}
    assert c.post("/api/v1/extensions/import", headers=h, json={"url": "https://github.com/acme/tools"}).json()["version"] == 2
    bad = c.post("/api/v1/extensions/import", headers=h, json={"url": "https://github.com/acme/missing"})
    assert bad.status_code == 400 and "404" in bad.json()["detail"]
    assert c.post("/api/v1/extensions/import", headers=h, json={"url": "https://example.com/m.json"}).status_code == 400

    # a skill folder zipped up (as claude.ai exports one): one skill, in a plugin of its own
    data = base64.b64encode(zipped({"notes/SKILL.md": PDF.replace("name: pdf", "name: pdf-forms"), "notes/FORMS.md": "x"})).decode()
    got = c.post("/api/v1/extensions/import/archive", headers=h, json={"filename": "pdf.skill", "data": data}).json()
    ext = c.get(f"/api/v1/extensions/{got['id']}", headers=h).json()
    assert ext["name"] == "pdf_forms" and ext["spec"]["items"][0]["spec"]["files"] == {"FORMS.md": "x"}
    junk = c.post("/api/v1/extensions/import/archive", headers=h, json={"filename": "x.zip", "data": base64.b64encode(b"nope").decode()})
    assert junk.status_code == 400


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def test_a_skills_files_in_a_conversation(app, db, cfg, folder, new_client, web, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    c.post("/api/v1/extensions/import", headers=h, json={"url": "https://github.com/anthropics/skills/tree/HEAD/skills/pdf"})
    llm.seen.clear()
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "use_skill", {"name": "pdf"})]},
        {"content": "", "tool_calls": [call(2, "use_skill", {"name": "pdf", "file": "FORMS.md"})]},
        {"content": "Done."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Fill this PDF form"}).text)
    assert [x["summary"] for x in ev["step"]] == ["Read the skill pdf", "Read FORMS.md from the skill pdf"]
    first = [b for b in llm.seen if b.get("tools")][0]
    use = next(t for t in first["tools"] if t["function"]["name"] == "use_skill")
    assert "file" in use["function"]["parameters"]["properties"]
    tool_msgs = [m["content"] for b in llm.seen if b.get("tools") for m in b["messages"] if m["role"] == "tool"]
    assert '"files": ["FORMS.md"]' in tool_msgs[0] and "field by field" in tool_msgs[-1]
