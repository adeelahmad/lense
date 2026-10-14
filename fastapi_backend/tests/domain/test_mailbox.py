"""Reading mail for the assistant's mailbox (mailbox.py): what the person wrote apart from what they quoted or
forwarded, an HTML body's text, and the servers guessed from an address."""

from __future__ import annotations

from app.domain import mailbox


def test_a_reply_keeps_only_what_the_person_wrote():
    gmail = "Yes, Tuesday.\n\nOn Mon, 5 Oct 2026 at 10:00, Lens <\nlens@x.io> wrote:\n> When?\n"
    assert mailbox.split(gmail) == ("Yes, Tuesday.", None)
    outlook = "Yes.\n\n________________________________\nFrom: Lens <lens@x.io>\nSent: Monday\nSubject: Re: x\n\nWhen?"
    assert mailbox.split(outlook) == ("Yes.", None)
    quoted = "> When?\nTuesday\n-- \nAnn, Acme\nphone"
    assert mailbox.split(quoted) == ("Tuesday", None)


def test_a_forward_is_told_apart():
    for marker in ("---------- Forwarded message ---------", "Begin forwarded message:", "-------- Forwarded Message --------"):
        own, fwd = mailbox.split(f"Keep this.\n\n{marker}\nFrom: office@port.example\n\nBerth 4.")
        assert own == "Keep this." and fwd.startswith("From: office@port.example") and fwd.endswith("Berth 4.")
    # Outlook marks a forward as it marks a reply: the subject tells
    outlook = "FYI\n\n-----Original Message-----\nFrom: office@port.example\n\nBerth 4."
    assert mailbox.split(outlook) == ("FYI", None)
    assert mailbox.split(outlook, forward=True)[1].endswith("Berth 4.")


def test_html_bodies_are_read_as_text():
    html = (
        "<html><head><style>p{}</style></head><body><p>Hello&nbsp;there</p><div>Line two<br>three</div><script>x()</script></body></html>"
    )
    assert mailbox.html_text(html) == "Hello there\n\nLine two\nthree"


def test_servers_are_guessed_from_the_address():
    assert mailbox.servers({"address": "me@icloud.com"}) == (("imap.mail.me.com", 993, "ssl"), ("smtp.mail.me.com", 587, "starttls"))
    assert mailbox.servers({"address": "lens@example.org", "smtp_security": "ssl", "imap_security": "starttls"}) == (
        ("imap.example.org", 143, "starttls"),
        ("smtp.example.org", 465, "ssl"),
    )
    own = {"address": "lens@example.org", "imap_host": "mail.example.org", "imap_port": 1993, "smtp_host": "mail.example.org"}
    assert mailbox.servers(own) == (("mail.example.org", 1993, "ssl"), ("mail.example.org", 587, "starttls"))
