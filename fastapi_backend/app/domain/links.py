"""Lists of links, read for a bulk web import (docs/api.md#web-pages): one address per line, a browser's bookmark
export (the HTML file every browser writes), or Chrome's own Bookmarks file (JSON). Each link becomes a web page
captured as a document (webcapture.py); the bookmark folders it was in can become its tags.

Only what a link says is read here: nothing is fetched. Addresses are checked when each is imported.
"""

from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser

MAX = 500  # links in one import, at most
URL = re.compile(r"https?://[^\s<>\"']+", re.I)
SKIP = {"bookmarks bar", "bookmarks menu", "bookmarks toolbar", "other bookmarks", "mobile bookmarks", "favorites bar"}


class _Bookmarks(HTMLParser):
    """The Netscape bookmark file: <DT><H3>folder</H3><DL> … <DT><A HREF="…">title</A> … </DL>."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.path, self.pending, self.text, self.href = [], [], None, None, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "h3":
            self.text = ""
        elif tag == "a":
            self.href, self.text = a.get("href"), ""
        elif tag == "dl":
            self.path.append(self.pending)
            self.pending = None

    def handle_endtag(self, tag):
        if tag == "h3" and self.text is not None:
            self.pending, self.text = " ".join(self.text.split()), None
        elif tag == "a" and self.href is not None:
            self.out.append((self.href, " ".join((self.text or "").split()), [p for p in self.path if p]))
            self.href, self.text = None, None
        elif tag == "dl" and self.path:
            self.path.pop()

    def handle_data(self, data):
        if self.text is not None:
            self.text += data


def _chrome(node, path, out):
    if not isinstance(node, dict):
        return
    if node.get("type") == "url":
        out.append((node.get("url"), node.get("name") or "", list(path)))
    for child in node.get("children") or []:
        _chrome(child, [*path, node.get("name")] if node.get("type") == "folder" and node.get("name") else path, out)


def read(raw):
    """The links in `raw` (bytes or text), in order and without repeats: [{url, title, folders}]. The folders are the
    bookmark folders it was in, outermost first, without the browser's own (Bookmarks bar, Other bookmarks)."""
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw or "")
    text = text.lstrip("﻿")
    found = []
    head = text.lstrip()[:200].lower()
    if head.startswith("{"):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if isinstance(data, dict) and isinstance(data.get("roots"), dict):
            for root in data["roots"].values():
                if isinstance(root, dict):
                    for child in root.get("children") or []:
                        _chrome(child, [], found)
    if not found and ("<!doctype netscape-bookmark-file" in head or "<dl" in text[:5000].lower() or "<a " in head):
        p = _Bookmarks()
        p.feed(text)
        found = p.out
    if not found:
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            m = URL.search(line)
            if m:
                rest = (line[: m.start()] + line[m.end() :]).strip(" -–|:\t")
                found.append((m.group(0).rstrip(".,;)]"), rest, []))
    out, seen = [], set()
    for url, title, folders in found:
        url = html.unescape(str(url or "")).strip()
        if not url.lower().startswith(("http://", "https://")) or url in seen:
            continue
        seen.add(url)
        folders = [f for f in folders if f and f.casefold() not in SKIP]
        out.append({"url": url, "title": (title or "").strip()[:200] or None, "folders": folders})
    return out


def tags(folders, most=5, width=40):
    """A link's bookmark folders as tags: the innermost few, each cut to a tag's length."""
    return [" ".join(f.split())[:width].strip() for f in folders[-most:] if f.strip()]
