"""Documents that aren't PDFs, made into PDFs to read like one (docs/api.md#documents-and-images).

Word, PowerPoint and spreadsheet files (and their OpenDocument and RTF kin) are converted by LibreOffice. Plain text,
Markdown, saved web pages (HTML) and emails become a page of HTML first, cleaned of anything that could fetch or run,
and are printed to PDF by headless Chromium, or by LibreOffice where there's no Chromium. Neither may reach anything
while it works: Chromium goes through netguard's proxy, which serves the page and refuses the rest, and LibreOffice is
pointed at a proxy that isn't there. The resource keeps its own file; the PDF is its rendition,
data_dir/renditions/<resource>.pdf, which its pages are drawn and its text read from.

An email's attachments are kept as its files (role attachment); those Lens can read (documents, images, audio, video,
other emails) also become resources of their own beside it, each saying which email it came from
(documents.attachment_resources).
"""

from __future__ import annotations

import base64
import datetime as dt
import fcntl
import functools
import html
import json
import os
import pathlib
import re
import select
import shutil
import signal
import subprocess
import tempfile
import time
from email.utils import getaddresses, parsedate_to_datetime
from html.parser import HTMLParser

from . import anytopdf, netguard, store

R = store.R
OFFICE = {
    ".doc": "Word document",
    ".docx": "Word document",
    ".odt": "OpenDocument text",
    ".rtf": "RTF document",
    ".ppt": "PowerPoint presentation",
    ".pptx": "PowerPoint presentation",
    ".odp": "OpenDocument presentation",
    ".xls": "Excel spreadsheet",
    ".xlsx": "Excel spreadsheet",
    ".ods": "OpenDocument spreadsheet",
}
WORDS = {
    **OFFICE,
    ".pdf": "PDF",
    ".txt": "text file",
    ".text": "text file",
    ".md": "Markdown file",
    ".markdown": "Markdown file",
    ".mdx": "MDX file",
    ".html": "web page",
    ".htm": "web page",
    ".eml": "email",
    ".msg": "Outlook email",
}
TYPES = {
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".odt": "application/vnd.oasis.opendocument.text",
    ".rtf": "application/rtf",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".odp": "application/vnd.oasis.opendocument.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ods": "application/vnd.oasis.opendocument.spreadsheet",
    ".txt": "text/plain",
    ".text": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".mdx": "text/markdown",
    ".html": "text/html",
    ".htm": "text/html",
    ".eml": "message/rfc822",
    ".msg": "application/vnd.ms-outlook",
}
CHROMIUM = ("chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome", "headless_shell")
MAX_ATTACHMENTS = 100  # an email's attachments kept (and made resources), at most
INLINE_IMAGES = ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp")


class Unavailable(RuntimeError):
    """This server can't convert this kind of file (a program it needs isn't installed)."""


def ext_of(name):
    return pathlib.PurePosixPath(str(name or "")).suffix.lower()


def needs(name):
    """Whether a document has to be made into a PDF to be read (it's one of ours, and not a PDF)."""
    ext = ext_of(name)
    return ext in store.DOCUMENT_EXT and ext != ".pdf"


def word(name):
    """What a document is, for people: "Word document", "email", "PDF"."""
    return WORDS.get(ext_of(name), "document")


def content_type(name):
    return TYPES.get(ext_of(name))


def rendition_path(cfg, rid):
    return pathlib.Path(cfg["data_dir"]) / "renditions" / f"{int(rid)}.pdf"


# ---------- the programs that convert ----------
def soffice(cfg):
    return (cfg.get("documents") or {}).get("soffice") or shutil.which("soffice") or shutil.which("libreoffice")


def chromium(cfg):
    own = (cfg.get("documents") or {}).get("chromium")
    return own or next((p for p in (shutil.which(n) for n in CHROMIUM) if p), None)


def _has_msg():
    try:
        import extract_msg  # noqa: F401
    except ImportError:
        return False
    return True


def capabilities(cfg):
    """What this server can make into PDFs: Office files (LibreOffice), text, Markdown, saved web pages and emails
    (Chromium or LibreOffice), Outlook .msg emails (those, and the extract-msg package), and web pages captured from
    their address (Chromium). anytopdf does the same where Lens can't (anytopdf.py): emails, text and pages here, and
    Office files too on a conversion node."""
    by = None if anytopdf.mode(cfg) == "lens" else anytopdf.available(cfg)
    office, pages = bool(soffice(cfg) or by == "node"), bool(chromium(cfg) or soffice(cfg) or by)
    return {"office": office, "pages": pages, "msg": pages and _has_msg(), "web": bool(chromium(cfg))}


def unavailable(cfg, name):
    """Why this server can't read this document (None when it can): what it would need."""
    ext = ext_of(name)
    if ext not in store.DOCUMENT_EXT or ext == ".pdf":
        return None
    can = capabilities(cfg)
    if ext in OFFICE and not can["office"]:
        return f"converting {word(name)}s needs LibreOffice on the server (the lens:full image) or an anytopdf conversion node"
    if ext not in OFFICE and not can["pages"]:
        return f"converting {word(name)}s needs Chromium or LibreOffice on the server (the lens:full image)"
    if ext == ".msg" and not can["msg"]:
        return "reading Outlook .msg emails needs the extract-msg package on the server (pip install -e '.[msg]')"
    return None


def bootstrap(cfg):
    """The converters the server found, for Settings' startup values."""
    d = cfg.get("documents") or {}
    return {
        "soffice": d.get("soffice") or (shutil.which("soffice") or shutil.which("libreoffice") or "not installed"),
        "chromium": d.get("chromium") or (chromium(cfg) or "not installed"),
        "anytopdf": anytopdf.binary(cfg) or "not installed",
        "web_networks": [str(n) for n in d.get("web_networks") or []],
    }


def _last_said(text):
    """The last thing a program said that's worth repeating: an error or a warning if it gave one, not D-Bus noise."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    lines = [x.strip() for x in (text or "").splitlines() if x.strip() and "dbus" not in x.lower()]
    flagged = [x for x in lines if any(k in x for k in ("ERROR", "FATAL", "WARNING"))]
    return ((flagged or lines)[-1] if lines else "")[-200:]


def _run(argv, seconds, env=None, cwd=None):
    """Run a converter in a process group of its own, and leave nothing of it behind: soffice starts soffice.bin,
    which kept the pipes open (so a timeout waited forever) and could outlive the run."""
    p = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=cwd, start_new_session=True)
    try:
        out, err = p.communicate(timeout=seconds)
    except subprocess.TimeoutExpired:
        _kill_group(p)
        _, err = p.communicate()
        said = _last_said(err)
        raise ValueError(
            f"converting it took longer than {seconds} s (documents.convert_seconds){f'; it last said: {said}' if said else ''}"
        ) from None
    except BaseException:
        _kill_group(p)
        p.communicate()
        raise
    _kill_group(p)  # whatever it left running
    return subprocess.CompletedProcess(argv, p.returncode, out, err)


def _kill_group(p):
    try:
        os.killpg(p.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def office_pdf(cfg, src, out):
    """An Office, OpenDocument or RTF file made into a PDF by LibreOffice, offline and with a profile of its own."""
    exe = soffice(cfg)
    if not exe:
        raise Unavailable(unavailable(cfg, src) or "LibreOffice isn't installed")
    with tempfile.TemporaryDirectory(prefix="lens-office-") as tmp:
        doc = pathlib.Path(tmp) / f"document{ext_of(src)}"
        shutil.copyfile(src, doc)
        env = netguard.nowhere_env({**os.environ, "HOME": tmp})
        argv = [exe, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard"]
        argv += [f"-env:UserInstallation=file://{tmp}/profile", "--convert-to", "pdf", "--outdir", tmp, str(doc)]
        r = _run(argv, (cfg.get("documents") or {}).get("convert_seconds") or 300, env=env, cwd=tmp)
        made = pathlib.Path(tmp) / "document.pdf"
        if not made.is_file() or made.stat().st_size == 0:
            said = (r.stderr or r.stdout or "").strip().splitlines()
            raise ValueError(f"LibreOffice couldn't read it ({said[-1][:200] if said else f'exit {r.returncode}'})")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made), out)
    return "libreoffice"


RESOLVE_NOTHING = "MAP * ~NOTFOUND, EXCLUDE 127.0.0.1"  # every name fails to resolve; the proxy is 127.0.0.1


@functools.lru_cache(maxsize=8)
def no_sandbox(exe):
    """Whether Chromium has to run without its sandbox here: as root it won't start one, and some systems don't let it
    (a container without user namespaces, Ubuntu's AppArmor). Found once, by printing an empty page, so that no page
    decides whether it's printed without one."""
    if os.geteuid() == 0:
        return True
    with tempfile.TemporaryDirectory(prefix="lens-chromium-") as profile:
        argv = [exe, "--headless", "--disable-gpu", "--no-first-run", f"--user-data-dir={profile}"]
        try:
            _print([*argv, "--proxy-server=http://127.0.0.1:9"], no_bus(profile), "data:text/html,<p>Lens</p>", 60, 0)
        except (OSError, ValueError):
            return True
        return False


def no_bus(profile):
    """Chromium's environment: the server's, with no D-Bus to reach. Chromium asks D-Bus services (the network, power,
    screen savers) things on its main thread and waits for the answers, which on a system bus where one is slow to
    start can each take 25 s; and nothing on the server's buses is any page's business."""
    nowhere = f"unix:path={profile}/no-bus"
    return {**os.environ, "DBUS_SYSTEM_BUS_ADDRESS": nowhere, "DBUS_SESSION_BUS_ADDRESS": nowhere}


def print_pdf(exe, guard, url, out, seconds, settle_ms=5000):
    """Chromium, headless, printing `url` to the PDF `out` through `guard` (its only way out), with its sandbox where
    it can have one. It's driven over its DevTools pipe (as Puppeteer and Playwright drive it), not by --print-to-pdf,
    which some builds never finish (Chromium 154 among them)."""
    with tempfile.TemporaryDirectory(prefix="lens-chromium-") as profile:
        # WebRTC only through the proxy: full Chromium takes this from the profile, and ignores the switch below
        # (which the headless shell takes)
        (pathlib.Path(profile) / "Default").mkdir()
        (pathlib.Path(profile) / "Default" / "Preferences").write_text(
            json.dumps({"webrtc": {"ip_handling_policy": "disable_non_proxied_udp"}})
        )
        argv = [exe, "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check", "--disable-extensions"]
        argv += ["--disable-background-networking", "--disable-component-update", "--disable-sync", "--disable-default-apps"]
        argv += ["--mute-audio", "--hide-scrollbars", "--disable-dev-shm-usage", f"--user-data-dir={profile}", *guard.args()]
        # nothing around the proxy: no name lookups of its own, and WebRTC (which a page's script can start) only
        # through the proxy, never UDP straight to an address
        argv += [f"--host-resolver-rules={RESOLVE_NOTHING}", "--dns-prefetch-disable"]
        argv += ["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
        # nothing it could wait on: no keyring, crash reporter or casting (and no D-Bus: no_bus)
        argv += ["--password-store=basic", "--use-mock-keychain", "--disable-breakpad", "--disable-features=MediaRouter"]
        argv += ["--enable-logging=stderr"]  # what it says goes into the error when it fails
        argv += ["--no-sandbox"] if no_sandbox(exe) else []
        try:
            pdf = _print(argv, no_bus(profile), url, seconds, settle_ms)
        except ValueError as e:
            raise ValueError(f"{e}{_asked(guard)}") from None
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(pdf)


def _high(fd):
    """`fd` moved above the ones a child is given (0-4), so that giving them can't overwrite it."""
    moved = fcntl.fcntl(fd, fcntl.F_DUPFD_CLOEXEC, 10)
    os.close(fd)
    return moved


def _print(argv, env, url, seconds, settle_ms):
    """The PDF of `url`, printed by the Chromium `argv` starts, over its DevTools pipe (--remote-debugging-pipe: its
    commands on its fd 3, its answers and events on its fd 4, each JSON ending in a NUL). It prints once the page has
    loaded and gone quiet (nothing fetched for half a second), or `settle_ms` after it loaded."""
    to_read, to_write = map(_high, os.pipe())
    from_read, from_write = map(_high, os.pipe())
    said = tempfile.TemporaryFile()
    err = _high(os.dup(said.fileno()))
    deadline, buf, n = time.monotonic() + seconds, b"", 0
    file_actions = [
        (os.POSIX_SPAWN_OPEN, 0, os.devnull, os.O_RDONLY, 0),
        (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0),
        (os.POSIX_SPAWN_DUP2, err, 2),
        (os.POSIX_SPAWN_DUP2, to_read, 3),
        (os.POSIX_SPAWN_DUP2, from_write, 4),
    ]
    try:
        # its own process group, so that it goes with all its helpers
        try:
            pid = os.posix_spawn(argv[0], [*argv, "--remote-debugging-pipe", "about:blank"], env, file_actions=file_actions, setsid=True)
        except NotImplementedError:  # Pythons built against an old glibc (uv's own builds) have no setsid here
            pid = os.posix_spawn(argv[0], [*argv, "--remote-debugging-pipe", "about:blank"], env, file_actions=file_actions, setpgroup=0)
    finally:
        for fd in (to_read, from_write, err):
            os.close(fd)
    events: list[dict] = []

    def receive(by):
        """The next message, or None at the monotonic time `by`; EOFError when Chromium has gone."""
        nonlocal buf
        while b"\0" not in buf:
            left = by - time.monotonic()
            if left <= 0 or not select.select([from_read], [], [], left)[0]:
                return None
            chunk = os.read(from_read, 1 << 20)
            if not chunk:
                raise EOFError
            buf += chunk
        msg, buf = buf.split(b"\0", 1)
        return json.loads(msg)

    def call(method, params=None, session=None):
        nonlocal n
        n += 1
        msg = {"id": n, "method": method, "params": params or {}, **({"sessionId": session} if session else {})}
        os.write(to_write, json.dumps(msg).encode() + b"\0")
        while True:
            m = receive(deadline)
            if m is None:
                raise TimeoutError
            if m.get("id") == n:
                if "error" in m:
                    raise ValueError(f"Chromium couldn't {method}: {(m['error'] or {}).get('message')}")
                return m.get("result") or {}
            events.append(m)

    def happened(name, loader, by):
        """Whether the page's lifecycle reached `name` (load, networkIdle) by the monotonic time `by`."""
        while True:
            for e in events:
                p = e.get("params") or {}
                if e.get("method") == "Page.lifecycleEvent" and p.get("name") == name and p.get("loaderId") == loader:
                    return True
            m = receive(by)
            if m is None:
                return False
            events.append(m)

    try:
        target = call("Target.createTarget", {"url": "about:blank"})["targetId"]
        session = call("Target.attachToTarget", {"targetId": target, "flatten": True})["sessionId"]
        call("Page.enable", session=session)
        call("Page.setLifecycleEventsEnabled", {"enabled": True}, session=session)
        nav = call("Page.navigate", {"url": url}, session=session)
        if nav.get("errorText"):
            raise ValueError(f"Chromium couldn't load it ({nav['errorText']})")
        if not happened("load", nav.get("loaderId"), deadline):
            raise TimeoutError
        happened("networkIdle", nav.get("loaderId"), min(deadline, time.monotonic() + settle_ms / 1000))
        data = call("Page.printToPDF", {"printBackground": True, "preferCSSPageSize": True}, session=session)["data"]
        try:
            call("Browser.close")
        except (TimeoutError, EOFError, ValueError):
            pass
        return base64.b64decode(data)
    except TimeoutError:
        last = _last_said(_said(said))
        raise ValueError(
            f"converting it took longer than {seconds} s (documents.convert_seconds){f'; it last said: {last}' if last else ''}"
        ) from None
    except (EOFError, OSError, KeyError):
        raise ValueError(f"Chromium couldn't print it ({_last_said(_said(said)) or 'it stopped'})") from None
    finally:
        _end(pid, 5)
        for fd in (to_write, from_read):
            os.close(fd)
        said.close()


def _said(f):
    f.flush()
    f.seek(0)
    return f.read()


def _end(pid, seconds):
    """Chromium gone, with its helpers: it's given `seconds` to close by itself."""
    until = time.monotonic() + seconds
    while time.monotonic() < until:
        try:
            if os.waitpid(pid, os.WNOHANG)[0]:
                break
        except ChildProcessError:
            break
        time.sleep(0.05)
    try:
        os.killpg(pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _asked(guard):
    """What the page asked the proxy for, for an error saying why it couldn't be printed."""
    return f"; it asked for {', '.join(guard.asked[-5:])}" if guard.asked else "; it asked for nothing"


def _chromium_pdf(exe, page, out, seconds):
    with netguard.Guard({netguard.DOCUMENT_URL: (page.encode("utf-8"), "text/html; charset=utf-8")}) as guard:
        print_pdf(exe, guard, netguard.DOCUMENT_URL, out, seconds)


def html_pdf(cfg, page, out):
    """A page of HTML (made by page_html) printed to a PDF: by Chromium, else by LibreOffice. Returns which did."""
    seconds = (cfg.get("documents") or {}).get("convert_seconds") or 300
    exe = chromium(cfg)
    if exe:
        _chromium_pdf(exe, page, out, seconds)
        return "chromium"
    lo = soffice(cfg)
    if not lo:
        raise Unavailable("making a PDF of it needs Chromium or LibreOffice on the server (the lens:full image)")
    with tempfile.TemporaryDirectory(prefix="lens-page-") as tmp:
        doc = pathlib.Path(tmp) / "document.html"
        doc.write_text(page, encoding="utf-8")
        env = netguard.nowhere_env({**os.environ, "HOME": tmp})
        argv = [lo, "--headless", "--norestore", "--nolockcheck", "--nodefault", "--nofirststartwizard"]
        argv += [f"-env:UserInstallation=file://{tmp}/profile", "--convert-to", "pdf:writer_web_pdf_Export", "--outdir", tmp, str(doc)]
        r = _run(argv, seconds, env=env, cwd=tmp)
        made = pathlib.Path(tmp) / "document.pdf"
        if not made.is_file() or made.stat().st_size == 0:
            said = (r.stderr or r.stdout or "").strip().splitlines()
            raise ValueError(f"LibreOffice couldn't print it ({said[-1][:200] if said else f'exit {r.returncode}'})")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made), out)
    return "libreoffice"


# ---------- HTML that can't fetch or run anything ----------
KEEP = frozenset(
    "a abbr address article aside b bdi bdo big blockquote br caption center cite code col colgroup dd del details dfn div "
    "dl dt em figcaption figure font footer h1 h2 h3 h4 h5 h6 header hr i img ins kbd li main mark nav ol p pre q s samp "
    "section small span strike strong sub summary sup table tbody td tfoot th thead time tr tt u ul var".split()
)
DROP = frozenset(
    "script style head title template noscript iframe frame frameset object embed applet svg math canvas audio video "
    "select datalist map textarea button".split()
)
VOID = frozenset("br hr img col wbr link meta base input source track area param".split())
ATTRS = frozenset(
    "title dir lang align valign width height bgcolor color border cellpadding cellspacing colspan rowspan span start "
    "type alt face size style".split()
)
DATA_IMAGE = re.compile(r"^data:image/(png|jpe?g|gif|webp|bmp);base64,[A-Za-z0-9+/=\s]+$", re.I)
UNSAFE_STYLE = re.compile(r"url\s*\(|expression\s*\(|@import|behavior\s*:|binding\s*:|image-set\s*\(", re.I)


class _Clean(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if self.skip or tag in DROP:
            self.skip += tag in DROP and tag not in VOID
            return
        if tag not in KEEP:
            return  # unwrapped: its text stays
        kept = []
        for k, v in attrs:
            v = v or ""
            if k in ATTRS and not (k == "style" and UNSAFE_STYLE.search(v)):
                kept.append((k, v))
            elif k == "href" and tag == "a" and re.match(r"^(https?:|mailto:|#)", v.strip(), re.I):
                kept.append((k, v.strip()))
            elif k == "src" and tag == "img" and DATA_IMAGE.match(v.strip()):
                kept.append((k, re.sub(r"\s+", "", v)))
        if tag == "img" and not any(k == "src" for k, _ in kept):
            return  # an image from elsewhere: nothing to show
        self.out.append(f"<{tag}" + "".join(f' {k}="{html.escape(v, quote=True)}"' for k, v in kept) + ">")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag in DROP and tag not in VOID:
            self.skip = max(0, self.skip - 1)

    def handle_endtag(self, tag):
        if tag in DROP:
            self.skip = max(0, self.skip - 1)
            return
        if not self.skip and tag in KEEP and tag not in VOID:
            self.out.append(f"</{tag}>")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(html.escape(data, quote=False))


def clean_html(markup):
    """HTML with only what reads: text, structure, tables, inline styles without URLs, and images written into it."""
    p = _Clean()
    p.feed(markup or "")
    p.close()
    return "".join(p.out)


STYLE = """
@page { size: A4; margin: 18mm 16mm; }
html { background: #fff; }
body { margin: 0; color: #111; font: 11pt/1.5 "Liberation Serif", "DejaVu Serif", "Noto Serif", Georgia, serif; }
h1, h2, h3, h4, h5, h6, header, footer, th { font-family: "Liberation Sans", "DejaVu Sans", "Noto Sans", Arial, sans-serif; }
pre, code, kbd, samp, tt { font-family: "Liberation Mono", "DejaVu Sans Mono", "Noto Sans Mono", monospace; font-size: 9.5pt; }
pre { white-space: pre-wrap; overflow-wrap: anywhere; }
img { max-width: 100%; height: auto; }
.text { white-space: pre-wrap; overflow-wrap: anywhere; }
.md table { border-collapse: collapse; } .md td, .md th { border: 1px solid #bbb; padding: 3px 6px; }
.md blockquote { margin-left: 0; padding-left: 12px; border-left: 3px solid #ccc; color: #444; }
header.email { margin-bottom: 18px; padding-bottom: 10px; border-bottom: 1px solid #ccc; }
header.email h1 { font-size: 16pt; margin: 0 0 8px; }
header.email table { border-collapse: collapse; font-size: 10pt; }
header.email th { text-align: left; color: #555; padding: 1px 12px 1px 0; vertical-align: top; font-weight: 600; }
footer.email { margin-top: 24px; padding-top: 8px; border-top: 1px solid #ccc; font-size: 10pt; color: #333; }
"""


def page_html(title, body):
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{html.escape(title or '')}</title><style>{STYLE}</style></head><body>{body}</body></html>"
    )


def decode(data, declared=None):
    """Text from bytes: its byte order mark, the charset it declares, UTF-8, else Windows-1252."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", "replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", "replace")
    for enc in (declared, "utf-8"):
        if enc:
            try:
                return data.decode(enc)
            except (LookupError, UnicodeDecodeError):
                continue
    return data.decode("cp1252", "replace")


def text_page(text, title):
    return page_html(title, f'<div class="text">{html.escape(text)}</div>')


def markdown_page(text, title, mdx=False):
    import markdown

    if mdx:  # MDX: its imports, exports and components go; their text stays
        text = "\n".join(x for x in text.splitlines() if not re.match(r"^\s*(import|export)\s", x))
    body = markdown.markdown(text, extensions=["extra", "sane_lists"], output_format="html")
    return page_html(title, f'<div class="md">{clean_html(body)}</div>')


def _charset(raw):
    m = re.search(rb"<meta[^>]+charset\s*=\s*[\"']?([\w-]+)", raw[:4096], re.I)
    return m.group(1).decode("ascii", "ignore") if m else None


def web_page(raw):
    """A saved web page as a page of ours: its title and its body's text and structure."""
    text = decode(raw, _charset(raw))
    m = re.search(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    title = html.unescape(re.sub(r"\s+", " ", m.group(1))).strip() if m else ""
    b = re.search(r"<body[^>]*>(.*)</body>", text, re.I | re.S)
    return page_html(title, clean_html(b.group(1) if b else text)), title


# ---------- emails ----------
def _when(value):
    if isinstance(value, dt.datetime):
        d = value
    else:
        try:
            d = parsedate_to_datetime(str(value or ""))
        except (TypeError, ValueError, IndexError):
            return None
    if d is None:
        return None
    return (d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)).isoformat(timespec="seconds")


def _people(value):
    found = [f"{n} <{a}>" if n and a else (a or n) for n, a in getaddresses([str(value or "")])]
    return ", ".join(x for x in found if x)


def _safe_name(name, fallback):
    n = re.sub(r"[\x00-\x1f\x7f/\\]", "", str(name or "")).strip().strip(".")
    return n[:150] or fallback


def read_eml(path):
    """An .eml email: {subject, from, to, cc, date, html, text, parts: [{name, data, type, cid, attached}], inline:
    [{cid, type, data}]}: its attachments, and the images its HTML shows from inside it."""
    import email
    from email import policy

    with open(path, "rb") as f:
        msg = email.message_from_binary_file(f, policy=policy.default)
    return _message(msg)


def _message(msg):
    out = {
        "subject": str(msg.get("subject") or "").strip(),
        "from": _people(msg.get("from")),
        "to": _people(msg.get("to")),
        "cc": _people(msg.get("cc")),
        "date": _when(msg.get("date")),
        "html": None,
        "text": None,
        "parts": [],
        "inline": [],
    }
    for part in msg.walk():  # images the HTML shows by their Content-ID (cid:), wherever they sit
        cid = (part.get("Content-ID") or "").strip().strip("<>")
        if cid and not part.is_multipart() and part.get_content_type() in INLINE_IMAGES:
            out["inline"].append({"cid": cid, "type": part.get_content_type(), "data": part.get_payload(decode=True) or b""})
    body = msg.get_body(preferencelist=("html", "plain"))
    if body is not None:
        content = body.get_content()
        out["html" if body.get_content_type() == "text/html" else "text"] = content if isinstance(content, str) else ""
    if body is not None and out["html"] is not None:
        plain = msg.get_body(preferencelist=("plain",))
        out["text"] = plain.get_content() if plain is not None else None
    for i, part in enumerate(msg.iter_attachments(), 1):
        kind = part.get_content_type()
        if kind == "message/rfc822":
            inner = part.get_content()
            data = inner.as_bytes()
            name = _safe_name(str(inner.get("subject") or ""), f"message-{i}") + ".eml"
        else:
            data = part.get_payload(decode=True) or b""
            name = _safe_name(part.get_filename(), f"attachment-{i}")
        cid = (part.get("Content-ID") or "").strip().strip("<>") or None
        out["parts"].append(
            {"name": name, "data": data, "type": kind, "cid": cid, "attached": part.get_content_disposition() == "attachment"}
        )
    return out


def read_msg(path):
    """An Outlook .msg email, read by extract-msg, in read_eml's shape."""
    import extract_msg

    m = extract_msg.openMsg(str(path))
    try:
        raw_html = getattr(m, "htmlBody", None)
        out = {
            "subject": str(getattr(m, "subject", "") or "").strip(),
            "from": str(getattr(m, "sender", "") or ""),
            "to": str(getattr(m, "to", "") or ""),
            "cc": str(getattr(m, "cc", "") or ""),
            "date": _when(getattr(m, "date", None)),
            "html": decode(raw_html) if isinstance(raw_html, bytes) else raw_html,
            "text": getattr(m, "body", None),
            "parts": [],
            "inline": [],
        }
        for i, a in enumerate(getattr(m, "attachments", []) or [], 1):
            data = getattr(a, "data", None)
            if not isinstance(data, bytes):
                continue  # an Outlook item inside it (another .msg): left out
            name = _safe_name(getattr(a, "longFilename", None) or getattr(a, "shortFilename", None), f"attachment-{i}")
            cid = (getattr(a, "cid", None) or getattr(a, "contentId", None) or "").strip("<>") or None
            out["parts"].append({"name": name, "data": data, "type": getattr(a, "mimetype", None) or "", "cid": cid, "attached": True})
        return out
    finally:
        m.close()


def _size(n):
    return f"{n / 1024 / 1024:.1f} MB" if n >= 1024 * 1024 else f"{max(1, round(n / 1024))} KB"


def email_page(e):
    """An email as a page: its subject, who sent it to whom and when, its body (images written into it from its parts)
    and its attachments by name. Returns (page, the parts that are attachments)."""
    import base64

    shown = e.get("html") or ""
    used = set()
    for p in [*e.get("inline", []), *e["parts"]]:
        cid = p.get("cid")
        if cid and p["type"] in INLINE_IMAGES and f"cid:{cid}" in shown:
            uri = f"data:{p['type']};base64,{base64.b64encode(p['data']).decode('ascii')}"
            shown = shown.replace(f"cid:{cid}", uri)
            used.add(cid)
    # an image the email shows is part of it, not an attachment, unless it was attached as well
    attachments = [p for p in e["parts"] if p["attached"] or p.get("cid") not in used]
    rows = [("From", e["from"]), ("To", e["to"]), ("Cc", e["cc"])]
    if e["date"]:
        d = dt.datetime.fromisoformat(e["date"])
        rows.append(("Date", f"{d.day} {d:%B %Y, %H:%M} {d:%z}".strip()))
    head = "".join(f"<tr><th>{k}</th><td>{html.escape(v)}</td></tr>" for k, v in rows if v)
    body = clean_html(shown) if e.get("html") else f'<div class="text">{html.escape(e.get("text") or "")}</div>'
    foot = ""
    if attachments:
        items = "".join(f"<li>{html.escape(p['name'])} ({_size(len(p['data']))})</li>" for p in attachments)
        foot = f'<footer class="email"><b>Attachments</b><ul>{items}</ul></footer>'
    subject = e["subject"] or "(no subject)"
    page = page_html(subject, f'<header class="email"><h1>{html.escape(subject)}</h1><table>{head}</table></header>{body}{foot}')
    return page, attachments


# ---------- making the PDF ----------
def by_anytopdf(cfg, src):
    """Whether anytopdf makes this document's PDF (documents.converter): always when set to `anytopdf`, never when set
    to `lens`, and on `auto` only when Lens's own converters can't."""
    how = anytopdf.mode(cfg)
    if how == "lens" or not anytopdf.available(cfg):
        return False
    if how == "anytopdf":
        return ext_of(src) not in OFFICE or bool(soffice(cfg)) or anytopdf.available(cfg) == "node"
    ext = ext_of(src)
    return not soffice(cfg) if ext in OFFICE else not (chromium(cfg) or soffice(cfg))


def page_pdf(cfg, page, out, via_anytopdf):
    """A page of HTML made into a PDF: by Chromium (or LibreOffice), or by anytopdf, which reads its text."""
    if not via_anytopdf:
        return html_pdf(cfg, page, out)
    with tempfile.TemporaryDirectory(prefix="lens-page-") as tmp:
        p = pathlib.Path(tmp) / "page.html"
        p.write_text(page, encoding="utf-8")
        return anytopdf.to_pdf(cfg, p, out)


def to_pdf(cfg, src, out):
    """Make `out`, the PDF of the document at `src`. Returns what was learnt on the way: {by, title?, email?,
    attachments?}. Unavailable when the server can't; ValueError when the file can't be read."""
    why = unavailable(cfg, src)
    if why:
        raise Unavailable(why)
    ext, path, via = ext_of(src), pathlib.Path(src), by_anytopdf(cfg, src)
    if ext in OFFICE:
        return {"by": anytopdf.to_pdf(cfg, path, out) if via else office_pdf(cfg, path, out)}
    if ext in (".eml", ".msg"):
        e = read_msg(path) if ext == ".msg" else read_eml(path)
        page, attachments = email_page(e)
        info = {k: e[k] for k in ("subject", "from", "to", "cc", "date") if e.get(k)}
        return {"by": page_pdf(cfg, page, out, via), "title": e["subject"] or None, "email": info, "attachments": attachments}
    raw = path.read_bytes()
    if ext in store.PAGE_EXT:
        page, title = web_page(raw)
        return {"by": page_pdf(cfg, page, out, via), "title": title or None}
    text, title = decode(raw), path.stem
    page = markdown_page(text, title, ext == ".mdx") if ext in (".md", ".markdown", ".mdx") else text_page(text, title)
    return {"by": page_pdf(cfg, page, out, via)}


# ---------- an email's attachments ----------
def keep_attachments(db, cfg, rid, attachments, say):
    """An email's attachments kept as its files (once: a file it has already isn't added again), and those Lens can
    read made resources of their own beside it (documents.attachment_resources), queued for the namespace's pipeline.
    A resource made from an attachment says which email and file it came from (attached_to)."""
    from . import documents, files

    rec = db.one("SELECT space, collection, recorded_at FROM $r", r=R("recording", rid)) or {}
    have = {(f["name"], f["size"]): f for f in files.of(db, rid) if f.get("role") == "attachment"}
    kept = made = 0
    for a in attachments[:MAX_ATTACHMENTS]:
        try:
            f = have.get((files.clean_name(a["name"]), len(a["data"])))
        except ValueError:  # a name with nothing left to keep
            f, a = None, {**a, "name": "attachment"}
        if f is None:
            tmp = files.incoming(cfg)
            tmp.write_bytes(a["data"])
            try:
                f = files.add(db, cfg, rid, tmp, a["name"], "attachment", by=f"email:{rid}")
            except ValueError as e:
                say(f"attachment {a['name']} wasn't kept: {e}")
                continue
            kept += 1
        if not (cfg.get("documents") or {}).get("attachment_resources", True) or f.get("resource"):
            continue
        ext = ext_of(f["name"])
        kind = documents.kind_of(f["name"]) or ("audio" if ext in {e.lower() for e in cfg["audio"]["extensions"]} else None)
        if not kind or (kind == "document" and unavailable(cfg, f["name"])):
            continue
        made += _resource_of(db, cfg, rid, rec, f, kind)
    if attachments[MAX_ATTACHMENTS:]:
        say(f"only its first {MAX_ATTACHMENTS} attachments were kept")
    if attachments:
        say(f"{len(attachments)} attachment(s): {kept} kept now, {made} made resource(s) of their own")


def _resource_of(db, cfg, rid, rec, f, kind):
    """One attachment as a resource of its own (or the one the same file is already, in its namespace): 1 if made."""
    from . import files, ingest, jobs, keyring

    src = files.path_of(cfg, f)
    fp = ingest.fingerprint(src, db=db, cfg=cfg)
    known = db.one("SELECT record::id(id) AS id FROM recording WHERE fp_key = $k", k=f"{rec['space']}:{fp}")
    if known:
        db.q("UPDATE $r SET resource = $x", r=R("resource_file", f["id"]), x=known["id"])
        return 0
    ns = store.space_names(db).get(rec["space"]) or str(rec["space"])
    dest = pathlib.Path(cfg["data_dir"]) / "uploads" / ns / f"attachment-{int(rid)}-{f['id']}" / f["name"]
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dest)  # an encrypted attachment stays encrypted: its header names the key it needs
    if not keyring.is_encrypted(src):
        keyring.protect(db, cfg, rec["space"], dest)
    st = dest.stat()
    new = db.next_id("recording")
    db.q(
        "CREATE $r CONTENT $d",
        r=R("recording", new),
        d=store.clean(
            {
                "space": rec["space"],
                "collection": rec.get("collection"),
                "path": str(dest),
                "source": kind,
                "media": {"kind": kind} if kind != "audio" else None,
                "size": keyring.plain_size(db, cfg, dest),
                "mtime": st.st_mtime,
                "fingerprint": fp,
                "fp_key": f"{rec['space']}:{fp}",
                "title": pathlib.PurePosixPath(f["name"]).stem or f["name"],
                "recorded_at": rec.get("recorded_at") or ingest.recorded_at(dest, st.st_mtime),
                "attached_to": {"resource": int(rid), "file": f["id"]},
                "status": "new",
                "created_at": store.now(),
            }
        ),
    )
    db.q("UPDATE $r SET resource = $x", r=R("resource_file", f["id"]), x=new)
    jobs.enqueue(db, new, None, by=f"email:{rid}")
    return 1
