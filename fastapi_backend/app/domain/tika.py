"""Apache Tika (tika.apache.org) as a document reader: the text and the description of about a thousand kinds of file,
read by a Tika server (docs/api.md#documents-and-images).

Off unless documents.tika_url names a Tika server: the `tika` service Lens's compose files can start next to it
(`--profile tika`, http://tika:9998), or one elsewhere. It has two uses:
- documents.tika_text (on): a document that neither Lens's converters nor anytopdf can make into a PDF here (an Office
  file on a server without LibreOffice, say) is read by Tika instead, as text alone: it has no pages to look at, but
  its words are searched, analyzed and asked about like any other's.
- documents.tika_metadata (on): what every document says about itself (its title, authors, language, dates, pages) is
  read by Tika too, as Dublin Core terms. Its title and date become the resource's while it's still named and dated
  after its file, and its authors and language its metadata's (metadata.defaults).

The document goes to the address the admin set and nowhere else: only http(s), with no user name or password in the
address, and no redirect is followed. It is sent as document.<type>, never by its own name. What Tika answers is
bounded (MAX_ANSWER) before Lens reads it, and only the Dublin Core terms Lens keeps (TERMS) are taken from it.
"""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import re
import urllib.error
import urllib.parse
import urllib.request

MAX_ANSWER = 64 << 20  # the most of Tika's answer that is read
MAX_TEXT = 20_000_000  # the most text kept of one document (and what's in it)
MAX_PARTS = 500  # what's in a document (attachments, embedded files) read with it, at most
CONTENT = ("tk:content", "X-TIKA:content")  # where Tika puts a part's text: 4.x, then 2.x and 3.x
FAILED = ("tk:exception:container", "x-tika:exception:runtime", "x-tika:exception:container")  # why it couldn't
# Dublin Core terms Lens keeps, and the Tika keys they are read from (the first one there)
TERMS = {
    "title": ("dc:title", "pdf:docinfo:title"),
    "creators": ("dc:creator", "meta:author", "Author"),
    "subject": ("dc:subject", "cp:subject"),
    "description": ("dc:description",),
    "publisher": ("dc:publisher",),
    "language": ("dc:language",),
    "created": ("dcterms:created", "meta:creation-date"),
    "modified": ("dcterms:modified",),
    "pages": ("xmpTPg:NPages", "meta:page-count"),
    "words": ("meta:word-count",),
    "format": ("Content-Type",),
}
LANG = re.compile(r"^[a-zA-Z]{2,3}(-[A-Za-z0-9]{2,8})*$")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A Tika server that answers with a redirect is refused: the document goes to the address set, never on."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "it redirected elsewhere, and Lens doesn't follow", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _opts(cfg):
    return cfg.get("documents") or {}


def url(cfg):
    """The Tika server's address, or None when Tika is off."""
    return (_opts(cfg).get("tika_url") or "").strip().rstrip("/") or None


def reads_text(cfg):
    """Whether Tika reads documents Lens can't make into PDFs here."""
    return bool(url(cfg)) and _opts(cfg).get("tika_text", True) is not False


def describes(cfg):
    """Whether Tika reads what every document says about itself."""
    return bool(url(cfg)) and _opts(cfg).get("tika_metadata", True) is not False


def _url(cfg):
    """The server's address, refused unless it is http(s) with a host and no user name or password."""
    got = url(cfg)
    u = urllib.parse.urlsplit(got or "")
    if u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
        raise ValueError("documents.tika_url is a Tika server's http(s) address, such as http://tika:9998")
    return got


def _call(cfg, method, path, body=None, headers=None, seconds=60):
    req = urllib.request.Request(_url(cfg) + path, data=body, method=method, headers=headers or {})
    return _OPENER.open(req, timeout=seconds)


def _bounded(r):
    raw = r.read(MAX_ANSWER + 1)
    if len(raw) > MAX_ANSWER:
        raise ValueError(f"Tika's answer ran past {MAX_ANSWER >> 20} MB")
    return raw


def _said(e):
    try:
        return (e.read(2000) or b"").decode("utf-8", "replace").strip().splitlines()[0][:300] or e.reason
    except (AttributeError, IndexError, OSError):
        return getattr(e, "reason", str(e))


def parts(cfg, path):
    """What Tika read in the file at `path`: [{key: value}], the document first, then what's in it (its /rmeta)."""
    path = pathlib.Path(path)
    seconds = _opts(cfg).get("convert_seconds") or 300
    headers = {
        "Accept": "application/json",
        "Content-Disposition": f"attachment; filename=document{path.suffix.lower()}",  # its kind, not its name
        "Content-Type": "application/octet-stream",
    }
    try:
        with _call(cfg, "PUT", "/rmeta/text", path.read_bytes(), headers, seconds) as r:
            raw = _bounded(r)
    except urllib.error.HTTPError as e:
        if e.code in (415, 422):
            raise ValueError(f"Tika can't read it ({_said(e)})") from None
        raise ValueError(f"the Tika server said {e.code}: {_said(e)}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"the Tika server can't be reached ({getattr(e, 'reason', e)})") from None
    try:
        got = json.loads(raw)
    except ValueError:
        raise ValueError("the Tika server didn't answer like Tika") from None
    if not isinstance(got, list) or not got or not all(isinstance(p, dict) for p in got):
        raise ValueError("the Tika server didn't answer like Tika")
    return got[:MAX_PARTS]


def _first(part, keys):
    for k in keys:
        v = part.get(k)
        if isinstance(v, list):
            v = [str(x).strip() for x in v if str(x).strip()]
            if v:
                return v
        elif v is not None and str(v).strip():
            return str(v).strip()
    return None


def _when(v):
    try:
        d = dt.datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).astimezone(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _count(v):
    try:
        n = int(str(v).strip())
    except ValueError:
        return None
    return n if n >= 0 else None


def described(part):
    """What a document says about itself, as the Dublin Core terms Lens keeps: {title, creators, subject, description,
    publisher, language, created, modified, pages, words, format}, those it says."""
    out = {}
    for term, keys in TERMS.items():
        v = _first(part, keys)
        if v is None:
            continue
        if term == "creators":
            names = v if isinstance(v, list) else re.split(r"\s*;\s*", v)
            v = list(dict.fromkeys(n[:200] for n in names if n))[:20]
        elif isinstance(v, list):
            v = v[0]
        if term in ("created", "modified"):
            v = _when(v)
        elif term in ("pages", "words"):
            v = _count(v)
        elif term == "language":
            v = v if LANG.match(v) else None
        elif term == "format":
            v = v.split(";")[0].strip().lower()[:100]
        elif isinstance(v, str):
            v = v[: 2000 if term == "description" else 500]
        if v not in (None, "", []):
            out[term] = v
    return out


def text_of(found):
    """All the text Tika read: the document's, then each thing in it, at most MAX_TEXT characters."""
    out, size = [], 0
    for part in found:
        t = _first(part, CONTENT)
        t = "\n".join(t) if isinstance(t, list) else t
        if not t:
            continue
        t = t.strip()[: MAX_TEXT - size]
        out.append(t)
        size += len(t)
        if size >= MAX_TEXT:
            break
    return "\n\n".join(out)


def read(cfg, path):
    """The text Tika reads in the file at `path`, and what it says about itself: {text, described}. ValueError when
    the server can't be reached or can't read it."""
    found = parts(cfg, path)
    text = text_of(found)
    broke = next((v for k, v in found[0].items() if k.lower().startswith(FAILED)), None)
    if broke and not text:
        said = str(broke[0] if isinstance(broke, list) else broke).strip().splitlines()[0]
        raise ValueError(f"Tika couldn't read it ({said.split(': ', 1)[-1][:300]})")
    return {"text": text, "described": described(found[0])}


def version(cfg):
    """The Tika server's version (`Apache Tika 4.0.0`); ValueError saying what's wrong when it doesn't answer as one."""
    if not url(cfg):
        raise ValueError("set the Tika server's address first (documents.tika_url)")
    try:
        with _call(cfg, "GET", "/version", headers={"Accept": "text/plain"}, seconds=10) as r:
            said = r.read(200).decode("utf-8", "replace").strip()
    except urllib.error.HTTPError as e:
        raise ValueError(f"it said {e.code}: {_said(e)}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"it can't be reached ({getattr(e, 'reason', e)})") from None
    if not said.startswith("Apache Tika"):
        raise ValueError("it answered, but not like a Tika server")
    return said
