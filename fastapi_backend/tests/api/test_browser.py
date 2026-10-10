"""The person's browser, for the assistant (browser.py): a side panel connects, the assistant's browser_ tools ask it
to act and wait for what it did, and only its own person reaches any of it."""

from __future__ import annotations

import json
import re
import threading

import pytest

from app.domain import ai_tools, browser
from tests import fake_llm
from tests.api._assist import Assist, sse, start_llm


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


def unwrapped(text):
    """A tool's result as the model read it, without the markers that say it's third-party text (untrusted.py)."""
    return re.sub(r'^<untrusted-[0-9a-f]+ source="[^"]*">\n|\n</untrusted-[0-9a-f]+>$', "", text)


def actions(text):
    return [json.loads(b.split("data: ", 1)[1]) for b in text.split("\n\n") if b.startswith("event: action")]


class SidePanel:
    """A browser's side panel, as the extension runs it: it asks for actions (once=true, again and again) and answers
    each with `handle(tool, args)`, which returns a result or raises to say why not."""

    def __init__(self, client, headers, handle, label="Chrome"):
        self.c, self.h, self.handle = client, headers, handle
        self.sid = client.post("/api/v1/browser/sessions", headers=headers, json={"label": label}).json()["id"]
        self.seen, self.stop = [], threading.Event()
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def _loop(self):
        while not self.stop.is_set():
            got = self.c.get(f"/api/v1/browser/sessions/{self.sid}/actions", headers=self.h, params={"once": "true"})
            for a in actions(got.text):
                self.seen.append(a)
                try:
                    body = {"ok": True, "result": self.handle(a["tool"], a["args"])}
                except Exception as e:  # noqa: BLE001 - what the browser says back
                    body = {"ok": False, "error": str(e)}
                self.c.post(f"/api/v1/browser/actions/{a['id']}", headers=self.h, json=body)
            self.stop.wait(0.05)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.stop.set()
        self.thread.join(5)


def page(url="https://mail.example/inbox", text="Inbox: Invoice from ACME [e3] link 'invoice.pdf'"):
    return {"url": url, "title": "Inbox", "text": text, "said": f"Read {url}"}


def test_a_connected_side_panel_gives_the_assistant_the_browser(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]

    def handle(tool, args):
        if tool == "open":
            return page(args["url"])
        if tool == "click":
            return {**page(text="Invoice from ACME. Attached: invoice.pdf"), "said": "Clicked “Invoice from ACME”"}
        raise ValueError(f"unexpected {tool}")

    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "browser_open", {"url": "https://mail.example/inbox"})]},
        {"content": "", "tool_calls": [call(2, "browser_click", {"ref": "e3"})]},
        {"content": "The invoice from ACME has invoice.pdf attached."},
    ]
    with SidePanel(c, h, handle) as panel:
        cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
        llm.seen.clear()
        ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Check my mail", "browser": panel.sid}).text)
    assert ev["token"][0]["text"] == "The invoice from ACME has invoice.pdf attached."
    assert [x["tool"] for x in ev["step"]] == ["browser_open", "browser_click"]
    assert [x["summary"] for x in ev["step"]] == ["Read https://mail.example/inbox", "Clicked “Invoice from ACME”"]
    assert [(a["tool"], a["args"], a["chat"]) for a in panel.seen] == [
        ("open", {"url": "https://mail.example/inbox"}, cid),
        ("click", {"ref": "e3"}, cid),
    ]
    first = llm.seen[0]
    offered = {t["function"]["name"] for t in first["tools"]}
    assert {"browser_tabs", "browser_open", "browser_read", "browser_click", "browser_type", "browser_press", "browser_import"} <= offered
    assert "Lens side panel of their browser" in first["messages"][0]["content"]
    # what the page said reached the model as the tool's result, marked as third-party text
    tool_said = [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool"]
    assert tool_said[-1].startswith("<untrusted-") and 'source="tool browser_click"' in tool_said[-1]
    assert "Attached: invoice.pdf" in unwrapped(tool_said[-1])

    # the same question without the side panel, or with it closed (it says so as it closes), has no browser
    assert c.delete(f"/api/v1/browser/sessions/{panel.sid}", headers=h).status_code == 200
    llm.tool_script = [{"content": "I can't see your browser."}]
    llm.seen.clear()
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Again", "browser": panel.sid}).text)
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Again"}).text)
    for body in llm.seen:
        assert not any(t["function"]["name"].startswith("browser_") for t in body.get("tools") or [])


def test_what_the_browser_refuses_reaches_the_model(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]

    def handle(tool, args):
        raise PermissionError("the person said no to clicking “Send”")

    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "browser_click", {"ref": "e9", "consequential": True})]},
        {"content": "You chose not to send it."},
    ]
    with SidePanel(c, h, handle) as panel:
        cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
        llm.seen.clear()
        ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Send it", "browser": panel.sid}).text)
    assert panel.seen[0]["args"] == {"ref": "e9", "consequential": True}
    assert ev["step"][0]["summary"] == "browser_click: the person said no to clicking “Send”"
    assert "the person said no" in [m["content"] for m in llm.seen[-1]["messages"] if m["role"] == "tool"][0]


def test_attachments_downloaded_by_the_browser_join_the_conversation(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cv, hv = s.cl["viewer"]
    body = b"Invoice 42: 100 EUR, due Friday.\n"

    def upload(client, headers):
        up = client.post("/api/v1/uploads", headers=headers, json={"filename": "invoice.txt", "size": len(body), "hold": True}).json()
        client.put(f"/api/v1/uploads/{up['id']}", headers=headers, params={"offset": 0}, content=body)
        return up["id"]

    mine, theirs = upload(c, h), upload(cv, hv)
    given = iter([{"upload_id": mine, "said": "Downloaded invoice.txt"}, {"upload_id": theirs}])

    def handle(tool, args):
        assert tool == "import"
        return next(given)

    llm.tool_script = [
        {"content": "", "tool_calls": [call(1, "browser_import", {"ref": "e3"})]},
        {"content": "", "tool_calls": [call(2, "browser_import", {"url": "https://mail.example/att/2"})]},
        {"content": "", "tool_calls": [call(3, "import_files", {"upload_ids": [mine], "namespace": "pods"})]},
        {"content": "I attached invoice.txt and asked to put it in pods."},
    ]
    with SidePanel(c, h, handle) as panel:
        cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
        llm.seen.clear()
        ev = sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Import the invoice", "browser": panel.sid}).text)
    assert [x["summary"] for x in ev["step"][:2]] == [
        "Attached invoice.txt from the browser",
        "browser_import: the browser didn't attach the file",  # someone else's upload isn't taken for one
    ]
    said = json.loads(unwrapped([m["content"] for m in llm.seen[1]["messages"] if m["role"] == "tool"][0]))
    assert said["upload_id"] == mine and said["filename"] == "invoice.txt"
    assert ev["approval"][0]["summary"] == "Import invoice.txt into pods"


def test_only_its_own_person_reaches_a_session(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    cv, hv = s.cl["viewer"]
    sid = c.post("/api/v1/browser/sessions", headers=h, json={"label": "Chrome on the laptop"}).json()["id"]
    assert [(x["id"], x["label"], x["live"]) for x in c.get("/api/v1/browser/sessions", headers=h).json()] == [
        (sid, "Chrome on the laptop", True)
    ]
    assert cv.get("/api/v1/browser/sessions", headers=hv).json() == []
    assert cv.get(f"/api/v1/browser/sessions/{sid}/actions", headers=hv, params={"once": "true"}).status_code == 404
    assert cv.post(f"/api/v1/browser/sessions/{sid}/run", headers=hv, json={"tool": "tabs"}).status_code == 404
    assert cv.delete(f"/api/v1/browser/sessions/{sid}", headers=hv).status_code == 404
    assert new_client().post("/api/v1/browser/sessions", json={}).status_code == 401

    # someone else's conversation can't use it either
    box = ai_tools.Toolbox(db, cfg, {"id": 999, "email": "x"}, {s.pods}, {s.pods}, None, None, browser=sid)
    assert box.browser is None and not any(t["function"]["name"].startswith("browser_") for t in box.specs())

    # an action of this session can be answered only by its person, and only once
    done = threading.Event()
    got = {}

    def ask():
        try:
            got["out"] = browser.run(db, sid, _account(db, "ed@x.io"), "tabs", {}, wait=10)
        finally:
            done.set()

    threading.Thread(target=ask, daemon=True).start()
    waiting = []
    while not waiting:
        waiting = actions(c.get(f"/api/v1/browser/sessions/{sid}/actions", headers=h, params={"once": "true"}).text)
    aid = waiting[0]["id"]
    assert cv.post(f"/api/v1/browser/actions/{aid}", headers=hv, json={"ok": True, "result": "spoof"}).status_code == 404
    assert c.post(f"/api/v1/browser/actions/{aid}", headers=h, json={"ok": True, "result": {"tabs": []}}).status_code == 200
    assert done.wait(5) and got["out"] == {"tabs": []}
    assert c.post(f"/api/v1/browser/actions/{aid}", headers=h, json={"ok": True, "result": {}}).status_code == 404

    # closed, its waiting actions fail and the assistant can't use it
    assert c.delete(f"/api/v1/browser/sessions/{sid}", headers=h).status_code == 200
    assert c.get("/api/v1/browser/sessions", headers=h).json() == []
    with pytest.raises(browser.BrowserError, match="side panel isn't connected"):
        browser.run(db, sid, _account(db, "ed@x.io"), "tabs", {})


def _account(db, email):
    return db.values("SELECT VALUE record::id(id) FROM account WHERE email = $e", e=email)[0]


def test_agents_use_the_browser_through_the_api(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    with SidePanel(c, h, lambda tool, args: {"tabs": [{"id": 7, "title": "Inbox", "active": True}]}) as panel:
        out = c.post(f"/api/v1/browser/sessions/{panel.sid}/run", headers=h, json={"tool": "tabs"}).json()
    assert out == {"ok": True, "result": {"tabs": [{"id": 7, "title": "Inbox", "active": True}]}, "error": None}
    # nobody answering: the agent hears so instead of waiting forever
    out = c.post(f"/api/v1/browser/sessions/{panel.sid}/run", headers=h, json={"tool": "read", "wait": 1}).json()
    assert out["ok"] is False and "didn't answer in time" in out["error"]
    assert c.post(f"/api/v1/browser/sessions/{panel.sid}/run", headers=h, json={"tool": "delete_everything"}).status_code == 422
    audit = db.values("SELECT VALUE action FROM audit_log WHERE string::starts_with(action, 'browser.')")
    assert "browser.connect" in audit and "browser.run" in audit


def test_admins_can_turn_it_off(app, db, cfg, folder, new_client, llm):
    s = Assist(app, db, cfg, folder, new_client)
    c, h = s.cl["editor"]
    sid = c.post("/api/v1/browser/sessions", headers=h, json={}).json()["id"]
    ca, ha = s.cl["admin"]
    assert ca.put("/api/v1/settings/ai", headers=ha, json={"browser": False}).status_code == 200
    assert c.post("/api/v1/browser/sessions", headers=h, json={}).status_code == 403
    assert c.get(f"/api/v1/browser/sessions/{sid}/actions", headers=h, params={"once": "true"}).status_code == 403
    llm.tool_script = [{"content": "No browser here."}]
    llm.seen.clear()
    cid = c.post("/api/v1/chats", headers=h, json={}).json()["id"]
    sse(c.post(f"/api/v1/chats/{cid}/messages", headers=h, json={"content": "Open my mail", "browser": sid}).text)
    assert not any(t["function"]["name"].startswith("browser_") for t in llm.seen[0].get("tools") or [])
    assert ca.put("/api/v1/settings/ai", headers=ha, json={"browser_steps": 0}).status_code == 400
