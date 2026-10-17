"""The assistant's web search and page reading (web.py, docs/assistant.md#web-search): off until an admin picks a
provider; each provider against a fake one here, never the internet; pages read through netguard (public addresses
only, here a network allowed for the test, on the web's ports); results cited like excerpts; and asking first once an
answer has read text from outside Lens."""

from __future__ import annotations

import http.server
import json
import sys
import threading
import types
import urllib.parse

import pytest

from app.domain import ai_tools, components, convert, netguard, settings, store, web
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm
from tests.helpers import text_pdf

PAGE = (
    "<html><head><title>Harbour news</title><style>p {color: red}</style></head><body>"
    "<nav>Home</nav><h1>The harbour</h1><p>Boats come in at dawn.</p><p>The ferry leaves at noon.</p>"
    "<p>Gulls follow the fishing boats.</p><script>document.write('not text')</script></body></html>"
)


class _Site(http.server.BaseHTTPRequestHandler):
    """Pages to read, and the search APIs: SearXNG's /search, Brave's and Tavily's, each answering in its own shape."""

    got: list[dict] = []
    base = ""

    def _send(self, code, kind, body, headers=()):
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers:
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj):
        self._send(200, "application/json", json.dumps(obj).encode())

    def do_GET(self):  # noqa: N802 - the handler's name
        u = urllib.parse.urlsplit(self.path)
        q = dict(urllib.parse.parse_qsl(u.query))
        _Site.got.append({"method": "GET", "path": u.path, "query": q})
        if u.path == "/searx/search":
            return self._json(
                {
                    "results": [
                        {
                            "title": "Harbour <b>news</b>",
                            "url": "https://www.harbour.example/news",
                            "content": "Boats at dawn.",
                            "publishedDate": "2026-10-01",
                        },
                        {"title": "Not a page", "url": "javascript:alert(1)", "content": "dropped"},
                        {"title": "Harbour page", "url": f"{_Site.base}/news", "content": "Local news."},
                    ]
                }
            )
        if u.path == "/brave/search":
            if self.headers.get("X-Subscription-Token") != "brave-key":
                return self._send(401, "application/json", b"{}")
            row = {"title": "Brave result", "url": "https://b.example/1", "description": "From Brave.", "age": "2 days ago"}
            return self._json({"web": {"results": [row]}})
        if u.path == "/doc.pdf":
            return self._send(200, "application/pdf", text_pdf([["Lighthouse report", "The lamp was lit at dusk."]]))
        if u.path == "/moved":
            return self._send(302, "text/plain", b"", [("Location", "/page")])
        if u.path == "/to-inside":
            return self._send(302, "text/plain", b"", [("Location", "http://10.1.2.3/secret")])
        if u.path == "/blank":
            return self._send(200, "text/html", b"<html><body><div id=app></div></body></html>")
        if u.path == "/long":
            return self._send(200, "text/html", ("<p>" + "harbour " * 600 + "</p>").encode())
        if u.path == "/file.zip":
            return self._send(200, "application/zip", b"PK\x03\x04")
        return self._send(200, "text/html; charset=utf-8", PAGE.encode())

    def do_POST(self):  # noqa: N802 - the handler's name
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])) or b"{}")
        _Site.got.append({"method": "POST", "path": self.path, "body": body})
        if self.headers.get("Authorization") != "Bearer tavily-key":
            return self._send(401, "application/json", b"{}")
        self._json({"results": [{"title": "Tavily result", "url": "https://t.example/1", "content": "From Tavily."}]})

    def log_message(self, *a):
        pass


@pytest.fixture
def site(monkeypatch, cfg):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Site.got = []
    port = srv.server_address[1]
    monkeypatch.setattr(netguard, "PORTS", netguard.PORTS | {port})  # the test site's port, as if 80
    cfg["documents"]["web_networks"] = ["127.0.0.0/8"]  # loopback, as if it were a public address
    base = _Site.base = f"http://127.0.0.1:{port}"
    monkeypatch.setattr(web, "BRAVE_URL", f"{base}/brave/search")
    monkeypatch.setattr(web, "TAVILY_URL", f"{base}/tavily/search")
    yield base
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
def ddgs(monkeypatch):
    """A stand-in for the ddgs package (the `web` extra isn't installed for tests): what it was asked, what it says."""
    calls: list[dict] = []
    answer: dict = {"rows": [{"title": "Duck result", "href": "https://d.example/1", "body": "From DuckDuckGo."}]}

    class DDGSException(Exception):
        pass

    class DDGS:
        def __init__(self, proxy=None, timeout=5):
            calls.append({"proxy": proxy, "timeout": timeout})

        def text(self, query, **kw):
            calls[-1].update(query=query, **kw)
            if isinstance(answer["rows"], Exception):
                raise answer["rows"]
            return answer["rows"]

    pkg, exc = types.ModuleType("ddgs"), types.ModuleType("ddgs.exceptions")
    pkg.DDGS, exc.DDGSException = DDGS, DDGSException  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "ddgs", pkg)
    monkeypatch.setitem(sys.modules, "ddgs.exceptions", exc)
    return types.SimpleNamespace(calls=calls, answer=answer, error=DDGSException)


def call(i, name, args):
    return {"id": f"c{i}", "type": "function", "function": {"name": name, "arguments": json.dumps(args)}}


def _me(db):
    return db.one("SELECT record::id(id) AS id, email FROM account WHERE email = 'ed@x.io'")


def _box(db, cfg, s, outside=(), said="", chat=None):
    return ai_tools.Toolbox(db, cfg, _me(db), {s.pods}, {s.pods}, {}, chat, outside=outside, said=said)


def _names(box):
    return {t["function"]["name"] for t in box.specs()}


def test_off_by_default_so_the_assistant_has_no_web_tools(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    assert cfg["web"]["search"] == "off" and not web.enabled(cfg)
    box = _box(db, cfg, s)
    assert not (_names(box) & web.NAMES) and "web_search" not in box.system_note()
    assert json.loads(box.call("web_search", {"query": "boats"})[0]) == {"error": "no tool web_search"}
    with pytest.raises(ValueError, match="web search is off"):
        web.search(cfg, "boats")
    with pytest.raises(ValueError, match="off"):
        web.read(cfg, "https://example.com/")
    # an admin picks a provider: both tools follow that one switch, and each can still be switched off on its own
    cfg["web"]["search"] = "duckduckgo"
    box = _box(db, cfg, s)
    assert _names(box) >= web.NAMES and "web_search" in box.system_note()
    assert web.NAMES <= ai_tools.builtin_names() and ai_tools.SENDS_OUT == web.NAMES
    cfg["ai"]["disabled_tools"] = ["web_read"]
    assert "web_search" in _names(_box(db, cfg, s)) and "web_read" not in _names(_box(db, cfg, s))


def test_searxng_brave_and_tavily_answer_as_results(app, db, cfg, site):
    cfg["web"].update(search="searxng", searxng_url=f"{site}/searx")
    got = web.search(cfg, "  harbour   boats ", limit=5, time_range="week")
    assert got == [
        {
            "title": "Harbour news",
            "url": "https://www.harbour.example/news",
            "snippet": "Boats at dawn.",
            "site": "harbour.example",
            "date": "2026-10-01",
        },
        {"title": "Harbour page", "url": f"{site}/news", "snippet": "Local news.", "site": "127.0.0.1"},
    ]
    assert _Site.got[-1]["query"] == {"q": "harbour boats", "format": "json", "safesearch": "1", "time_range": "week"}
    assert len(web.search(cfg, "harbour", limit=1)) == 1

    cfg["web"].update(search="brave", api_key=None)
    asked = len(_Site.got)
    with pytest.raises(ValueError, match="needs its API key"):
        web.search(cfg, "harbour")
    assert len(_Site.got) == asked  # nothing went out
    cfg["web"]["api_key"] = "wrong"
    with pytest.raises(ValueError, match="401.*check the API key"):
        web.search(cfg, "harbour")
    cfg["web"]["api_key"] = "brave-key"
    assert web.search(cfg, "harbour", time_range="day") == [
        {"title": "Brave result", "url": "https://b.example/1", "snippet": "From Brave.", "site": "b.example", "date": "2 days ago"}
    ]
    assert _Site.got[-1]["query"] == {"q": "harbour", "count": "5", "freshness": "pd"}

    cfg["web"].update(search="tavily", api_key="tavily-key")
    assert web.search(cfg, "harbour", limit=3)[0]["title"] == "Tavily result"
    assert _Site.got[-1]["body"] == {"query": "harbour", "max_results": 3}

    # every search is a row in the activity ledger: the provider and how many came back, never what was searched for
    rows = db.rows("SELECT * OMIT id FROM activity WHERE action = 'web.search' ORDER BY at")
    assert [r["detail"]["provider"] for r in rows] == ["searxng", "searxng", "brave", "brave", "tavily"]
    assert rows[0]["kind"] == "out" and rows[0]["ok"] and rows[0]["detail"] == {"provider": "searxng", "results": 2}
    assert not rows[0].get("unpriced") and rows[-1]["unpriced"] and rows[-1]["detail"]["results"] == 1  # they charge
    assert not rows[2]["ok"] and "harbour" not in json.dumps(rows, default=str)


def test_duckduckgo_asks_through_the_guard_with_ddgs(app, db, cfg, ddgs):
    cfg["web"]["search"] = "duckduckgo"
    assert web.search(cfg, "duck pond", limit=2, time_range="month") == [
        {"title": "Duck result", "url": "https://d.example/1", "snippet": "From DuckDuckGo.", "site": "d.example"}
    ]
    c = ddgs.calls[-1]
    assert c["proxy"].startswith("http://127.0.0.1:")  # netguard's proxy is its only way out
    assert (c["query"], c["backend"], c["max_results"], c["timelimit"]) == ("duck pond", "duckduckgo", 2, "m")
    ddgs.answer["rows"] = ddgs.error("No results found.")
    assert web.search(cfg, "nothing at all") == []
    ddgs.answer["rows"] = ddgs.error("Ratelimit")
    with pytest.raises(ValueError, match="DuckDuckGo didn't answer"):
        web.search(cfg, "duck")


def test_duckduckgo_is_fetched_when_its_picked(app, db, cfg, monkeypatch):
    monkeypatch.setitem(sys.modules, "ddgs", None)  # not installed yet
    cfg["web"]["search"] = "duckduckgo"
    with pytest.raises(ValueError, match="still being installed"):
        web.search(cfg, "duck")
    c = components.BY_ID["web-search"]
    assert c.needed(cfg, {}) and not c.present(cfg) and c.license == "MIT"
    cfg["web"]["search"] = "searxng"
    assert not c.needed(cfg, {})


def test_reading_pages_and_pdfs(app, db, cfg, site):
    cfg["web"]["search"] = "searxng"
    got = web.read(cfg, f"{site}/news")
    assert (got["title"], got["site"], got["kind"], got["url"]) == ("Harbour news", "127.0.0.1", "html", f"{site}/news")
    assert "Boats come in at dawn." in got["text"] and "not text" not in got["text"] and "color" not in got["text"]
    assert got["chars"] == len(got["text"]) and "truncated" not in got and got["fetched_at"]
    # find: the paragraphs that mention its words, and those either side
    got = web.read(cfg, f"{site}/news", find="ferry")
    assert got["found"] and "ferry leaves" in got["text"] and "Home" not in got["text"]
    assert web.read(cfg, f"{site}/news", find="submarine")["found"] is False
    # a redirect is followed (and checked again by the proxy); a PDF is read with pypdf
    assert web.read(cfg, f"{site}/moved")["url"] == f"{site}/page"
    pdf = web.read(cfg, f"{site}/doc.pdf")
    assert pdf["kind"] == "pdf" and "The lamp was lit at dusk." in pdf["text"]
    # at most web.read_chars of it
    cfg["web"]["read_chars"] = 1000
    got = web.read(cfg, f"{site}/long")
    assert len(got["text"]) == 1000 and got["truncated"] and got["chars"] > 4000
    with pytest.raises(ValueError, match="not a page to read"):
        web.read(cfg, f"{site}/file.zip")
    rows = db.rows("SELECT detail FROM activity WHERE action = 'web.read'")
    assert rows and all(r["detail"] == {"host": "127.0.0.1"} for r in rows)


def test_a_page_that_needs_scripts_is_loaded_with_chromium(app, db, cfg, site, monkeypatch):
    cfg["web"]["search"] = "searxng"
    printed = []

    def print_pdf(exe, guard, url, out, seconds, settle_ms=5000):  # Chromium, through the same guard
        printed.append((exe, url, guard.forward))
        out.write_bytes(text_pdf([["Written by its script."]]))

    monkeypatch.setattr(convert, "print_pdf", print_pdf)
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    assert web.read(cfg, f"{site}/blank")["text"] == ""  # off, and no Chromium: the page as it came
    cfg["web"]["render"] = True
    assert web.read(cfg, f"{site}/blank")["kind"] == "html" and printed == []
    monkeypatch.setattr(convert, "chromium", lambda cfg: "/usr/bin/chromium")
    got = web.read(cfg, f"{site}/blank")
    assert got["kind"] == "rendered" and "Written by its script." in got["text"]
    assert printed == [("/usr/bin/chromium", f"{site}/blank", True)]
    assert web.read(cfg, f"{site}/long")["kind"] == "html" and len(printed) == 1  # a page with text isn't loaded again


def test_only_public_addresses_are_read(app, db, cfg, site):
    cfg["web"]["search"] = "searxng"
    with pytest.raises(ValueError, match=r"refused: .*10\.1\.2\.3"):
        web.read(cfg, f"{site}/to-inside")  # the guard checks the redirect's address too
    cfg["documents"]["web_networks"] = []
    with pytest.raises(ValueError, match="isn't a public address"):
        web.read(cfg, f"{site}/news")
    for bad in ("file:///etc/passwd", "ftp://example.com/x", "http://user:pw@example.com/"):
        with pytest.raises(ValueError):
            web.read(cfg, bad)
    with pytest.raises(ValueError, match="80 and 443"):
        web.read(cfg, "http://8.8.8.8:22/")


def test_an_answer_cites_the_web_and_the_conversation_keeps_it(app, db, cfg, folder, new_client, llm, site):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    r = ca.put("/api/v1/settings/web", headers=ha, json={"search": "searxng", "searxng_url": f"{site}/searx"})
    assert r.status_code == 200, r.text
    c, h = s.cl["viewer"]  # anyone who chats gets the read tools
    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "web_search", {"query": "harbour boats"})]},
        {"content": "", "tool_calls": [call(2, "web_read", {"url": f"{site}/news"})]},
        {"content": "Boats come in at dawn [1], and the ferry leaves at noon [2]."},
    ]
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "When do the boats come in?"}).text)
    host = site.removeprefix("http://")
    assert [x["summary"] for x in ev["step"]] == ['Searched the web for "harbour boats": 2 results', f"Read {host}/news"]
    assert "approval" not in ev  # the page read came from the search's results: nothing it read went out
    # what the tools read reached the model marked as data
    tool_msgs = [m["content"] for m in [b for b in llm.seen if b.get("tools")][-1]["messages"] if m["role"] == "tool"]
    assert len(tool_msgs) == 2 and all(t.startswith("<untrusted-") for t in tool_msgs)
    cited = ev["passages"][-1]
    assert [p["n"] for p in cited] == [1, 2]
    assert cited[0] == {
        "n": 1,
        "source": "web",
        "url": "https://www.harbour.example/news",
        "title": "Harbour news",
        "site": "harbour.example",
        "fetched_at": cited[0]["fetched_at"],
        "text": "Boats at dawn.",
        "used": True,
    }
    # the page read keeps the number its search result had, with the page's own text
    assert cited[1]["url"] == f"{site}/news" and cited[1]["text"].startswith("Home The harbour Boats come in at dawn.")
    # the conversation keeps web citations whatever the person may read in the archive
    msg = c.get(f"/api/v1/chats/{cid}", headers=h).json()["messages"][-1]
    assert [p["n"] for p in msg["passages"]] == [1, 2] and msg["passages"][0]["source"] == "web"
    assert msg["passages"][0]["recording_id"] is None and msg["passages"][1]["site"] == "127.0.0.1"


def test_it_asks_before_a_search_once_it_read_outside_text(app, db, cfg, folder, new_client, site):
    s = Assist(app, db, cfg, folder, new_client)
    cfg["web"].update(search="searxng", searxng_url=f"{site}/searx")
    cid = db.next_id("chat")
    # the person wrote the words: it searches at once, even after reading outside text
    box = _box(db, cfg, s, outside=["page"], said="look up harbour boats please", chat=cid)
    assert box.wary()
    out, summary = box.call("web_search", {"query": "harbour boats"})
    assert summary.startswith("Searched the web") and box.approvals == []
    # a result's address came from a tool: reading it carries nothing the answer read, so it doesn't ask either
    assert box.call("web_read", {"url": f"{site}/news"})[1] == f"Read {site.removeprefix('http://')}/news"
    assert box.approvals == []
    # words it made up after reading outside text wait for the person's yes, and nothing is sent until then
    before = len(_Site.got)
    out, summary = box.call("web_search", {"query": "secret budget figures"})
    assert summary.startswith("Asked for approval: Search the web for “secret budget figures” (asked first: ")
    assert len(_Site.got) == before and json.loads(out)["status"] == "waiting for the person's approval"
    out, summary = box.call("web_read", {"url": "https://evil.example/?q=secret"})
    assert summary.startswith("Asked for approval: Read the web page evil.example?q=secret") and len(box.approvals) == 2
    # approved, it runs; asked again the same way, it hands over what it found then without searching again
    done = ai_tools.approve(db, cfg, box.approvals[0]["id"], _me(db), {s.pods})
    assert done["status"] == "done" and done["output"]["results"][0]["site"] == "harbour.example"
    sent = len(_Site.got)
    box = _box(db, cfg, s, outside=["earlier answers"], chat=cid)
    out, summary = box.call("web_search", {"query": "Secret  budget figures"})
    assert summary == 'Searched the web for "Secret budget figures": 2 results' and box.approvals == []
    assert len(_Site.got) == sent and box.refs[0]["source"] == "web" and box.refs[0]["url"] == "https://www.harbour.example/news"
    # with asking first switched off, nothing waits
    cfg["ai"]["untrusted_asks"] = False
    box = _box(db, cfg, s, outside=["page"], chat=cid)
    box.call("web_search", {"query": "made up words"})
    assert box.approvals == []


def test_web_search_settings(app, db, cfg, folder, new_client):
    s = Assist(app, db, cfg, folder, new_client)
    ca, ha = s.cl["admin"]
    for bad in ({"search": "google"}, {"results": 0}, {"read_chars": 10}, {"searxng_url": "searx.lan"}, {"render": "yes"}):
        assert ca.put("/api/v1/settings/web", headers=ha, json=bad).status_code == 400, bad
    r = ca.put("/api/v1/settings/web", headers=ha, json={"search": "brave", "api_key": "brave-key", "results": 8})
    assert r.status_code == 200, r.text
    view = ca.get("/api/v1/settings", headers=ha).json()["web"]["values"]
    assert view["api_key"] == {"secret": True, "set": True} and view["results"] == 8 and view["search"] == "brave"
    eff = settings.Settings(db, cfg).current()["web"]
    assert eff["api_key"] == "brave-key" and eff["read_chars"] == 20000 and eff["render"] is False
    c, h = s.cl["editor"]
    assert c.put("/api/v1/settings/web", headers=h, json={"search": "off"}).status_code == 403
    assert store.DEFAULTS["web"]["search"] == "off"
