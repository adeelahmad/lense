"""Lens's own manual, for its assistant and for agents (the lens_help tool): the documentation in docs/, found by words,
and what this server has: its addresses (the web app, MCP, the API), its version, what is turned on and where each is
set, and what the person asking may do.

The docs are read from LENS_DOCS_DIR, else the docs/ folder next to the code: the repository's own, and /docs in the
containers, where docker-compose mounts it read-only. Each page is cut at its headings into sections, kept in an SQLite
FTS5 table in memory (English stemming, BM25 ranking; what the word index uses too, textindex.py), built on first use
and again when a page changes: a few MB for the whole manual, no model and no network. A section links to its page on
GitHub at that heading. Without the docs (an image built elsewhere and run without them), only the server's own facts
are given, with the link to the docs online.
"""

from __future__ import annotations

import os
import pathlib
import re
import sqlite3
import threading

from . import __version__, browser, chat, https, llm, oauth, prometheus, semantic, store, tunnel

DOCS_URL = "https://github.com/adeelahmad/lense/blob/main/docs/"
SKIP = {"CHANGELOG.md"}  # release notes: every change ever made, which would crowd out how things work now
SECTION_CHARS = 1200  # of a section found by a search
PAGE_CHARS = 5000  # of a page read whole; the rest of its sections are listed, to read with a query
TOP = 4  # sections a search gives back
MORE = 40  # headings listed of a long page's sections not read
HEADING = re.compile(r"^(#{1,4})\s+(.+?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(```|~~~)")


def folder():
    """Where the docs are, or None when this install has none (or an empty folder where they'd be mounted)."""
    for p in (os.environ.get("LENS_DOCS_DIR"), pathlib.Path(__file__).resolve().parents[3] / "docs"):
        if p and pathlib.Path(p).is_dir() and any(pathlib.Path(p).glob("*.md")):
            return pathlib.Path(p)
    return None


def slug(heading, seen):
    """A heading's anchor as GitHub writes it: lower case, punctuation dropped, spaces as hyphens, and -1, -2 after
    a repeat on the same page."""
    s = re.sub(r"[^\w\- ]", "", heading.replace("`", "").strip().lower()).replace(" ", "-")
    n = seen.get(s, 0)
    seen[s] = n + 1
    return f"{s}-{n}" if n else s


def _clean(lines):
    text = "\n".join(lines)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def sections(name, text):
    """A page cut at its headings: (page title, [{title, anchor, text}]), each section titled with the headings above
    it ("MCP server › Connecting"). Headings inside code blocks are code."""
    lines = text.splitlines()
    page, seen, chain, out, body, anchor, fenced = None, {}, [], [], [], "", False

    def close():
        t = _clean(body)
        if t:
            out.append({"title": " › ".join(h for _, h in chain) or page or name, "anchor": anchor, "text": t})

    for line in lines:
        if FENCE.match(line):
            fenced = not fenced
        m = None if fenced else HEADING.match(line)
        if not m:
            body.append(line)
            continue
        close()
        level, title = len(m.group(1)), m.group(2).replace("`", "").strip()
        if level == 1 and page is None:
            page, anchor = title, ""
            seen[slug(title, {})] = 1
        else:
            anchor = slug(m.group(2), seen)
        chain = [(lv, t) for lv, t in chain if lv < level] + [(level, title)]
        body = []
    close()
    return page or name.removesuffix(".md"), out


class Index:
    """The docs' sections in an in-memory FTS5 table, title words counting four times body words."""

    def __init__(self, root, sig):
        self.root, self.sig, self.lock = root, sig, threading.Lock()
        self.pages, self.rows = {}, []
        self.con = sqlite3.connect(":memory:", check_same_thread=False)
        self.con.execute(
            "CREATE VIRTUAL TABLE s USING fts5(title, body, page UNINDEXED, tokenize = 'porter unicode61 remove_diacritics 2')"
        )
        for path in sorted(root.glob("*.md")):
            if path.name in SKIP:
                continue
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:  # README and CHANGELOG link to the repository's root, which a container doesn't have
                continue
            title, parts = sections(path.name, text)
            self.pages[path.name] = {"page": path.name, "title": title, "url": DOCS_URL + path.name, "sections": []}
            for p in parts:
                row = {
                    "page": path.name,
                    "title": p["title"],
                    "url": DOCS_URL + path.name + (f"#{p['anchor']}" if p["anchor"] else ""),
                    "text": p["text"],
                }
                self.rows.append(row)
                self.pages[path.name]["sections"].append(row)
                self.con.execute(
                    "INSERT INTO s (rowid, title, body, page) VALUES (?, ?, ?, ?)", (len(self.rows), row["title"], row["text"], path.name)
                )
        self.con.commit()

    def search(self, query, page=None, k=TOP):
        words = chat.keywords(query, 8)
        if not words:
            return []
        expr = " OR ".join('"' + w.replace('"', "") + '"' for w in words)
        sql = "SELECT rowid FROM s WHERE s MATCH ?" + (" AND page = ?" if page else "") + " ORDER BY bm25(s, 4.0, 1.0) LIMIT ?"
        with self.lock:
            try:
                ids = [r[0] for r in self.con.execute(sql, (expr, page, k) if page else (expr, k))]
            except sqlite3.OperationalError:  # a word FTS5 can't take as written
                return []
        return [self.rows[i - 1] for i in ids]


_INDEX = {}
_LOCK = threading.Lock()


def _signature(root):
    return tuple((p.name, p.stat().st_mtime_ns, p.stat().st_size) for p in sorted(root.glob("*.md")) if p.exists())


def index():
    """The docs' index, built again when a page changed; None without docs."""
    root = folder()
    if root is None:
        return None
    sig = _signature(root)
    with _LOCK:
        got = _INDEX.get("docs")
        if got is None or got.root != root or got.sig != sig:
            got = _INDEX["docs"] = Index(root, sig)
    return got


def page_named(ix, name):
    """A page by its file name, with or without .md, or by its title, in any case."""
    want = (name or "").strip().lower().removeprefix("docs/")
    for p in ix.pages.values():
        if want in (p["page"].lower(), p["page"].lower().removesuffix(".md"), p["title"].lower()):
            return p
    return None


def _cut(text, n):
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def docs(query=None, page=None):
    """What the docs say: the sections best matching `query` (in `page` only, when given), with the docs' contents
    (every page by its title: what Lens does, at a glance), or a page read whole. {"sections": [{page, title, url,
    text}], "contents", "more": the headings of a long page's sections not read, to look up with a query and the page};
    {"missing": ...} without docs."""
    ix = index()
    if ix is None:
        return {"missing": f"this server doesn't have Lens's docs; they are at {DOCS_URL}"}
    contents = {x["page"].removesuffix(".md"): x["title"] for x in ix.pages.values()}
    p = page_named(ix, page) if page else None
    if page and not p:
        return {"sections": [], "error": f"no page {page} in the docs: pick one of the contents", "contents": contents}
    found = [s | {"text": _cut(s["text"], SECTION_CHARS)} for s in ix.search(query, p["page"] if p else None)] if query else []
    if found or not p:  # nothing found on a page asked for by name: that page, read whole
        return {"sections": found, "contents": contents}
    out, left, more = [], PAGE_CHARS, []
    for s in p["sections"]:
        if left > 200:
            out.append(s | {"text": _cut(s["text"], left)})
            left -= len(s["text"])
        else:
            more.append(s["title"].split(" › ", 1)[-1])
    if len(more) > MORE:
        more = more[:MORE] + [f"and {len(more) - MORE} more"]
    return {"sections": out} | ({"more": more} if more else {})


def _on(cfg, section, key="enabled"):
    return bool((cfg.get(section) or {}).get(key))


def server(db, cfg, web, admin=False, readable=(), editable=()):
    """This server as the person asking may know it: its version and addresses, how apps sign in, what is turned on
    (with the settings page for each; admins change them), and their own access. Nothing secret: no keys, tokens or
    model server addresses."""
    from app.email import mail_enabled

    web = (web or "").rstrip("/")
    remote = tunnel.status(db, cfg) if (tunnel.conf(cfg).get("mode") or "off") != "off" else {}
    names = store.space_names(db)
    # what people ask about, and the settings page each is on (after /settings/)
    on = {
        "model_provider": (llm.configured(cfg), "llm"),
        "search_by_meaning": (semantic.configured(cfg), "search"),
        "local_model": (_on(cfg, "local_llm"), "local-model"),
        "assistant_tools": (bool(cfg["ai"].get("tools")), "ai"),
        "assistant_extensions": (bool(cfg["ai"].get("extensions", True)), "ai"),
        "assistant_in_your_browser": (browser.enabled(cfg), "ai"),
        "chat_rooms_through_matterbridge": (_on(cfg, "bridge"), "bridge"),
        "matterbridge_run_by_lens": (_on(cfg, "matterbridge", "run"), "bridge"),
        "telegram": (_on(cfg, "telegram"), "telegram"),
        "slack": (_on(cfg, "slack"), "slack"),
        "assistant_email": (_on(cfg, "mailbox"), "mailbox"),
        "sending_email": (mail_enabled(cfg), "mail"),
        "remote_access_tunnel": (bool(remote), "remote-access"),
        "https": (https.enabled(cfg), "https"),
        "apps_sign_in_with_oauth": (oauth.enabled(cfg), "tokens"),
        "sensors_mqtt_syslog": (_on(cfg, "sensors"), "sensors"),
        "file_shares_s3_webdav": (_on(cfg, "shares"), "shares"),
        "nfs_share": (_on(cfg, "nfs"), "nfs"),
        "smb_share": (_on(cfg, "smb"), "smb"),
        "file_encryption": (_on(cfg, "encryption", "files"), "encryption"),
        "scheduled_backups": (bool((cfg.get("backups") or {}).get("schedule")), "backups"),
        "update_checks": (_on(cfg, "updates", "check"), "updates"),
        "telemetry": (_on(cfg, "telemetry"), "telemetry"),
        "prometheus_metrics": (prometheus.enabled(cfg), "telemetry"),
        "usage_analytics_to_google": (_on(cfg, "analytics"), "analytics"),
    }
    return store.clean(
        {
            "version": __version__,
            "addresses": store.clean(
                {
                    "web_app": web,
                    # MCP clients (Claude, ChatGPT, Cursor...) add this as a remote server and sign in through Lens; one
                    # that can't sends an API token made on the API tokens page
                    "mcp": f"{web}/mcp",
                    "api": f"{web}/api/v1",
                    "api_tokens_page": f"{web}/account/tokens",
                    "remote_access": remote.get("url") if remote.get("connected") or tunnel.hostname(cfg) else None,
                    "https": https.public_url(cfg) or None,
                }
            ),
            "chat_model": cfg["llm"].get("model") if llm.configured(cfg) else None,
            "turned_on": {k: v[0] for k, v in on.items()},
            # where each is changed, for admins: the web app's /settings/<page>
            "settings_pages": {k: v[1] for k, v in on.items()} if admin else None,
            "settings": f"{web}/settings" if admin else None,
            "you": {
                "admin": bool(admin),
                "namespaces_you_read": sorted(names[s] for s in readable if s in names),
                "namespaces_you_edit": sorted(names[s] for s in editable if s in names),
            },
            "docs": DOCS_URL,
        }
    )


def answer(db, cfg, web, admin=False, readable=(), editable=(), query=None, page=None):
    """The lens_help tool's answer: this server, and what the docs say about `query` (or one `page` of them)."""
    return {"this_server": server(db, cfg, web, admin, readable, editable), "docs": docs(query, page)}
