"""An anytopdf for tests (anytopdf.py): `anytopdf ... convert IN -o OUT ...` writes a PDF of IN's text (a page of
HTML's text without its markup; an image's, the words in its name) and, with --dump-graph, what its plugins found (a
face, and a bus when it's given an objects model). It logs how it was run, one JSON line each, to
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

ROOT = pathlib.Path(__file__).resolve().parents[1]

SCRIPT = r"""#!@PYTHON@
import json, os, pathlib, sys
sys.path.insert(0, @ROOT@)
from tests.fake_anytopdf import pdf_of

args = sys.argv[1:]
if args == ["--version"]:
    print("anytopdf 0.4.0")
    sys.exit(0)
with open(@LOG@, "a") as f:
    f.write(json.dumps({"args": args, "proxy": os.environ.get("https_proxy"), "anytopdf_env": sorted(k for k in os.environ if k.startswith("ANYTOPDF_"))}) + "\n")
src, out = args[args.index("convert") + 1], args[args.index("-o") + 1]
if "unreadable" in open(src, "rb").read().decode("utf-8", "replace"):
    print("WARNING [input.unreadable]: it can't be read", file=sys.stderr)
    sys.exit(3)
pathlib.Path(out).write_bytes(pdf_of(src, open(src, "rb").read()))
if "--dump-graph" in args:  # what its plugins found: a face, and a bus when given an objects model
    found = [{"kind": "face", "text": "face", "confidence": 0.9, "region": {"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.06}, "attributes": {"face_index": "0"}},
             {"kind": "face", "text": "1 face", "confidence": None, "region": None, "attributes": {}}]
    if os.environ.get("ANYTOPDF_OBJECTS_MODEL"):
        found.append({"kind": "object", "text": "bus", "confidence": 0.93, "region": {"x": 0.02, "y": 0.2, "width": 0.9, "height": 0.5}, "attributes": {"label": "bus"}})
    graph = {"sources": [], "units": [{"kind": "visual", "annotations": found}]}
    pathlib.Path(args[args.index("--dump-graph") + 1]).write_text(json.dumps(graph))
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
        name = re.search(r"filename=([^&]+)", self.path).group(1)
        raw = self.rfile.read(int(self.headers["Content-Length"]))
        jid = f"job_{len(Node.jobs) + 1}"
        failed = b"unreadable" in raw
        Node.jobs[jid] = {"name": name, "pdf": None if failed else pdf_of(name, raw)}
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
