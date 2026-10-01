"""Making documents into PDFs safely: the HTML Lens prints keeps text and structure and nothing that fetches or runs;
the proxy the browser goes through serves Lens's page and refuses everything else; emails are read with their
attachments and inline images."""

from __future__ import annotations

import base64
import http.client
from email.message import EmailMessage

from app.domain import convert, netguard

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")


def test_html_is_cleaned_of_anything_that_fetches_or_runs():
    dirty = (
        '<p style="color:red" onclick="steal()">Hello <b>there</b> &amp; &lt;you&gt;</p>'
        "<script>alert(1)</script><style>body{background:url(http://x/y)}</style>"
        '<img src="http://169.254.169.254/latest/meta-data/"><img src="data:image/png;base64,AAAA" alt="pixel">'
        '<a href="javascript:steal()">bad</a> <a href="https://example.org/a">good</a>'
        '<div style="background-image: url(http://x/y)">styled</div><iframe src="http://x/"><p>inside</p></iframe>'
        "<custom-tag>kept text</custom-tag><svg><text>drawn</text></svg>"
    )
    clean = convert.clean_html(dirty)
    assert '<p style="color:red">Hello <b>there</b> &amp; &lt;you&gt;</p>' in clean
    assert "script" not in clean and "alert" not in clean and "url(" not in clean and "background" not in clean
    assert "169.254" not in clean and '<img src="data:image/png;base64,AAAA" alt="pixel">' in clean
    assert "<a>bad</a>" in clean and '<a href="https://example.org/a">good</a>' in clean
    assert "<div>styled</div>" in clean and "inside" not in clean and "drawn" not in clean
    assert "kept text" in clean and "custom-tag" not in clean


def test_text_markdown_and_saved_pages_become_pages_of_ours():
    assert convert.decode("café".encode()) == "café" and convert.decode("café".encode("cp1252")) == "café"
    assert convert.decode(b"\xef\xbb\xbfbom") == "bom" and convert.decode("hi".encode("utf-16")) == "hi"
    page = convert.text_page("Line one\n  <b>not bold</b>", "Notes")
    assert "<title>Notes</title>" in page and "&lt;b&gt;not bold&lt;/b&gt;" in page
    md = convert.markdown_page("# The harbour\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n<script>x</script>", "Report")
    assert "<h1>The harbour</h1>" in md and "<td>1</td>" in md and "<script>" not in md
    mdx = convert.markdown_page("import X from './x'\n\n# Title\n\n<Callout>Said</Callout>", "MDX", mdx=True)
    assert "import" not in mdx and "<h1>Title</h1>" in mdx and "Said" in mdx
    page, title = convert.web_page(
        b'<html><head><meta charset="windows-1252"><title>Saved \xe9t\xe9</title></head><body><p>Body</p></body></html>'
    )
    assert title == "Saved été" and "<p>Body</p>" in page
    assert (convert.needs("a.DOCX"), convert.needs("a.pdf"), convert.needs("a.mp3")) == (True, False, False)
    assert (convert.word("x.pptx"), convert.word("x.eml"), convert.content_type("x.odt")) == (
        "PowerPoint presentation",
        "email",
        "application/vnd.oasis.opendocument.text",
    )


def _email(folder):
    m = EmailMessage()
    m["Subject"] = "Harbour report"
    m["From"] = "Mara Keane <mara@example.org>"
    m["To"] = "tom@example.org, Ann <ann@example.org>"
    m["Date"] = "Tue, 29 Sep 2026 09:30:00 +0100"
    m.set_content("The ships arrived at dawn.")
    m.add_alternative(
        '<p>The <b>ships</b> arrived at dawn.</p><img src="cid:logo@x"><img src="http://127.0.0.1:9/track.png">', subtype="html"
    )
    m.get_payload()[1].add_related(PNG, "image", "png", cid="<logo@x>")
    m.add_attachment(b"%PDF-1.4 fake", maintype="application", subtype="pdf", filename="manifest.pdf")
    m.add_attachment(b"notes", maintype="application", subtype="octet-stream", filename="data.xyz")
    path = folder / "harbour.eml"
    path.write_bytes(m.as_bytes())
    return path


def test_an_email_is_read_with_its_attachments_and_inline_images(folder):
    e = convert.read_eml(_email(folder))
    assert (e["subject"], e["from"], e["date"]) == ("Harbour report", "Mara Keane <mara@example.org>", "2026-09-29T09:30:00+01:00")
    assert e["to"] == "tom@example.org, Ann <ann@example.org>" and "<b>ships</b>" in e["html"]
    assert [(p["name"], p["attached"]) for p in e["parts"]] == [("manifest.pdf", True), ("data.xyz", True)]
    assert [(p["cid"], p["type"]) for p in e["inline"]] == [("logo@x", "image/png")]
    page, attachments = convert.email_page(e)
    assert "data:image/png;base64," in page and "127.0.0.1:9" not in page  # the logo written in, the tracker gone
    assert "<h1>Harbour report</h1>" in page and "29 September 2026, 09:30 +0100" in page
    assert [a["name"] for a in attachments] == ["manifest.pdf", "data.xyz"]  # the inline logo isn't an attachment
    assert "manifest.pdf (1 KB)" in page


def test_the_proxy_serves_lens_page_and_refuses_everything_else():
    with netguard.Guard({netguard.DOCUMENT_URL: (b"<p>hi</p>", "text/html; charset=utf-8")}) as g:
        port = int(g.url.rsplit(":", 1)[1])

        def ask(method, target):
            c = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            c.putrequest(method, target, skip_host=True, skip_accept_encoding=True)
            c.endheaders()
            r = c.getresponse()
            out = (r.status, r.getheader("Content-Security-Policy"), r.read())
            c.close()
            return out

        status, csp, body = ask("GET", netguard.DOCUMENT_URL)
        assert (status, body) == (200, b"<p>hi</p>") and "script-src" not in csp and "default-src 'none'" in csp
        assert ask("GET", "http://169.254.169.254/latest/meta-data/")[0] == 403
        assert ask("CONNECT", "example.org:443")[0] == 403
        assert ask("POST", netguard.DOCUMENT_URL)[0] == 403
        assert g.refused == ["GET http://169.254.169.254/latest/meta-data/", "CONNECT example.org:443", "POST http://document.lens/"]
        assert g.args() == [f"--proxy-server={g.url}", "--proxy-bypass-list=<-loopback>"]
    env = netguard.nowhere_env({"HTTPS_PROXY": "http://corp:3128", "no_proxy": "localhost", "PATH": "/bin"})
    assert env["HTTPS_PROXY"] == env["http_proxy"] == "http://127.0.0.1:9" and "no_proxy" not in env and env["PATH"] == "/bin"


def test_what_the_server_can_convert(cfg, monkeypatch):
    monkeypatch.setattr(convert, "soffice", lambda cfg: None)
    monkeypatch.setattr(convert, "chromium", lambda cfg: None)
    assert convert.capabilities(cfg) == {"office": False, "pages": False, "msg": False}
    assert convert.unavailable(cfg, "a.pdf") is None and convert.unavailable(cfg, "a.mp3") is None
    assert convert.unavailable(cfg, "a.docx") == "converting Word documents needs LibreOffice on the server (the lens:full image)"
    assert "Chromium or LibreOffice" in convert.unavailable(cfg, "a.eml")
    monkeypatch.setattr(convert, "chromium", lambda cfg: "/usr/bin/chromium")
    monkeypatch.setattr(convert, "_has_msg", lambda: False)
    assert convert.capabilities(cfg) == {"office": False, "pages": True, "msg": False}
    assert convert.unavailable(cfg, "a.md") is None and "extract-msg" in convert.unavailable(cfg, "a.msg")
    assert convert.bootstrap(cfg)["chromium"] == "/usr/bin/chromium"
