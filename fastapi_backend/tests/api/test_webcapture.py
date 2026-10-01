"""Web pages captured as documents (docs/api.md#web-pages): a page is printed by Chromium with its scripts run, a PDF
link kept as it is; only public addresses are reached (here, a network allowed for the test), on the web's ports;
capturing happens once."""

from __future__ import annotations

import http.server
import ipaddress
import socket
import threading
import time

import pytest

from app.domain import convert, jobs, netguard, store, webcapture
from tests import fake_llm
from tests.helpers import chromium_binary, drain, login, make_user, text_pdf

R = store.R
CHROME = chromium_binary()
PAGE = (
    "<html><head><title>Harbour page</title></head><body><h1>The harbour page</h1>"
    "<p>Boats come in at dawn.</p><img src='/pixel.png'><img src='http://169.254.169.254/latest/meta-data/'>"
    "<p id='late'></p><script>document.getElementById('late').textContent = 'Written by the page itself.'</script>"
    "</body></html>"
)
# a page whose script tries WebRTC (STUN is UDP, which would go straight to the address, around the proxy) while an
# image that comes slowly keeps it loading
RTC = (
    "<html><body><p id='s'>waiting</p><script>document.getElementById('s').textContent = 'Its script ran.';"
    "const pc = new RTCPeerConnection({iceServers: [{urls: 'stun:127.0.0.1:PORT'}]}); pc.createDataChannel('x');"
    "pc.createOffer().then((o) => pc.setLocalDescription(o));"
    "</script><img src='/slow.png'></body></html>"
)


class _Site(http.server.BaseHTTPRequestHandler):
    seen: list[str] = []
    stun = 0

    def do_GET(self):  # noqa: N802 - the handler's name
        _Site.seen.append(self.path)
        if self.path == "/report.pdf":
            body, kind = text_pdf([["Lighthouse report", "The lamp was lit at dusk."]]), "application/pdf"
        elif self.path == "/rtc":
            body, kind = RTC.replace("PORT", str(_Site.stun)).encode(), "text/html; charset=utf-8"
        elif self.path == "/slow.png":
            time.sleep(2)
            body, kind = b"", "image/png"
        elif self.path == "/pixel.png":
            body, kind = b"", "image/png"
        elif self.path in ("/moved", "/to-ftp"):
            self.send_response(302)
            self.send_header("Location", "/" if self.path == "/moved" else f"ftp://127.0.0.2:{_Site.stun}/x")
            self.end_headers()
            return
        else:
            body, kind = PAGE.encode(), "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


@pytest.fixture
def site(monkeypatch):
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Site.seen = []
    monkeypatch.setattr(netguard, "PORTS", netguard.PORTS | {srv.server_address[1]})  # the test site's port, as if 80
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()


@pytest.fixture
def app(cfg, db):
    srv, url = fake_llm.start()
    cfg["llm"].update(base_url=url, model="fake")
    cfg["documents"]["chromium"] = CHROME
    from app.main import create_app

    yield create_app(cfg, db, background=False)
    srv.shutdown()


@pytest.fixture
def env(client, db):
    make_user(db, "root@x.io", "root password 1", admin=True)
    make_user(db, "ed@x.io", "editor password 1", roles={"pods": "editor"})
    make_user(db, "view@x.io", "viewer password 1", roles={"pods": "viewer"})
    return {
        "ha": login(client, "root@x.io", "root password 1"),
        "he": login(client, "ed@x.io", "editor password 1"),
        "hv": login(client, "view@x.io", "viewer password 1"),
    }


def test_only_public_addresses_on_web_ports(cfg):
    pub = netguard.public_ip
    assert [pub(a) for a in ("8.8.8.8", "2606:4700::1111")] == [True, True]
    private = ("10.0.0.1", "127.0.0.1", "169.254.169.254", "192.168.1.5", "100.64.0.1", "::1", "fd00::1", "::ffff:127.0.0.1", "224.0.0.1")
    assert not any(pub(a) for a in private)
    assert pub("10.1.2.3", [ipaddress.ip_network("10.0.0.0/8")])  # a network an admin allowed
    assert netguard.resolve("8.8.8.8", 443) == ("8.8.8.8", 443)
    with pytest.raises(ValueError, match="isn't a public address"):
        netguard.resolve("localhost", 80)
    for url, why in (
        ("ftp://example.org/x", "starts with http"),
        ("http://user:pw@example.org/", "user name or password"),
        ("https://8.8.8.8:8443/", "only the web's own ports"),
        ("http://127.0.0.1/admin", "isn't a public address"),
        ("http://169.254.169.254/latest/meta-data/", "isn't a public address"),
    ):
        with pytest.raises(ValueError, match=why):
            webcapture.check_url(cfg, url)
    assert webcapture.check_url(cfg, " https://8.8.8.8/a?b=1#frag ") == "https://8.8.8.8/a?b=1"
    assert webcapture.placeholder("https://example.org/news/item?id=4") == "example.org/news/item?id=4"


def test_file_names():
    assert webcapture.file_name("https://example.org/files/Annual%20Report.PDF", "Annual") == "annual-report.pdf"
    assert webcapture.file_name("https://example.org/news/", "Harbour news") == "harbour-news.pdf"
    assert webcapture.file_name("https://example.org/news/", None) == "example-org-news.pdf"
    assert webcapture.file_name("https://example.org/.pdf", None) == "example-org-pdf.pdf"


def test_the_proxy_forwards_to_allowed_addresses_only(site):
    import urllib.error

    with netguard.Guard(forward=True) as g:  # public addresses only: the test site is refused
        opener = netguard.opener(g)
        with pytest.raises(urllib.error.HTTPError):
            opener.open(site + "/", timeout=10)
        assert "isn't a public address" in g.refused[0] and _Site.seen == []
    with netguard.Guard(forward=True, networks=[ipaddress.ip_network("127.0.0.0/8")]) as g:
        opener = netguard.opener(g)
        with opener.open(site + "/moved", timeout=10) as r:  # redirects are followed, each one checked
            assert b"The harbour page" in r.read() and r.geturl() == site + "/"
        assert _Site.seen == ["/moved", "/"]
        # nor a redirect to anything but the web: urllib would otherwise reach an ftp:// address itself
        with socket.socket() as trap:
            trap.bind(("127.0.0.2", 0))
            trap.listen(1)
            trap.settimeout(1)
            _Site.stun = trap.getsockname()[1]
            with pytest.raises(urllib.error.URLError, match="unknown url type: ftp"):
                opener.open(site + "/to-ftp", timeout=10)
            with pytest.raises(TimeoutError):
                trap.accept()
    with netguard.Guard(forward=True, networks=[ipaddress.ip_network("127.0.0.0/8")]) as g:
        opener = netguard.opener(g)
        with pytest.raises(urllib.error.HTTPError):
            opener.open("http://127.0.0.1:9/", timeout=10)  # not a web port
        assert "port 9 isn't a web port" in g.refused[0]


@pytest.mark.skipif(not CHROME, reason="needs Chromium")
def test_a_page_cant_get_around_the_proxy(cfg, site, tmp_path):
    """A page's scripts run, but reach nothing except through the proxy: WebRTC sends nothing to an address itself."""
    from pypdf import PdfReader

    cfg["documents"].update(chromium=CHROME, web_networks=["127.0.0.0/8"])
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.bind(("127.0.0.1", 0))
        udp.settimeout(1)
        _Site.stun = udp.getsockname()[1]
        got = webcapture.capture(cfg, site + "/rtc", tmp_path / "rtc.pdf")
        assert got["how"] == "printed" and "Its script ran." in PdfReader(str(tmp_path / "rtc.pdf")).pages[0].extract_text()
        assert "/slow.png" in _Site.seen
        with pytest.raises(TimeoutError):
            udp.recvfrom(2048)


@pytest.mark.skipif(not CHROME, reason="needs Chromium")
def test_private_addresses_are_refused(app, client, env, site):
    url = "/api/v1/import/web"
    r = client.post(url, json={"url": site + "/", "namespace": "pods"}, headers=env["he"])
    assert (r.status_code, r.json()["detail"].endswith("only public web pages can be captured")) == (400, True)
    assert client.post(url, json={"url": site + "/", "namespace": "pods"}, headers=env["hv"]).status_code == 403
    assert _Site.seen == []


@pytest.fixture
def intranet(cfg):
    cfg["documents"]["web_networks"] = ["127.0.0.0/8"]  # a network an admin allowed: the test site's


@pytest.mark.skipif(not CHROME, reason="needs Chromium")
def test_a_web_page_becomes_a_document(intranet, app, client, env, db, cfg, site):
    he, hv = env["he"], env["hv"]
    url = "/api/v1/import/web"

    page = client.post(url, json={"url": site + "/moved", "namespace": "pods"}, headers=he).json()
    doc = client.post(url, json={"url": site + "/report.pdf", "namespace": "pods", "title": "Lamp report"}, headers=he).json()
    renamed = client.post(url, json={"url": site + "/", "namespace": "pods"}, headers=he).json()
    rec = db.one("SELECT title, web, source FROM $r", r=R("recording", page["id"]))
    assert (rec["title"], rec["source"], rec["web"]) == (site.split("//")[1] + "/moved", "document", {"url": site + "/moved"})
    assert client.patch(f"/api/v1/resources/{renamed['id']}", json={"title": "Keeper's page"}, headers=he).status_code == 200
    drain(db, cfg)
    assert client.get(f"/api/v1/resources/{renamed['id']}", headers=hv).json()["title"] == "Keeper's page"  # kept

    got = client.get(f"/api/v1/resources/{page['id']}", headers=hv).json()
    assert (got["title"], got["status"], got["web"]["how"], got["web"]["final"]) == ("Harbour page", "analyzed", "printed", site + "/")
    assert got["web"]["captured_at"] and set(got["web"]) == {"url", "final", "captured_at", "how"}
    text = " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=page["id"]))
    assert "Boats come in at dawn." in text and "Written by the page itself." in text  # its scripts ran
    assert "/pixel.png" in _Site.seen
    assert [f.name for f in webcapture.folder(cfg, page["id"]).iterdir()] == ["harbour-page.pdf"]  # named by its title
    assert client.get(f"/api/v1/resources/{page['id']}/files", headers=hv).json()["primary"]["name"] == "harbour-page.pdf"
    log = client.get(f"/api/v1/jobs/{page['job']}/log", headers=he).json()["lines"]
    assert any(f"captured {site}/" in x for x in log)

    pdf = client.get(f"/api/v1/resources/{doc['id']}", headers=hv).json()
    assert (pdf["title"], pdf["web"]["how"]) == ("Lamp report", "pdf")  # named by whoever captured it
    text = " ".join(s["text"] for s in db.rows("SELECT idx, text FROM segment WHERE recording = $r ORDER BY idx", r=doc["id"]))
    assert "The lamp was lit at dusk." in text
    assert [f.name for f in webcapture.folder(cfg, doc["id"]).iterdir()] == ["report.pdf"]  # the link's own name

    # transcribed again, it keeps the page it captured
    seen = len(_Site.seen)
    jobs.enqueue(db, page["id"], ["transcribe"], by="test")
    drain(db, cfg)
    assert len(_Site.seen) == seen
    assert sorted(db.values("SELECT VALUE detail.url FROM audit_log WHERE action = 'import.web'")) == [
        site + "/",
        site + "/moved",
        site + "/report.pdf",
    ]

    # a page that can't be reached fails its job, saying why
    gone = client.post(url, json={"url": "http://127.0.0.1:9/", "namespace": "pods"}, headers=he)
    assert gone.status_code == 400 and "only the web's own ports" in gone.json()["detail"]


def test_without_chromium(app, client, env, cfg, monkeypatch):
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    r = client.post("/api/v1/import/web", json={"url": "https://8.8.8.8/", "namespace": "pods"}, headers=env["he"])
    assert (r.status_code, r.json()["detail"]) == (400, "capturing web pages needs Chromium on the server (the lens:full image)")
    assert client.get("/api/v1/uploads/limits", headers=env["he"]).json()["convert"]["web"] is False
