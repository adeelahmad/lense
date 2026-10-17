"""A conversation, or one message of it, as a file to keep or hand on (docs/assistant.md, "Copy and export").

- Markdown: the title and what the conversation draws on, then each question and answer, its citations as numbered
  footnotes (the asset, the time or page, and a link back to it in Lens; a web page's address), and the tools an
  answer used as a short "Looked up" list.
- JSON: the conversation as a tree in OpenAI's chat message format, as other tools and trainers read it: `mapping`
  holds each message by id with its `parent` and `children`, and `current_node` is the version showing. Each node's
  `message` is {role, content}; an answer that used tools has them before it in `tool_messages` (an assistant message
  with `tool_calls`, then a `tool` message per call carrying its summary: raw results aren't kept). What only Lens
  knows (citations, the answer's record, files and the page it was asked from) is under `lens`. Dublin Core terms
  describe the conversation. `messages()` gives one version as a single {"messages": [...]} object, the shape
  fine-tuning reads. The schema is docs/schemas/chat-export.schema.json.
- PDF: the Markdown on a page, printed by Chromium where it's installed, else by anytopdf (here or on a conversion
  node, fetched on first use), else by LibreOffice; Unavailable says why when none can run here.

Nothing here reads the database or decides access: the caller hands over the messages the reader may see, citations
they can't read already left out. No model is called.
"""

from __future__ import annotations

import json
import pathlib
import re
import tempfile

from . import __version__, anytopdf, convert, render, store

FORMAT = "lens.chat-export/1"
DC = "http://purl.org/dc/terms/"
QUOTE = 280  # the most of a citation's text a footnote quotes
CITE = re.compile(r"\[(\d+)\](?!\()")  # [n], but not a link's text


class Unavailable(RuntimeError):
    """A PDF can't be made here: no Chromium, anytopdf or LibreOffice can run."""


def _when(value):
    """An ISO time as "2026-10-16 22:43 UTC" (times are kept in UTC)."""
    return f"{value[:16].replace('T', ' ')} UTC" if value else ""


def _line(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _cut(text, n=QUOTE):
    text = _line(text)
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


def link(web, p):
    """Where a citation can be read: a web page's address, or the asset's page in Lens at its moment."""
    if p.get("source") == "web" or p.get("url"):
        return p.get("url")
    if p.get("recording_id") is None:
        return None
    at = f"?t={int(p['t0']) // 1000}" if p.get("page") is None and (p.get("t0") or 0) >= 1000 else ""
    return f"{web}/resources/{p['recording_id']}{at}"


def citation(web, p):
    """A citation as exports give it: a web page's {n, source: web, url, title, site, fetched_at, text}, or an
    archive moment's {n, source, recording_id, title, namespace, time, page, speaker, text, url}."""
    if p.get("source") == "web" or p.get("url"):
        keep = ("n", "url", "title", "site", "fetched_at", "text", "used")
        return store.clean({"source": "web", **{k: p.get(k) for k in keep}})
    keep = ("n", "source", "recording_id", "title", "namespace", "recorded_at", "time", "page", "speaker", "text", "used")
    return store.clean({**{k: p.get(k) for k in keep}, "source": p.get("source") or "said", "url": link(web, p)})


def scope_words(scope):
    """What a conversation draws on, in words."""
    s = scope or {}
    parts = []
    if s.get("namespaces"):
        parts.append(("namespace " if len(s["namespaces"]) == 1 else "namespaces ") + ", ".join(s["namespaces"]))
    for key, one, many in (
        ("recordings", "asset", "assets"),
        ("collections", "collection", "collections"),
        ("speakers", "person", "people"),
    ):
        if n := len(s.get(key) or []):
            parts.append(f"{n} {one if n == 1 else many}")
    if s.get("from") or s.get("to"):
        parts.append(" ".join(x for x in (f"from {s['from']}" if s.get("from") else "", f"to {s['to']}" if s.get("to") else "") if x))
    return "; ".join(parts) or "everything you can read"


def _versions(m):
    v = m.get("versions") or {}
    return f", version {v['index']} of {v['count']}" if (v.get("count") or 0) > 1 else ""


# ---------- Markdown ----------
def _cited(m):
    """The citations an answer's text cites, by number."""
    said = {int(n) for n in CITE.findall(m.get("content") or "")}
    return {p["n"]: p for p in m.get("passages") or [] if p.get("n") in said}


def _question(web, m):
    out = [f"## You, {_when(m.get('created_at'))}{_versions(m)}", ""]
    ctx = m.get("context") or {}
    if ctx.get("url"):
        title = _line(ctx.get("title")) or ctx["url"]
        out += [f"*Asked from [{title}]({web}{ctx['url']})*", ""]
        if ctx.get("selection"):
            out += ["> " + _cut(ctx["selection"], 1000), ""]
    out += [m.get("content") or "", ""]
    if files := [a.get("filename") for a in m.get("attachments") or [] if a.get("filename")]:
        out += ["Attached: " + ", ".join(files), ""]
    return out


def _answer(web, m):
    model = (m.get("meta") or {}).get("model") or m.get("model")
    out = [f"## Lens{f', {model}' if model else ''}, {_when(m.get('created_at'))}{_versions(m)}", ""]
    cited = _cited(m)
    text = CITE.sub(lambda x: f"[^{m['id']}-{x.group(1)}]" if int(x.group(1)) in cited else x.group(0), m.get("content") or "")
    if text.strip():
        out += [text, ""]
    if m.get("stopped"):
        out += ["*Stopped before it finished.*", ""]
    if m.get("notice"):
        out += [f"> {_line(m['notice'])}", ""]
    if m.get("error"):
        out += [f"> Error: {_line(m['error'])}", ""]
    if steps := m.get("steps") or []:
        out += ["Looked up:", ""]
        out += [f"- `{s.get('tool')}`" + (f": {_cut(s['summary'], 200)}" if s.get("summary") else "") for s in steps]
        out.append("")
    for n, p in sorted(cited.items()):
        where = link(web, p)
        if p.get("source") == "web" or p.get("url"):
            name = _line(p.get("title")) or p.get("site") or where
            said = ", ".join(x for x in (f"[{name}]({where})" if where else name, p.get("site") if p.get("site") != name else None) if x)
            read = f", read {p['fetched_at'][:10]}" if p.get("fetched_at") else ""
            out.append(f"[^{m['id']}-{n}]: {said}{read}: “{_cut(p.get('text'))}”")
        else:
            about = ", ".join(x for x in (_line(p.get("title")) or "Untitled", p.get("time"), p.get("speaker")) if x)
            out.append(f"[^{m['id']}-{n}]: {about}: “{_cut(p.get('text'))}”" + (f" <{where}>" if where else ""))
    if cited:
        out.append("")
    return out


def markdown(chat, msgs, web, one=False):
    """`msgs` (oldest first, citations already filtered) as Markdown: the conversation's title and details, then each
    message. `one`: the export of a single message (and the question it answers)."""
    out = [f"# {_line(chat.get('title')) or 'Conversation'}", ""]
    out.append(f"- Conversation: <{web}/chat/{chat['id']}>")
    if chat.get("created_at"):
        out.append(
            f"- Started {_when(chat['created_at'])}" + (f", last message {_when(chat['updated_at'])}" if chat.get("updated_at") else "")
        )
    out.append(f"- Draws on {scope_words(chat.get('scope'))}")
    if any((m.get("versions") or {}).get("count", 1) > 1 for m in msgs):
        out.append(
            "- "
            + ("One message" if one else "The version showing")
            + "; the conversation has other versions (the JSON export holds them all)"
        )
    out += [f"- Exported {_when(store.now())} from Lens {__version__}", ""]
    for m in msgs:
        out += ["---", "", *(_question(web, m) if m.get("role") == "user" else _answer(web, m))]
    return "\n".join(out).rstrip() + "\n"


# ---------- JSON ----------
def _tool_messages(m):
    """An answer's tool calls as OpenAI chat messages: one assistant message calling them all, then one `tool` message
    each, carrying the step's summary (what Lens keeps of its result)."""
    steps = m.get("steps") or []
    if not steps:
        return []
    calls = [
        {
            "id": f"call_{m['id']}_{i}",
            "type": "function",
            "function": {"name": s.get("tool") or "", "arguments": json.dumps(s.get("args") or {}, ensure_ascii=False)},
        }
        for i, s in enumerate(steps, 1)
    ]
    out = [{"role": "assistant", "content": None, "tool_calls": calls}]
    out += [{"role": "tool", "tool_call_id": c["id"], "content": s.get("summary") or ""} for c, s in zip(calls, steps)]
    return out


def _message(m):
    return {"role": m["role"], "content": m.get("content") or ""}


def _lens(web, m):
    """What only Lens knows of a message."""
    keep = ("model", "stopped", "notice", "error", "check", "context", "versions")
    out = {k: m.get(k) for k in keep if m.get(k)}
    if m.get("attachments"):
        out["attachments"] = [store.clean({k: a.get(k) for k in ("filename", "size", "recording")}) for a in m["attachments"]]
    if m.get("passages"):
        out["citations"] = [citation(web, p) for p in m["passages"]]
    if m.get("meta"):
        out["meta"] = m["meta"]
    return out


def document(chat, msgs, web, creator=None):
    """`msgs` (any versions, oldest first, citations already filtered) as the JSON export: Dublin Core terms, then the
    tree (`mapping`, `current_node`). Parents and children are among `msgs` only."""
    ids = {m["id"] for m in msgs}
    children = {i: [] for i in ids}
    for m in msgs:
        if m.get("parent") in ids:
            children[m["parent"]].append(str(m["id"]))
    head = chat.get("head") if chat.get("head") in ids else (msgs[-1]["id"] if msgs else None)
    mapping = {}
    for m in msgs:
        node = {
            "id": str(m["id"]),
            "parent": str(m["parent"]) if m.get("parent") in ids else None,
            "children": children[m["id"]],
            "created_at": m.get("created_at"),
            "message": _message(m),
        }
        if m["role"] == "assistant" and (before := _tool_messages(m)):
            node["tool_messages"] = before
        node["lens"] = _lens(web, m)
        mapping[str(m["id"])] = node
    return {
        "@context": {"dc": DC},
        "format": FORMAT,
        "dc:title": chat.get("title") or "Conversation",
        "dc:created": chat.get("created_at"),
        "dc:modified": chat.get("updated_at"),
        **({"dc:creator": creator} if creator else {}),
        "dc:identifier": f"{web}/chat/{chat['id']}",
        "dc:format": "application/json",
        "dc:publisher": f"Lens {__version__}",
        "id": str(chat["id"]),
        "title": chat.get("title") or "Conversation",
        "scope": chat.get("scope") or {},
        "model": chat.get("model"),
        "exported_at": store.now(),
        "current_node": str(head) if head is not None else None,
        "mapping": mapping,
    }


def messages(msgs):
    """One version (oldest first) as {"messages": [...]}: OpenAI chat messages, an answer's tool calls before it."""
    out = []
    for m in msgs:
        if m["role"] == "assistant":
            out += _tool_messages(m)
        out.append(_message(m))
    return {"messages": out}


# ---------- PDF ----------
def printer(cfg):
    """What prints the PDF here: chromium, anytopdf (installed, on a conversion node, or fetched on first use) or
    libreoffice; None when nothing can."""
    if convert.chromium(cfg):
        return "chromium"
    if anytopdf.available(cfg) or (anytopdf.mode(cfg) != "lens" and anytopdf.archive()):
        return "anytopdf"
    return "libreoffice" if convert.soffice(cfg) else None


def pdf(cfg, title, text):
    """The Markdown `text` on a page, printed to a PDF: bytes. Unavailable when nothing can print it here."""
    how = printer(cfg)
    if how is None:
        raise Unavailable(
            "a PDF needs Chromium, anytopdf or LibreOffice on the server, and anytopdf is switched off or has no build "
            "for it (Settings › Documents); print the conversation from the web app instead"
        )
    page = convert.markdown_page(text, title)
    work = pathlib.Path(cfg["data_dir"]) / "tmp"
    work.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work, prefix="chat-export-") as tmp:
        out = pathlib.Path(tmp) / "conversation.pdf"
        if how == "anytopdf":
            if not anytopdf.available(cfg):
                anytopdf.fetch(cfg)
            doc = pathlib.Path(tmp) / "conversation.html"
            doc.write_text(page, encoding="utf-8")
            anytopdf.to_pdf(cfg, doc, out)
        else:
            convert.html_pdf(cfg, page, out)
        return out.read_bytes()


def file_name(title, ext, message=None):
    """The download's file name: the title's slug, and the message's id for one message."""
    base = render.slug(title) if title else "conversation"
    return f"{base if base != 'recording' else 'conversation'}{f'-message-{message}' if message else ''}.{ext}"
