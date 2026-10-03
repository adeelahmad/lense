"""Shared setup for the chat, collection and batch tests: seeded archive, a fake model and three people."""

from __future__ import annotations

import json

from app.domain import store, templates
from tests import fake_llm
from tests.helpers import login, make_user, seed


def sse(text):
    out = {}
    for block in text.strip().split("\n\n"):
        name = data = None
        for line in block.splitlines():
            if line.startswith("event: "):
                name = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if name:
            out.setdefault(name, []).append(data)
    return out


def start_llm(cfg):
    fake_llm.Handler.tool_script, fake_llm.Handler.reject_tools, fake_llm.Handler.decision = [], False, None
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    return srv


class Assist:
    """Seeded recordings a, b (pods) and call (calls); root (admin), ed (pods editor) and vi (pods viewer), signed in."""

    def __init__(self, app, db, cfg, folder, new_client):
        self.a, self.b, self.call = seed(db, cfg, folder)
        pods = store.ns_id(db, "pods")
        make_user(db, "root@x.io", "root password 1", admin=True)
        for email, role in (("ed@x.io", "editor"), ("vi@x.io", "viewer")):
            make_user(db, email, f"{role} password 1", roles={"pods": role})
        self.pods = pods
        self.notes = [t["id"] for t in templates.list_templates(db) if t["name"] == "Meeting notes"][0]
        self.cl = {}
        for who, email, pw in (
            ("admin", "root@x.io", "root password 1"),
            ("editor", "ed@x.io", "editor password 1"),
            ("viewer", "vi@x.io", "viewer password 1"),
        ):
            c = new_client()
            self.cl[who] = (c, login(c, email, pw))
