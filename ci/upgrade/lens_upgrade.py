#!/usr/bin/env python3
"""Seed a Lens with representative data, and check that an upgraded Lens still has all of it.

ci/upgrade/upgrade.sh runs these steps around an upgrade from the last release to this checkout (docs/database.md,
Upgrades). Every step but `rerun` needs only Python's standard library:

  seed URL STATE          write data through the API of the Lens at URL as the admin LENS_ADMIN_EMAIL /
                          LENS_ADMIN_PASSWORD: two transcripts (and their analysis), an entity, a note and a comment on
                          an asset, a notes page, a conversation, an MCP server, an API key and settings. Waits until
                          their jobs are done, then saves what the API shows of them in STATE (JSON).
  check URL STATE         read the same through the API of the (upgraded) Lens at URL, signed in again with the
                          password and with the API key; exits 1 listing what differs.
  dump SURREAL OUT        every record of every table in Lens's database on the SurrealDB server at SURREAL (an
                          http:// address; SURREAL_USER, SURREAL_PASS, SURREAL_NS and SURREAL_DB as Lens has them).
  kept BEFORE AFTER       exits 1 when a record in the BEFORE dump is gone from the AFTER one.
  ran-once BEFORE AFTER DATA_DIR LOG...
                          exits 1 unless each upgrade step that ran (done in AFTER, not in BEFORE) is said to start
                          exactly once across the logs (the API's and the worker's, started together), none failed,
                          and a backup was taken first in DATA_DIR/backups.
  rerun SURREAL BEFORE    with the backend's packages (its venv's python, from fastapi_backend): checks no step is
                          left to run, then runs every step twice; exits 1 when a step the upgrade ran (done now, not in
                          the BEFORE dump) changes a record the first time, or any step does the second time.

The seed is written against the API of the version it seeds: upgrade.sh runs the seed from the release it upgrades
from when that release has this file, and this checkout's otherwise. So when the API changes, change `seed` and
`facts` together, and keep the names `facts` gives what it reads: the upgraded Lens is checked against them.
"""

from __future__ import annotations

import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

NS = "upgrade"
TRANSCRIPTS = {
    "Harbour planning call": (
        "Ada|N|Welcome. Ada Lovelace will review the harbour plan with Charles Babbage in London on Monday.\n"
        "Charles|N|The Analytical Engine budget is due on Friday, so the harbour plan comes first.\n"
        "Ada|Happy|Then the Difference Engine team in Cambridge can start in the spring."
    ),
    "Engine budget review": (
        "Mary|N|Mary Somerville here. The Analytical Engine budget needs the harbour figures.\n"
        "Charles|N|Charles Babbage will send the London figures by Friday."
    ),
}
ENTITY = {
    "name": "Harbour Board",
    "type": "TERM",
    "description": "Approves the harbour plan.",
    "aliases": ["the Board"],
}
NOTE = "Check the tide tables before Monday."
COMMENT = "Babbage confirmed the date by email."
PAGE = {
    "title": "Harbour plan",
    "body": "Ada leads the review; the budget is due Friday.",
}
CHAT = {"title": "Harbour questions", "question": "Who reviews the harbour plan?"}
MCP = {
    "name": "tracker",
    "kind": "mcp",
    "description": "Issue tracker",
    "url": "https://mcp.example.com/mcp",
    "headers": {"Authorization": "Bearer upgrade-check-token"},
    "tools": [
        {
            "name": "search_issues",
            "description": "Find issues",
            "schema": {"type": "object"},
            "read_only": True,
        }
    ],
}
SETTINGS = {
    "llm": {"model": "upgrade-check-model", "api_key": "sk-upgrade-check"},
    "updates": {"check": False},
}
JOB_WAIT = 300  # seconds the seeded assets' jobs may take

# what the database keeps about upgrading itself, which an upgrade changes
BOOKKEEPING = {"migration", "migration_lock", "seq"}

# local addresses only: never through a web proxy
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Api:
    def __init__(self, url, token=None):
        self.url = url
        self.base = url.rstrip("/") + "/api/v1"
        self.token = token

    def call(self, method, path, body=None, raw=False, **query):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        headers = {"Accept": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with OPENER.open(req, timeout=120) as r:
                out = r.read()
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            raise SystemExit(f"{method} {path}: HTTP {e.code} {detail}") from None
        return out.decode() if raw else json.loads(out or b"null")

    def get(self, path, **query):
        return self.call("GET", path, **query)

    def post(self, path, body=None, **kw):
        return self.call("POST", path, {} if body is None else body, **kw)


def sign_in(url):
    email, password = os.environ["LENS_ADMIN_EMAIL"], os.environ["LENS_ADMIN_PASSWORD"]
    r = Api(url).post("/auth/login", {"email": email, "password": password})
    return Api(url, r["access_token"])


def wait_for_jobs(api):
    """Until every job of the seeded namespace has finished; they must have succeeded."""
    deadline = time.time() + JOB_WAIT
    while True:
        jobs = api.get("/jobs", namespace=NS, limit=100)["jobs"]
        busy = [j for j in jobs if j["status"] in ("queued", "running", "waiting")]
        if not busy:
            failed = [j for j in jobs if j["status"] != "succeeded"]
            if failed:
                summary = [(j["title"], j["status"], j["error"]) for j in failed]
                raise SystemExit(f"seeded assets' jobs didn't succeed: {summary}")
            return
        if time.time() > deadline:
            raise SystemExit(
                f"jobs still running after {JOB_WAIT}s: {[j['title'] for j in busy]}"
            )
        time.sleep(2)


def seed(url, state_path):
    api = sign_in(url)
    assets = {}
    for title, text in TRANSCRIPTS.items():
        body = {"namespace": NS, "title": title, "text": text}
        assets[title] = api.post("/import", body)["id"]
    wait_for_jobs(api)
    first = assets["Harbour planning call"]
    api.post(f"/namespaces/{NS}/entities", ENTITY)
    api.post(f"/resources/{first}/notes", {"text": NOTE})
    api.post(f"/resources/{first}/comments", {"text": COMMENT})
    api.post("/notes", {"ns": NS, **PAGE})
    chat = api.post("/chats", {"title": CHAT["title"]})["id"]
    # the answer streams; without a model Lens answers with the passages that match
    api.post(f"/chats/{chat}/messages", {"content": CHAT["question"]}, raw=True)
    api.post("/extensions", {"manifest": MCP, "enabled": False})
    token = api.post("/tokens", {"name": "upgrade check"})["token"]
    for section, values in SETTINGS.items():
        api.call("PUT", f"/settings/{section}", values)
    state = {"url": url, "token": token, "facts": facts(api, token)}
    with open(state_path, "w") as f:
        json.dump(state, f, indent=1, sort_keys=True)
    print(f"seeded {len(state['facts'])} facts into the Lens at {url}")


def facts(api, token):
    """What the API shows of the seeded data, by name."""
    out = {}
    me = api.get("/auth/me")["user"]
    out["admin"] = {"email": me["email"], "admin": me["admin"]}
    out["API key"] = Api(api.url, token).get("/auth/me")["user"]["email"]
    for r in api.get("/resources", ns=NS, limit=100):
        rid, title = r["id"], r["title"]
        doc = json.loads(api.get(f"/resources/{rid}/export.json", raw=True))
        out[f"asset {title}"] = {
            "namespace": r["namespace"],
            "transcript": [[s.get("speaker"), s["text"]] for s in doc["segments"]],
        }
        out[f"notes on {title}"] = sorted(
            n["text"] for n in api.get(f"/resources/{rid}/notes")
        )
        out[f"comments on {title}"] = sorted(
            c["text"] for c in api.get(f"/resources/{rid}/comments")
        )
    entities = api.get("/entities", namespaces=NS, limit=200)["items"]
    # every entity by name, whatever its type: an upgrade step may type a Term (Monday becomes a Date, which the list
    # leaves out unless asked for), but none may go
    quiet = api.get("/entities", namespaces=NS, limit=200, types="DATE,NUMBER")["items"]
    out["entities"] = sorted(e["name"] for e in entities + quiet)
    for e in entities:
        if e["name"] == ENTITY["name"]:
            out[f"entity {e['name']}"] = {
                "type": e["type"],
                "description": e["description"],
                "aliases": sorted(e["aliases"]),
            }
    for p in api.get("/notes", ns=NS)["pages"]:
        page = api.get(f"/notes/{p['id']}")
        out[f"page {page['title']}"] = page["body"]
    for c in api.get("/chats"):
        chat = api.get(f"/chats/{c['id']}")
        out[f"conversation {chat['title']}"] = [
            [m["role"], m["content"]] for m in chat["messages"]
        ]
    for x in api.get("/extensions"):
        ext = api.get(f"/extensions/{x['id']}")
        spec = ext["spec"]
        # the headers by name: a newer Lens shows the sealed values masked
        out[f"MCP server {ext['name']}"] = {
            "kind": ext["kind"],
            "description": ext["description"],
            "enabled": ext["enabled"],
            "url": spec.get("url"),
            "headers": sorted(spec.get("headers") or {}),
            "tools": sorted(t["name"] for t in spec.get("tools") or []),
        }
    settings = api.get("/settings")
    for section, values in SETTINGS.items():
        for key in values:
            v = settings[section]["values"][key]
            out[f"setting {section}.{key}"] = (
                v.get("set") if isinstance(v, dict) and v.get("secret") else v
            )
    hits = api.get("/search", q="harbour", ns=NS)["hits"]
    out["search harbour"] = sorted({h["title"] for h in hits})
    return out


def check(url, state_path):
    with open(state_path) as f:
        state = json.load(f)
    now = facts(sign_in(url), state["token"])
    wrong = []
    for name, was in state["facts"].items():
        if name not in now:
            wrong.append(f"{name}: gone (was {was!r})")
        elif now[name] != was:
            wrong.append(f"{name}: was {was!r}, now {now[name]!r}")
    if wrong:
        print(
            f"After the upgrade the API shows {len(wrong)} of {len(state['facts'])} seeded facts differently:"
        )
        print("\n".join("  " + w for w in wrong))
        sys.exit(1)
    print(f"all {len(state['facts'])} seeded facts read the same after the upgrade")


# ---------- the database itself ----------
def sql(surreal, query):
    auth = f"{os.environ.get('SURREAL_USER', 'root')}:{os.environ.get('SURREAL_PASS', 'root')}"
    headers = {
        "Accept": "application/json",
        "Authorization": "Basic " + base64.b64encode(auth.encode()).decode(),
        "Surreal-NS": os.environ.get("SURREAL_NS", "archive"),
        "Surreal-DB": os.environ.get("SURREAL_DB", "main"),
    }
    req = urllib.request.Request(
        surreal.rstrip("/") + "/sql",
        data=query.encode(),
        headers=headers,
        method="POST",
    )
    with OPENER.open(req, timeout=120) as r:
        results = json.loads(r.read())
    for res in results:
        if res.get("status") != "OK":
            raise SystemExit(f"{query}: {res.get('result')}")
    return [res["result"] for res in results]


def dump(surreal):
    """{table: {record id: record}} for every table, in one request."""
    tables = sorted(sql(surreal, "INFO FOR DB")[0]["tables"])
    rows = (
        sql(surreal, ";".join(f"SELECT * FROM `{t}`" for t in tables)) if tables else []
    )
    return {t: {str(r["id"]): r for r in rs} for t, rs in zip(tables, rows)}


def _data(surreal):
    return {t: rs for t, rs in dump(surreal).items() if t not in BOOKKEEPING and rs}


def _changes(a, b):
    """The ids of the records added, removed or changed between two dumps."""
    return sorted(
        i
        for t in set(a) | set(b)
        for i in set(a.get(t, {})) | set(b.get(t, {}))
        if a.get(t, {}).get(i) != b.get(t, {}).get(i)
    )


def write_dump(surreal, out_path):
    d = dump(surreal)
    with open(out_path, "w") as f:
        json.dump(d, f, indent=1, sort_keys=True, default=str)
    print(f"{sum(map(len, d.values()))} records in {len(d)} tables")


def kept(before_path, after_path):
    before, after = _load(before_path), _load(after_path)
    gone = {
        t: sorted(set(rows) - set(after.get(t, {})))
        for t, rows in before.items()
        if t not in BOOKKEEPING and set(rows) - set(after.get(t, {}))
    }
    if gone:
        print("Records gone after the upgrade:")
        for t, ids in gone.items():
            print(
                f"  {t}: {len(ids)} of {len(before[t])} ({', '.join(ids[:10])}{', …' if len(ids) > 10 else ''})"
            )
        sys.exit(1)
    n = sum(len(rows) for t, rows in before.items() if t not in BOOKKEEPING)
    print(f"all {n} records are still there")


def _load(path):
    with open(path) as f:
        return json.load(f)


def _done(d):
    return {r["name"] for r in d.get("migration", {}).values() if r.get("done_at")}


def ran_once(before_path, after_path, data_dir, logs):
    before, after = _load(before_path), _load(after_path)
    ran = sorted(_done(after) - _done(before))
    failed = sorted(
        r["name"] for r in after.get("migration", {}).values() if not r.get("done_at")
    )
    said = []
    for path in logs:
        with open(path, errors="replace") as f:
            said += re.findall(r"upgrading the database: ([\w-]+)", f.read())
    wrong = [f"{name} failed" for name in failed]
    for name in sorted(set(ran) | set(said)):
        if said.count(name) != 1 or name not in ran:
            wrong.append(
                f"{name}: done {'now' if name in ran else 'before'}, started {said.count(name)} times"
            )
    backups = os.path.join(data_dir, "backups")
    taken = (
        [n for n in os.listdir(backups) if n.startswith("before-upgrade-")]
        if os.path.isdir(backups)
        else []
    )
    if ran and not taken:
        wrong.append(f"no backup in {backups} before the upgrade")
    if wrong:
        print("The upgrade steps didn't run as they should:")
        print("\n".join("  " + w for w in wrong))
        sys.exit(1)
    print(
        f"steps run once each: {', '.join(ran) or 'none were pending'}; backups taken first: {', '.join(taken) or '-'}"
    )


def rerun(surreal, before_path):
    """Every step, twice, on the upgraded data: the steps the upgrade ran change nothing the first time (it's their
    second run), and no step changes anything the second time."""
    from app.domain import migrations, store

    db = store.connect(store.load_config(), upgrade=False)
    left = migrations.pending(db)
    if left:
        raise SystemExit(
            f"steps still pending after the upgrade (the next start would run them): {', '.join(left)}"
        )
    ran = _done(dump(surreal)) - _done(_load(before_path))
    wrong, names = [], []
    for name, step in migrations.steps():
        names.append(name)
        first = _data(surreal)
        step(db)
        second = _data(surreal)
        if name in ran and (changed := _changes(first, second)):
            wrong.append(
                f"{name}, run again after the upgrade that ran it, changed {', '.join(changed[:10])}"
            )
        step(db)
        if changed := _changes(second, _data(surreal)):
            wrong.append(
                f"{name}, run twice on the upgraded data, changed {', '.join(changed[:10])} the second time"
            )
    db.close()
    if wrong:
        print("Upgrade steps aren't safe to run again:")
        print("\n".join("  " + w for w in wrong))
        sys.exit(1)
    print(
        f"ran each of the {len(names)} steps twice on the upgraded data: the second run of those the upgrade ran "
        f"({', '.join(sorted(ran)) or 'none'}) and of every step changed nothing"
    )


def main(argv):
    cmd, args = (argv[0], argv[1:]) if argv else ("", [])
    commands = {
        "seed": (seed, 2),
        "check": (check, 2),
        "dump": (write_dump, 2),
        "kept": (kept, 2),
        "rerun": (rerun, 2),
    }
    if cmd == "ran-once" and len(args) >= 4:
        return ran_once(args[0], args[1], args[2], args[3:])
    if cmd not in commands or len(args) != commands[cmd][1]:
        sys.exit(__doc__)
    commands[cmd][0](*args)


if __name__ == "__main__":
    main(sys.argv[1:])
