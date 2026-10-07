"""An anytopdf for tests (anytopdf.py): `anytopdf ... convert IN -o OUT ...` writes a PDF of IN's text (a page of
HTML's text without its markup; an image's, the words in its name) and, with --dump-graph, what its plugins found on
each picture (a face, and a bus when it's given an objects model), with each face described in the face index when it
recognises faces. It logs how it was run, one JSON line each, to
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
srcs, out = args[args.index("convert") + 1 : args.index("-o")], args[args.index("-o") + 1]
if any("unreadable" in open(src, "rb").read().decode("utf-8", "replace") for src in srcs):
    print("WARNING [input.unreadable]: it can't be read", file=sys.stderr)
    sys.exit(3)
pathlib.Path(out).write_bytes(pdf_of(srcs[0], open(srcs[0], "rb").read()))
if "--dump-graph" in args and "--no-plugins" in args:  # what it finds with no plugins: places and dates
    from tests.fake_anytopdf import graph_of
    pathlib.Path(args[args.index("--dump-graph") + 1]).write_text(json.dumps(graph_of(srcs[0], open(srcs[0], "rb").read(), args)))
elif "--dump-graph" in args:  # what its plugins found on each picture: a face, and a bus when given an objects model
    from tests.fake_anytopdf import face_of

    sources, units, seen = [], [], []
    for n, src in enumerate(srcs):
        found = [{"kind": "face", "text": "face", "confidence": 0.9, "region": {"x": 0.1, "y": 0.2, "width": 0.05, "height": 0.06}, "attributes": {"face_index": "0"}},
                 {"kind": "face", "text": "1 face", "confidence": None, "region": None, "attributes": {}}]
        if b"nobody" in open(src, "rb").read():
            found = []
        if os.environ.get("ANYTOPDF_OBJECTS_MODEL"):
            found.append({"kind": "object", "text": "bus", "confidence": 0.93, "region": {"x": 0.02, "y": 0.2, "width": 0.9, "height": 0.5}, "attributes": {"label": "bus"}})
        sources.append({"id": f"source-{n}", "path": src})
        units.append({"kind": "visual", "source_id": f"source-{n}", "annotations": found})
        seen += [(src, face_of(src), a["region"]) for a in found if a["kind"] == "face" and a["region"]]
    graph = {"sources": sources, "units": units}
    pathlib.Path(args[args.index("--dump-graph") + 1]).write_text(json.dumps(graph))
    if "--recognize-faces" in args and os.environ.get("ANYTOPDF_FACE_EMBED_MODEL"):  # each face, described, in its index
        import sqlite3

        db = sqlite3.connect(args[args.index("--face-index") + 1])
        db.execute("CREATE TABLE sightings (id INTEGER PRIMARY KEY, source_path TEXT, vector BLOB, x REAL, y REAL, w REAL, h REAL)")
        for src, vec, r in seen:
            db.execute("INSERT INTO sightings (source_path, vector, x, y, w, h) VALUES (?, ?, ?, ?, ?, ?)", (src, vec, r["x"], r["y"], r["width"], r["height"]))
        db.commit()
"""
# the places the fake's gazetteer knows, and where a photo whose bytes say GPS was taken
PLACES = {"Berlin": ("Berlin, Germany", "Berlin", "Germany", "DE", "52.524", "13.411")}
GPS = ("Paris 16 Passy, Ile-de-France, France", "Paris 16 Passy", "France", "FR", "48.858056", "2.294444")
DATES = [(r"\b(\d{4}-\d{2}-\d{2})\b", "date", None), (r"\b3 March 2026\b", "date", "2026-03-03"), (r"\bnext Friday\b", "date", None)]


def face_of(path):
    """The description the fake gives a face, as little-endian f32: a picture's background colour decides who it is
    (as video_helpers.FakeFaces), anything else is one person."""
    import numpy as np

    e = np.zeros(128, dtype="<f4")
    try:
        from PIL import Image

        e[int(np.argmax(Image.open(path).convert("RGB").getpixel((2, 2))))] = 3.0  # not unit length: Lens makes it so
    except OSError:
        e[0] = 3.0
    e[5] = 0.3
    return e.tobytes()


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


def _loc(text, place, city, country, code, lat, lon, source, **more):
    attributes = {"place": place, "city": city, "country": country, "country_code": code, "latitude": lat, "longitude": lon}
    return {"kind": "location", "text": place, "provider": "location", "attributes": {**attributes, "source": source, **more}}


def graph_of(name, raw, args):
    """`--dump-graph`'s document graph: the places and dates in a text (unless --location is off, or --no-entities),
    and a photo's GPS place when its bytes say GPS."""
    where = args[args.index("--location") + 1] if "--location" in args else "on"
    notes = []
    if pathlib.Path(str(name)).suffix.lower() in (".png", ".jpg", ".jpeg"):
        if where != "off" and b"GPS" in raw:
            notes.append({**_loc(GPS[0], *GPS, "gps", distance_km="1.4"), "confidence": None})
    else:
        text = raw.decode("utf-8", "replace")
        if where == "on":
            for word, p in PLACES.items():
                if re.search(rf"\b{word}\b", text):
                    notes.append({**_loc(p[0], *p, "text", matched=word), "confidence": 0.6})
            if "Mobile" in text:  # a place name that's usually a word: anytopdf isn't sure
                notes.append(
                    {
                        **_loc(
                            "Mobile",
                            "Mobile, Alabama, United States",
                            "Mobile",
                            "United States",
                            "US",
                            "30.69",
                            "-88.04",
                            "text",
                            matched="Mobile",
                        ),
                        "confidence": 0.3,
                    }
                )
        if "--no-entities" not in args:
            for rx, kind, iso in DATES:
                for m in re.finditer(rx, text):
                    at = {"entity": kind, "from": "text", **({"iso": iso or m.group(1)} if (iso or m.groups()) else {"relative": "true"})}
                    notes.append(
                        {"kind": "timestamp", "text": m.group(0), "provider": "text-entities", "confidence": None, "attributes": at}
                    )
    return {
        "sources": [{"id": "s1", "path": str(name)}],
        "units": [{"id": "u1", "source_id": "s1", "kind": "text", "annotations": notes}],
        "metadata": {},
    }


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
