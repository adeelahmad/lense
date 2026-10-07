"""An anytopdf for tests (anytopdf.py): `anytopdf ... convert IN -o OUT ...` writes a PDF of IN's text (a page of
HTML's text without its markup; an image's, the words in its name), and logs how it was run, one JSON line each, to
the file named by its log. It's also a conversion node: `node(token)` serves `anytopdf queue serve`'s API from
memory, converting each upload the same way."""

from __future__ import annotations

import html
import http.server
import json
import pathlib
import re
import sys
import threading
import urllib.parse

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCRIPT = r"""#!@PYTHON@
import json, os, pathlib, sys
sys.path.insert(0, @ROOT@)
from tests.fake_anytopdf import evidence_of, pdf_of

args = sys.argv[1:]
if args == ["--version"]:
    print("anytopdf 0.4.0")
    sys.exit(0)
with open(@LOG@, "a") as f:
    f.write(json.dumps({"args": args, "proxy": os.environ.get("https_proxy"), "anytopdf_env": sorted(k for k in os.environ if k.startswith("ANYTOPDF_"))}) + "\n")
at, out = args.index("convert") + 1, args[args.index("-o") + 1]
srcs = args[at:next((i for i in range(at, len(args)) if args[i].startswith("-")), len(args))]
said = [args[i + 1] for i, a in enumerate(args) if a == "--transcript"]
if any("unreadable" in open(src, "rb").read().decode("utf-8", "replace") for src in srcs):
    print("WARNING [input.unreadable]: it can't be read", file=sys.stderr)
    sys.exit(3)
if len(srcs) == 1 and not said and "--no-provenance-page" in args:
    pathlib.Path(out).write_bytes(pdf_of(srcs[0], open(srcs[0], "rb").read()))
else:
    pathlib.Path(out).write_bytes(evidence_of([(s, open(s, "rb").read()) for s in srcs], said, "--no-provenance-page" not in args))
"""


def text_of(name, raw):
    """What the fake reads in a file: an image's name, a page of HTML's text, else its text."""
    name = str(name)
    if pathlib.Path(name).suffix.lower() in (".png", ".jpg", ".jpeg"):
        return [f"Photographed page {pathlib.Path(name).stem}"]
    if pathlib.Path(name).suffix.lower() == ".docx":  # its paragraphs, as LibreOffice on the node would read them
        import io
        import zipfile

        xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode()
        raw = "\n".join(re.sub(r"<[^>]+>", "", p) for p in re.findall(r"(?s)<w:p[ >].*?</w:p>", xml)).encode()
    text = raw.decode("utf-8", "replace")
    if pathlib.Path(name).suffix.lower() in (".html", ".htm"):
        text = re.sub(r"(?is)<(script|style|head)\b.*?</\1>", " ", text)
        text = html.unescape(re.sub(r"<[^>]+>", "\n", text))
    lines = [x.strip().encode("latin-1", "replace").decode("latin-1") for x in text.splitlines() if x.strip()]
    return lines or ["(empty)"]


def evidence_of(files, transcripts=(), provenance=True):
    """Several files as one PDF, a page each (audio and video as the transcript of the same name, given or packed
    beside it), then a provenance page with each one's SHA-256."""
    import hashlib

    from tests.helpers import text_pdf

    said = {pathlib.Path(t).stem: pathlib.Path(t).read_bytes() for t in transcripts}
    pages = []
    for name, raw in files:
        stem, ext = pathlib.Path(name).stem, pathlib.Path(name).suffix.lower()
        if ext in (".wav", ".mp3", ".m4a", ".mp4", ".webm", ".ogg", ".flac"):
            pages.append(["\n".join([f"Audio source: {pathlib.Path(name).name}", *text_of("x.srt", said.get(stem, b"(no transcript)"))])])
        else:
            pages.append(["\n".join(text_of(name, raw))])
    if provenance:
        lines = ["Provenance"]
        for name, raw in files:
            lines += [f"Source: {pathlib.Path(name).name}", f"SHA-256: {hashlib.sha256(raw).hexdigest()}"]
        pages.append(["\n".join(lines)])
    return text_pdf(pages)


def pdf_of(name, raw):
    from tests.helpers import text_pdf

    return text_pdf([["\n".join(text_of(name, raw))]])


def make(path, log):
    path = pathlib.Path(path)
    script = SCRIPT.replace("@PYTHON@", sys.executable).replace("@ROOT@", repr(str(ROOT))).replace("@LOG@", repr(str(log)))
    path.write_text(script)
    path.chmod(0o755)
    return str(path)


def runs(log):
    p = pathlib.Path(log)
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


class Node(http.server.BaseHTTPRequestHandler):
    token = ""
    jobs: dict[str, dict] = {}
    seen: list[str] = []

    def _send(self, code, body, kind="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self):
        if self.headers.get("Authorization") != f"Bearer {Node.token}":
            self._send(401, {"error": "missing or wrong bearer token"})
            return False
        return True

    def do_POST(self):  # noqa: N802 - the handler's name
        Node.seen.append(f"POST {self.path}")
        if not self._authorized():
            return
        name = urllib.parse.unquote_plus(re.search(r"filename=([^&]+)", self.path).group(1))
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        jid = f"job_{len(Node.jobs) + 1}"
        failed = b"unreadable" in raw
        if failed:
            made = None
        elif name.endswith(".zip"):  # one job of several files: each one's page (the node's own options keep provenance)
            import io
            import zipfile

            z = zipfile.ZipFile(io.BytesIO(raw))
            files = [(n, z.read(n)) for n in z.namelist()]
            made = evidence_of(files)
        else:
            made = pdf_of(name, raw)
        Node.jobs[jid] = {"name": name, "pdf": made}
        self._send(202, {"job_id": jid, "state": "queued", "origin": "http", "inputs": [name]})

    def do_GET(self):  # noqa: N802
        Node.seen.append(f"GET {self.path}")
        if not self._authorized():
            return
        m = re.match(r"^/v1/jobs/([^/]+)(/output)?$", self.path)
        job = Node.jobs.get(m.group(1)) if m else None
        if not job:
            return self._send(404, {"error": "no such job"})
        if m.group(2):
            return self._send(200, job["pdf"], "application/pdf") if job["pdf"] else self._send(409, {"error": "not done"})
        state = {"state": "failed", "exit_code": 3} if job["pdf"] is None else {"state": "succeeded", "exit_code": 0}
        self._send(200, {"job_id": m.group(1), "inputs": [job["name"]], **state})

    def log_message(self, *a):
        pass


def node(token):
    """A conversion node on loopback: (server, its address)."""
    Node.token, Node.jobs, Node.seen = token, {}, []
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Node)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
