"""Web pages captured as documents (docs/api.md#web-pages).

Someone gives a web address; Lens keeps the page as it was then, as a PDF, and reads it like any document. A link to a
PDF is kept as it is; any other page is printed by headless Chromium, its scripts run so that it looks as it does in a
browser. Both go through netguard's proxy, which reaches public addresses only (and the networks in
documents.web_networks, for an intranet), on the web's own ports, with every address it connects to checked first.
The capture is the resource's file, data_dir/web/<resource>/<name>.pdf (a PDF link's own name, else the page's
title); capturing happens in its transcribe step, once: a resource transcribed again keeps the page it captured.
"""

from __future__ import annotations

import ipaddress
import pathlib
import shutil
import urllib.error
import urllib.parse
import urllib.request

from . import convert, ingest, netguard, render, store

R = store.R
UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Lens/1.0 (archive capture)"
MB = 2**20


def networks(cfg):
    """The private networks an admin allowed pages to be captured from (documents.web_networks), besides the public
    internet."""
    return [ipaddress.ip_network(str(n), strict=False) for n in (cfg.get("documents") or {}).get("web_networks") or []]


def check_url(cfg, url):
    """A web address to capture, tidied: http or https, a host that resolves only to addresses that may be reached,
    on the web's ports. ValueError, saying why, otherwise."""
    url = (url or "").strip()
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("give a web address that starts with http:// or https://")
    if u.username or u.password:
        raise ValueError("a web address with a user name or password in it can't be captured")
    try:
        port = u.port or (443 if u.scheme == "https" else 80)
    except ValueError:
        raise ValueError("that web address has a port that isn't a number") from None
    if port not in netguard.PORTS:
        raise ValueError(f"only the web's own ports (80 and 443) can be captured, not {port}")
    nets = networks(cfg)
    netguard.resolve(u.hostname, port, lambda ip: netguard.public_ip(ip, nets))
    return urllib.parse.urlunsplit((u.scheme, u.netloc.lower(), u.path or "/", u.query, ""))


def placeholder(url):
    """What a page is called until it's captured and its title read: its address without the scheme."""
    u = urllib.parse.urlsplit(url)
    rest = (u.path if u.path != "/" else "") + (f"?{u.query}" if u.query else "")
    return (u.netloc + rest)[:200]


def create(db, sid, url, title=None, collection=None, by=None):
    """A resource for the page at `url` in namespace `sid` (in `collection`, else the namespace's default), to be
    captured when its pipeline runs. Returns its id."""
    rid = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", rid),
        d=store.clean(
            {
                "space": sid,
                "collection": store.home(db, sid, collection),
                "source": "document",
                "media": {"kind": "document"},
                "web": {"url": url},
                "title": (title or "").strip()[:200] or placeholder(url),
                "recorded_at": store.now(),
                "status": "new",
                "created_at": store.now(),
                "created_by": by,
            }
        ),
    )
    return rid


def folder(cfg, rid):
    """Where a resource's captured page is kept."""
    return pathlib.Path(cfg["data_dir"]) / "web" / str(int(rid))


def file_name(url, title):
    """What a captured page's PDF is called: a PDF link's own name (annual-report.pdf), else the page's title, else its
    address (harbour-news.pdf)."""
    last = pathlib.PurePosixPath(urllib.parse.unquote(urllib.parse.urlsplit(url).path)).name
    if last.lower().endswith(".pdf") and render.slug(last[:-4]) != "recording":
        return f"{render.slug(last[:-4])}.pdf"
    return f"{render.slug(title or placeholder(url))}.pdf"


def capture(cfg, url, out):
    """The page at `url` kept as the PDF `out`: a PDF as it is, anything else printed by Chromium. Returns {how
    ("pdf" or "printed"), final (the address it ended at), title}. ValueError when it can't be reached or read;
    convert.Unavailable without Chromium for a page."""
    seconds = (cfg.get("documents") or {}).get("convert_seconds") or 300
    most = cfg["uploads"]["max_mb"] * MB
    with netguard.Guard(forward=True, networks=networks(cfg), max_bytes=most) as guard:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,application/pdf;q=0.9,*/*;q=0.8"})
        try:
            with netguard.opener(guard).open(req, timeout=30) as r:
                final, kind = r.geturl(), r.headers.get_content_type()
                if kind == "application/pdf":
                    out.parent.mkdir(parents=True, exist_ok=True)
                    with open(out, "wb") as f:
                        shutil.copyfileobj(r, f, 1 << 20)
                    if out.stat().st_size >= most:
                        out.unlink(missing_ok=True)
                        raise ValueError(f"that PDF is larger than {cfg['uploads']['max_mb']} MB (uploads.max_mb)")
                    return {"how": "pdf", "final": final, "title": _title(out)}
        except urllib.error.HTTPError as e:
            raise ValueError(f"the page answered {e.code} {e.reason}") from None
        except (urllib.error.URLError, OSError) as e:
            why = getattr(e, "reason", e)
            raise ValueError(f"the page couldn't be reached ({why}){_refused(guard)}") from None
        exe = convert.chromium(cfg)
        if not exe:
            raise convert.Unavailable("capturing web pages needs Chromium on the server (the lens:full image)")
        convert.print_pdf(exe, guard, final, out, seconds, settle_ms=10000)
    return {"how": "printed", "final": final, "title": _title(out)}


def _refused(guard):
    return f"; refused: {guard.refused[0]}" if guard.refused else ""


def _title(pdf):
    try:
        from pypdf import PdfReader

        t = (PdfReader(str(pdf)).metadata or {}).get("/Title")
    except Exception:  # noqa: BLE001 - a title is a nicety
        return None
    t = str(t or "").strip()
    return t[:200] or None


def ensure(db, cfg, rid, rec, say):
    """A web page's file: captured now if it hasn't been (its transcribe step), else the one kept. Returns its path."""
    web = rec.get("web") or {}
    have = store.resolve_path(cfg, rec.get("path"))
    if have and pathlib.Path(have).is_file():
        return have
    got = capture(cfg, web["url"], folder(cfg, rid) / "capture.pdf")
    out = folder(cfg, rid) / file_name(got["final"], got["title"])
    (folder(cfg, rid) / "capture.pdf").replace(out)
    st = out.stat()
    patch = {
        "path": str(out),
        "size": st.st_size,
        "mtime": st.st_mtime,
        "fingerprint": ingest.fingerprint(out),
        "web": {**web, "final": got["final"], "captured_at": store.now(), "how": got["how"]},
    }
    if got["title"] and rec.get("title") == placeholder(web["url"]):  # still called by its address: the page's own
        patch["title"] = got["title"]
    db.q("UPDATE $r MERGE $d", r=R("recording", rid), d=patch)
    say(f"captured {got['final']}" + (" (a PDF)" if got["how"] == "pdf" else ""))
    return str(out)
