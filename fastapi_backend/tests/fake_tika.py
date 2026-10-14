"""An Apache Tika server for tests (tika.py), on loopback: `GET /version`, and `PUT /rmeta/text` answering like Tika
4.x. A Word file's text is its paragraphs; anything else is read as UTF-8. A file saying `unreadable` is one Tika can't
parse; one saying `nothing about itself` has no title, authors or dates. Every request is kept in Tika.seen."""

from __future__ import annotations

import http.server
import io
import json
import re
import threading
import zipfile


def text_of(raw):
    if raw[:2] == b"PK":  # a Word file: the text of its paragraphs
        xml = zipfile.ZipFile(io.BytesIO(raw)).read("word/document.xml").decode()
        return "\n".join(re.sub(r"<[^>]+>", "", p) for p in re.findall(r"<w:p[ >].*?</w:p>", xml))
    return raw.decode("utf-8", "replace")


class Tika(http.server.BaseHTTPRequestHandler):
    seen: list = []
    redirect = False

    def log_message(self, *args):
        pass

    def _send(self, code, body, kind="application/json", more=None):
        raw = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(raw)))
        for k, v in (more or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):  # noqa: N802
        Tika.seen.append({"method": "GET", "path": self.path})
        if Tika.redirect:
            return self._send(302, b"", "text/plain", {"Location": "http://example.org/elsewhere"})
        if self.path == "/version":
            return self._send(200, b"Apache Tika 4.0.0", "text/plain")
        return self._send(404, b"not found", "text/plain")

    def do_PUT(self):  # noqa: N802
        raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        Tika.seen.append({"method": "PUT", "path": self.path, "headers": dict(self.headers), "size": len(raw)})
        if Tika.redirect:
            return self._send(302, b"", "text/plain", {"Location": "http://example.org/elsewhere"})
        if self.path != "/rmeta/text":
            return self._send(404, b"not found", "text/plain")
        name = (self.headers.get("Content-Disposition") or "").rpartition("filename=")[2]
        part = {"tk:resource-name": name, "Content-Type": "application/octet-stream", "tk:embedded-depth": "0"}
        text = text_of(raw)
        if "unreadable" in text:
            part["tk:exception:container-exception"] = "org.apache.tika.exception.TikaException: Unexpected RuntimeException\n\tat x"
            return self._send(200, [part])
        if "nothing about itself" not in text:
            part.update(
                {
                    "dc:title": "Tide tables",
                    "dc:creator": ["Ada Harbour", "Ben Quay"],
                    "dcterms:created": "2024-03-01T12:00:00Z",
                    "dc:language": "en",
                    "xmpTPg:NPages": "3",
                    "Content-Type": "application/vnd.openxmlformats-officedocument.wordprocessingml.document; charset=x",
                }
            )
        part["tk:content"] = "\n\n\n" + text + "\n"
        inside = {"tk:embedded-depth": "1", "tk:content": "A note tucked inside.", "dc:title": "not the document's"}
        return self._send(200, [part, inside])


def server():
    """A Tika server on loopback: (server, its address)."""
    Tika.seen, Tika.redirect = [], False
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Tika)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"
