"""The assistant's own email address: mail sent or forwarded to it is answered by email, and what's attached is
imported. Give Lens a mailbox of its own (an address at any provider, with an app password) in Settings › Assistant
email and turn it on: Lens reads new mail over IMAP (the reading is feeds.py's, as for IMAP sources) and answers over
SMTP from that address, so it needs no public address.

What happens to a message is a chat room's (channels.py, bridge.py): it's answered as one Lens account
(mailbox.account), in a conversation per email thread, Lens picks the namespace each question is about, and
reversible changes can be approved by replying "yes". Mail that arrived before it was turned on isn't answered.

Anyone can send mail to an address, and anyone can write any address in From, so it answers only:

- the people in mailbox.users (their addresses), and
- mail the receiving server vouched for (mailbox.check "authenticated", the default): its Authentication-Results
  header (the topmost one, which that server adds, or the one from mailbox.auth_server) says DMARC passed, or DKIM or
  SPF passed for the From address's domain. "off" takes From as it is, for a server of your own that refuses forged
  mail.

It never answers automatic mail (auto-replies, bounces, mailing lists, its own). Mail from anyone else isn't
answered either (an answer would tell a stranger the address is read): its sender is listed in Settings, to let in
with one click, and their last messages are answered once they are. Mail that will be answered is marked read on the
server; anything else stays unread.

What's attached to a message is held for the assistant to import (channels.hold), as a file dropped into a
conversation is, and with mailbox.imports "auto" (the default) imported without waiting for approval, into a
namespace that exists: whoever sent it asked for that. A forwarded email is held whole, as an .eml, which is imported
as an email with its attachments. What a forward says, and what a reply quotes, is someone else's: the question
carries a forward's text marked as data (untrusted.py), and a reply's quoted part not at all.

A message is for a namespace when the address it was sent to names it after a + (lens+pods@example.org), or the first
of mailbox.routes it matches says so (mail_routes.py's conditions, as lines: "from: *@acme.com = acme",
"subject: invoice; from: billing@x.com = finance", "subject: newsletter = skip"); its conversation is then that
namespace's assistant's. Otherwise Lens picks the namespace per question, as in an open chat room.

Answers are plain text (no HTML, so nothing in one loads from another server when it's read), list the excerpts they
cite with links into the web app (bridge.sources), are sent in the same thread (In-Reply-To, References) and are
marked Auto-Submitted: auto-replied (RFC 3834), so other robots don't answer them.

Refine later: IMAP IDLE instead of looking every poll_seconds, a copy of answers in Sent, finding the servers from
the address (autoconfig) for more providers, letting in whole domains, voice notes sent as audio attachments.
"""

from __future__ import annotations

import email
import email.utils
import logging
import re
import smtplib
import ssl
import time
from email import policy
from email.message import EmailMessage
from html.parser import HTMLParser

from . import auth, channels, convert, feeds, mail_routes, store, untrusted

log = logging.getLogger(__name__)
TIMEOUT = 30
MAX_MESSAGE = 50 * 1024 * 1024
FORWARD_CHARS = 3000  # of a forward's text, in the question (the whole email is held, to import)
QUOTE_LINES = 20  # of the question, quoted under the answer
MAX_KEPT = 50  # senders and held messages kept for Settings
# where well-known providers keep their servers (IMAP over SSL, SMTP with STARTTLS on 587 work at all of them); any
# other address is guessed as imap.<domain> and smtp.<domain>
PROVIDERS = {
    **dict.fromkeys(("gmail.com", "googlemail.com"), ("imap.gmail.com", "smtp.gmail.com")),
    **dict.fromkeys(("outlook.com", "hotmail.com", "live.com", "msn.com"), ("outlook.office365.com", "smtp-mail.outlook.com")),
    **dict.fromkeys(("icloud.com", "me.com", "mac.com"), ("imap.mail.me.com", "smtp.mail.me.com")),
    **dict.fromkeys(("fastmail.com", "fastmail.fm"), ("imap.fastmail.com", "smtp.fastmail.com")),
    **dict.fromkeys(("gmx.com", "gmx.net", "gmx.de"), ("imap.gmx.net", "mail.gmx.net")),
    **dict.fromkeys(("yandex.com", "yandex.ru"), ("imap.yandex.com", "smtp.yandex.com")),
    "yahoo.com": ("imap.mail.yahoo.com", "smtp.mail.yahoo.com"),
    "aol.com": ("imap.aol.com", "smtp.aol.com"),
    "zoho.com": ("imap.zoho.com", "smtp.zoho.com"),
    "mailbox.org": ("imap.mailbox.org", "smtp.mailbox.org"),
}
SMTP_PORTS = {"ssl": 465, "starttls": 587, "none": 25}


class MailboxError(channels.ChannelError):
    pass


def settings_of(cfg):
    return cfg.get("mailbox") or {}


def servers(m):
    """((IMAP host, port, security), (SMTP host, port, security)) for the mailbox's settings `m`: what's set, else
    what's known of the address's provider, else imap.<domain> and smtp.<domain>; ports follow the security."""
    domain = (m.get("address") or "").rpartition("@")[2].lower()
    known = PROVIDERS.get(domain) or (f"imap.{domain}", f"smtp.{domain}")
    isec, ssec = m.get("imap_security") or "ssl", m.get("smtp_security") or "starttls"
    return (
        (m.get("imap_host") or known[0], int(m.get("imap_port") or (993 if isec == "ssl" else 143)), isec),
        (m.get("smtp_host") or known[1], int(m.get("smtp_port") or SMTP_PORTS[ssec]), ssec),
    )


def _user(m):
    return m.get("username") or m.get("address") or ""


def _imap(cfg):
    m = settings_of(cfg)
    host, port, security = servers(m)[0]
    return feeds.connect(host, port, security, _user(m), m.get("password") or "")


def _smtp(cfg):
    """A signed-in SMTP connection to send from the mailbox (MailboxError saying why when it can't be had)."""
    m = settings_of(cfg)
    host, port, security = servers(m)[1]
    try:
        if security == "ssl":
            s = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=ssl.create_default_context())
        else:
            s = smtplib.SMTP(host, port, timeout=TIMEOUT)
            if security == "starttls":
                s.starttls(context=ssl.create_default_context())
    except (OSError, smtplib.SMTPException) as e:
        raise MailboxError(f"couldn't reach {host}:{port}: {e}") from None
    try:
        if m.get("password"):
            s.login(_user(m), m["password"])
    except smtplib.SMTPAuthenticationError as e:
        s.close()
        raise MailboxError(f"the mail server refused to sign in: {e.smtp_code} {e.smtp_error.decode('utf-8', 'replace')}") from None
    except (OSError, smtplib.SMTPException) as e:
        s.close()
        raise MailboxError(f"couldn't sign in at {host}:{port}: {e}") from None
    return s


# ---------- reading a message ----------
def _header(msg, name):
    try:
        return re.sub(r"\s+", " ", str(msg.get(name) or "")).strip()
    except (TypeError, ValueError):  # a header too broken to read
        return ""


def _address(value):
    name, addr = email.utils.parseaddr(value or "")
    return name.strip(), addr.strip().lower()


def _domain(value):
    return (value or "").strip().strip("<>").rpartition("@")[2].lower().rstrip(".")


def _aligned(a, b):
    """Whether two domains belong together as DMARC's relaxed alignment has it (the same, or one under the other)."""
    return bool(a and b) and (a == b or a.endswith("." + b) or b.endswith("." + a))


RESULT_RX = re.compile(r"^\s*(dmarc|dkim|spf)\s*=\s*([a-z]+)", re.I)
PROP_RX = re.compile(r"\b(header\.from|header\.d|header\.i|smtp\.mailfrom)\s*=\s*\"?([^\s;\"]+)", re.I)


def _authserv(header):
    """The server an Authentication-Results header is from (its authserv-id); "" when it doesn't say (Microsoft's)."""
    first = header.split(";", 1)[0].strip()
    return "" if "=" in first else first.split()[0].lower() if first else ""


def vouched(cfg, msg, sender):
    """How mail from `sender` is known to be theirs ("DMARC", "DKIM", "SPF": what the receiving server found passed for
    their domain, or "From" when mailbox.check is off), or None when it isn't."""
    m = settings_of(cfg)
    if m.get("check") == "off":
        return "From"
    try:
        found = [str(h) for h in msg.get_all("Authentication-Results") or []]
    except (TypeError, ValueError):
        return None
    if m.get("auth_server"):
        found = [h for h in found if _authserv(h) == m["auth_server"].lower()]
    if not found:
        return None
    domain = _domain(sender)
    passed = set()
    for part in re.sub(r"\([^()]*\)", " ", found[0]).split(";"):  # the topmost: the receiving server's own
        r = RESULT_RX.match(part)
        if not r or r.group(2).lower() != "pass":
            continue
        props = {k.lower(): _domain(v) for k, v in PROP_RX.findall(part)}
        method = r.group(1).lower()
        if method == "dmarc" and _aligned(props.get("header.from") or domain, domain):
            passed.add("DMARC")
        elif method == "dkim" and _aligned(props.get("header.d") or props.get("header.i"), domain):
            passed.add("DKIM")
        elif method == "spf" and _aligned(props.get("smtp.mailfrom"), domain):
            passed.add("SPF")
    return next((x for x in ("DMARC", "DKIM", "SPF") if x in passed), None)


ROBOTS_RX = re.compile(r"^(mailer-daemon|postmaster|no-?reply|do-?not-?reply|bounces?)\b", re.I)


def automatic(msg, sender, me):
    """Why a message is one not to answer (automatic, a list's, a bounce, Lens's own), or None."""
    if not sender or sender == me:
        return "sent by the mailbox itself" if sender else "no sender"
    if ROBOTS_RX.match(sender.partition("@")[0]):
        return "an automatic sender"
    said = _header(msg, "auto-submitted").lower()
    if said and said != "no":
        return "automatic (Auto-Submitted)"
    if _header(msg, "precedence").lower() in ("bulk", "junk", "list", "auto_reply"):
        return "bulk mail"
    if _header(msg, "list-id") or _header(msg, "list-unsubscribe"):
        return "a mailing list's"
    if _header(msg, "x-autoreply") or _header(msg, "x-autorespond"):
        return "an auto-reply"
    if msg.get_content_type() == "multipart/report":
        return "a delivery report"
    return None


class _Text(HTMLParser):
    """An HTML body's text, a line per block."""

    BLOCKS = {"p", "div", "br", "li", "tr", "table", "blockquote", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "head", "title"):
            self.skip += 1
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_endtag(self, tag):
        if tag in ("script", "style", "head", "title"):
            self.skip = max(0, self.skip - 1)
        elif tag in self.BLOCKS:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)


def html_text(markup):
    p = _Text()
    p.feed(markup or "")
    p.close()
    lines = [re.sub(r"[ \t\xa0]+", " ", x).strip() for x in "".join(p.out).splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


# where a forward's text starts: Gmail's, Apple Mail's, Thunderbird's and Outlook's markers
FORWARD_RX = re.compile(
    r"^[ \t]*(?:-{2,}[ \t]*Forwarded message[ \t]*-{2,}|-{2,}[ \t]*Forwarded Message[ \t]*-{2,}|Begin forwarded message:)[ \t]*$",
    re.I | re.M,
)
# where a reply's quote starts: "On … wrote:" (it may wrap onto a second line), Outlook's header block
QUOTE_RX = re.compile(
    r"^[ \t]*On\b[^\n]{0,300}(?:\n[^\n]{0,200})?\bwrote:[ \t]*$|^[ \t]*-{2,}[ \t]*Original Message[ \t]*-{2,}[ \t]*$"
    r"|^[ \t]*_{5,}[ \t]*\n[ \t]*From:[^\n]*\n[ \t]*(?:Sent|Date):",
    re.I | re.M,
)
SIGNATURE_RX = re.compile(r"^-- ?$", re.M)
PREFIX_RX = re.compile(r"^\s*(?:(?:re|fwd?|aw|wg|tr|sv|vs)\s*(?:\[\d+\])?\s*:\s*)+", re.I)


def split(text, forward=False):
    """A message's text as (what the person wrote, what they forwarded or None): a reply's quote and the signature go.
    `forward`: the subject says it's a forward, so Outlook's quote markers start one."""
    text = (text or "").replace("\r\n", "\n")
    fwd, quote = FORWARD_RX.search(text), QUOTE_RX.search(text)
    if forward and quote and not fwd:
        fwd, quote = quote, None
    forwarded = None
    if fwd and (not quote or fwd.start() <= quote.start()):
        text, forwarded = text[: fwd.start()], text[fwd.end() :].strip() or None
    elif quote:
        text = text[: quote.start()]
    sig = SIGNATURE_RX.search(text)
    if sig:
        text = text[: sig.start()]
    own = "\n".join(x for x in text.split("\n") if not x.lstrip().startswith(">"))
    return re.sub(r"\n{3,}", "\n\n", own).strip(), forwarded


def body_text(msg):
    """The message's text: its plain part, else its HTML's text."""
    try:
        part = msg.get_body(preferencelist=("plain", "html"))
        content = part.get_content() if part is not None else ""
    except (KeyError, LookupError, ValueError, TypeError):  # an unknown charset, a broken part
        return ""
    if not isinstance(content, str):
        return ""
    return html_text(content) if part.get_content_type() == "text/html" else content


def _ids(value):
    return re.findall(r"<[^<>\s]{1,250}>", value or "")


def _files(parsed, raw, subject, forwarded):
    """[(file name, bytes)] to hold for the assistant: a forwarded email whole (the one attached, else this one), or
    the attachments (not the images the email shows inside it)."""
    attached = [p for p in parsed["parts"] if p["type"] == "message/rfc822"]
    if forwarded or attached:
        inline = [] if attached else [((PREFIX_RX.sub("", subject).replace("/", "-").strip() or "forwarded email") + ".eml", raw)]
        return (
            [(p["name"], p["data"]) for p in attached]
            + inline
            + [(p["name"], p["data"]) for p in parsed["parts"] if attached and p["type"] != "message/rfc822" and p["attached"]]
        )
    shown = parsed.get("html") or ""
    return [(p["name"], p["data"]) for p in parsed["parts"] if p["attached"] or not (p.get("cid") and f"cid:{p['cid']}" in shown)]


# ---------- the channel ----------
class Mailbox(channels.Channel):
    key, label, lease = "mailbox", "Email", "mailbox"
    sources = True  # an email is read later, away from Lens: it says where each cited excerpt is

    def __init__(self):
        self.parts = {}  # ref: the bytes of a file sent with a message just read (download)
        self.looked = 0.0  # when this process last looked at the mailbox (time.monotonic)

    def ready(self, cfg):
        m = settings_of(cfg)
        if not m.get("enabled"):
            return "off"
        if not m.get("address"):
            return "the assistant's email address"
        if not m.get("password"):
            return "the mailbox's password"
        if not m.get("account"):
            return "a Lens account to answer as"
        return None

    def check(self, db, cfg):
        m = settings_of(cfg)
        if not m.get("address"):
            return "set the assistant's email address first"
        if not m.get("password"):
            return "set the mailbox's password first (most providers give an app password for this)"
        folder = m.get("folder") or "INBOX"
        try:
            with _imap(cfg) as conn:
                feeds._select(conn, folder)
        except ValueError:
            return f"signed in, but the mailbox has no folder {folder}"
        except RuntimeError as e:
            return f"reading mail: {e}"
        try:
            _smtp(cfg).quit()
        except MailboxError as e:
            return f"sending mail: {e}"
        except (OSError, smtplib.SMTPException):
            pass  # signed in; only saying goodbye failed
        if not m.get("account"):
            return "choose the Lens account it answers as"
        u = auth.find_account(db, m["account"])
        if not u or u.get("disabled"):
            return f"there's no active Lens account {m['account']}"
        return None

    def acts(self, cfg):
        return {"import_files"} if settings_of(cfg).get("imports", "auto") == "auto" else ()

    def pause(self, cfg, reading, failed):
        if failed:  # a server that refused the sign-in isn't asked again at once (it may lock the account)
            return max(60, int(settings_of(cfg).get("poll_seconds") or 30))
        return 10  # the lease is kept fresh; fetch looks at the mailbox every poll_seconds

    def message(self, cfg, raw, uid, validity):
        """A message read from the mailbox as a channel message (channels.py), with what decides whether it's
        answered: auto (why it isn't to be), vouched (how the sender is known), mail (what routes look at)."""
        m = settings_of(cfg)
        msg = email.message_from_bytes(raw, policy=policy.default)
        name, sender = _address(_header(msg, "from"))
        subject = _header(msg, "subject")
        topic = PREFIX_RX.sub("", subject).strip()
        own, forwarded = split(body_text(msg), forward=bool(re.match(r"^\s*fwd?\s*:", subject, re.I)))
        try:
            parsed = convert.parse_email(msg)
        except (KeyError, LookupError, ValueError, TypeError):  # an unknown charset, a broken part: no files then
            parsed = {"parts": [], "html": None}
        files = []
        for i, (filename, data) in enumerate(_files(parsed, raw, subject, forwarded), 1):
            ref = f"{validity}-{uid}-{i}"
            self.parts[ref] = data
            files.append({"ref": ref, "filename": filename, "size": len(data)})
        msgid = (_ids(_header(msg, "message-id")) or [None])[0]
        refs = _ids(_header(msg, "references"))[-20:]
        replied = _ids(_header(msg, "in-reply-to"))
        root = (refs or replied or [msgid or f"{sender} {topic}"])[0]
        q = own
        if not (refs or replied) and topic and not forwarded and not own.startswith("/") and topic.lower() not in own.lower():
            q = f"{topic}\n\n{own}".strip()  # a new email's question is often its subject alone
        if forwarded:
            cut = forwarded[:FORWARD_CHARS] + (" (cut short)" if len(forwarded) > FORWARD_CHARS else "")
            q = (q or "I forwarded this email.") + "\n\nWhat I forwarded (held to import, as an attachment below):\n"
            q += untrusted.wrap(cut, "forwarded email", cfg)
        return {
            "gateway": "email",
            "channel": topic or "(no subject)",
            "account": "email",
            "username": sender,
            "userid": sender,
            "text": q,
            "event": "",
            "direct": True,  # mail to the assistant is for it
            "files": files,
            "thread": ["email", m.get("address"), root],
            "uid": uid,
            "validity": validity,
            "auto": automatic(msg, sender, (m.get("address") or "").lower()),
            "vouched": vouched(cfg, msg, sender) if sender else None,
            "mail": feeds._addressing(msg, subject, m.get("folder") or "INBOX"),
            "reply": {
                "to": sender,
                "name": name,
                "subject": subject,
                "message_id": msgid,
                "references": refs or replied,
                "date": _header(msg, "date"),
                "said": own,
            },
        }

    def answers(self, cfg, m):
        """Whether a message read is one Lens answers: its sender is let in, and known to have sent it."""
        return not m.get("auto") and bool(m.get("vouched")) and allowed(cfg, m.get("userid"))

    def fetch(self, db, cfg):
        m = settings_of(cfg)
        if time.monotonic() - self.looked < int(m.get("poll_seconds") or 30):
            return []
        self.looked, self.parts = time.monotonic(), {}
        folder = m.get("folder") or "INBOX"
        k = channels.state(db, self)
        out = []
        with _imap(cfg) as conn:
            _, validity = feeds._select(conn, folder, readonly=False)
            cursor = k.get("cursor") or {}
            if cursor.get("folder") != folder or cursor.get("validity") != validity:
                # turned on (or the folder changed, or the server rebuilt it): what's there already isn't answered
                channels.keep(db, self, cursor={"folder": folder, "validity": validity, "uid": _last_uid(conn)}, held=[])
                return []
            listed = feeds._messages(conn, folder, "", cursor, readonly=False)
            uids = sorted(e["cursor"]["uid"] for e in listed)
            if uids:  # once read they aren't read again, answered or not
                channels.keep(db, self, cursor={**cursor, "uid": uids[-1]})
            sizes = {e["cursor"]["uid"]: e["size"] or 0 for e in listed}
            held = [h for h in k.get("held") or [] if isinstance(h, dict) and h.get("validity") == validity]
            again = [h["uid"] for h in held if allowed(cfg, h.get("from"))]  # held for someone since let in
            if again:
                channels.keep(db, self, held=[h for h in held if h["uid"] not in again])
            for uid in again + uids:
                if sizes.get(uid, 0) > MAX_MESSAGE:
                    log.warning("mailbox: message %s is larger than %s MB, left unread", uid, MAX_MESSAGE // 1024 // 1024)
                    continue
                raw = feeds.read(conn, uid)
                if raw is None:
                    continue
                try:
                    out.append(self.message(cfg, raw, uid, validity))
                except Exception:  # noqa: BLE001 - one message that can't be read doesn't stop the rest
                    log.warning("mailbox: message %s couldn't be read", uid, exc_info=True)
            answering = [x["uid"] for x in out if self.answers(cfg, x)]
            if answering:  # read: what isn't answered stays unread, for the person who reads the mailbox
                conn.uid("STORE", ",".join(str(u) for u in answering), "+FLAGS", "(\\Seen)")
        if out:
            _seen(db, cfg, out)
        return out

    def handle(self, db, cfg, base, m):
        if m.get("auto") or not m.get("vouched") or not allowed(cfg, m.get("userid")):
            return False  # never answered: listed in Settings (_seen), and a stranger's held until they're let in
        ns = route(db, cfg, m)
        if ns == "":
            return False  # a route says to leave it
        return super().handle(db, cfg, base, {**m, "namespace": ns} if ns else m)

    def send(self, cfg, m, text):
        r = m.get("reply") or {}
        mb = settings_of(cfg)
        if not r.get("to"):
            raise MailboxError("there's no one to answer")
        out = EmailMessage()
        out["From"] = email.utils.formataddr((mb.get("name") or "Lens", mb["address"]))
        out["To"] = email.utils.formataddr((re.sub(r"[\r\n]+", " ", r.get("name") or ""), r["to"]))
        subject = re.sub(r"[\r\n]+", " ", r.get("subject") or "").strip()
        out["Subject"] = subject if re.match(r"^re\s*:", subject, re.I) else f"Re: {subject}" if subject else "Re: your email"
        out["Date"] = email.utils.formatdate(localtime=True)
        out["Message-ID"] = email.utils.make_msgid(domain=_domain(mb["address"]) or None)
        if r.get("message_id"):
            out["In-Reply-To"] = r["message_id"]
            out["References"] = " ".join([*r.get("references", [])[-19:], r["message_id"]])
        out["Auto-Submitted"] = "auto-replied"  # RFC 3834: other robots don't answer this
        body = (text or "").strip() or "(no answer)"
        said = [x for x in (r.get("said") or "").splitlines()][:QUOTE_LINES]
        if said:
            who = r.get("name") or r["to"]
            body += f"\n\nOn {r.get('date') or 'your email'}, {who} wrote:\n" + "\n".join(f"> {x}" if x else ">" for x in said)
        out.set_content(body)
        s = _smtp(cfg)
        try:
            s.send_message(out, from_addr=mb["address"], to_addrs=[r["to"]])
        except (OSError, smtplib.SMTPException) as e:
            raise MailboxError(f"couldn't send the answer to {r['to']}: {e}") from None
        finally:
            try:
                s.quit()
            except (OSError, smtplib.SMTPException):
                s.close()

    def download(self, cfg, ref):
        data = self.parts.pop(ref, None)
        if data is None:
            raise MailboxError("that file isn't here any more: send the email again")
        return data


MAILBOX = Mailbox()


def _last_uid(conn):
    """The UID of the open mailbox's newest message (0 when it has none): what the server said UIDNEXT is, less one,
    else what it says the last message's UID is."""
    _, data = conn.response("UIDNEXT")
    try:
        return max(0, int((data or [None])[0]) - 1)
    except (TypeError, ValueError):
        pass
    typ, data = conn.uid("FETCH", "*", "(UID)")
    found = [int(x) for d in data or [] if isinstance(d, bytes) for x in re.findall(rb"UID (\d+)", d)] if typ == "OK" else []
    return max(found, default=0)


def allowed(cfg, sender):
    """Whether Lens answers mail from `sender`: they're in mailbox.users."""
    return bool(sender) and sender.lower() in {u.strip().lower() for u in settings_of(cfg).get("users") or [] if u.strip()}


def route(db, cfg, m):
    """The namespace a message is for: the one its address names after a + (lens+pods@…), else the first route it
    matches says ("" to leave it); None when nothing says, and Lens picks per question. A namespace that doesn't
    exist says nothing."""
    names = set(store.space_names(db).values())
    local, _, domain = (settings_of(cfg).get("address") or "").lower().partition("@")
    for a in (m.get("mail") or {}).get("to") or []:
        mine, _, at = a.partition("@")
        tag = mine[len(local) + 1 :] if at == domain and mine.startswith(local + "+") else ""
        if tag in names:
            return tag
    for text in settings_of(cfg).get("routes") or []:
        try:
            rule = mail_routes.parse_line(text)
        except ValueError:
            continue
        if mail_routes.matches(rule, m.get("mail") or {}):
            return "" if rule.get("skip") else rule["namespace"] if rule["namespace"] in names else None
    return None


def _seen(db, cfg, msgs):
    """Who has written (latest first, with whether their mail is vouched for) for Settings, and the messages of people
    not let in, held to answer once they are."""
    k = channels.state(db, MAILBOX)
    people = [p for p in k.get("people") or [] if isinstance(p, dict)]
    held = [h for h in k.get("held") or [] if isinstance(h, dict)]
    for m in msgs:
        if m.get("auto") or not m.get("userid"):
            continue
        people = [p for p in people if p.get("id") != m["userid"]]
        people.insert(0, {"id": m["userid"], "name": m["reply"].get("name") or m["userid"], "vouched": m.get("vouched")})
        if m.get("vouched") and not allowed(cfg, m["userid"]):
            held = [h for h in held if h.get("uid") != m["uid"]]
            held.insert(0, {"uid": m["uid"], "validity": m["validity"], "from": m["userid"], "subject": m["channel"]})
    channels.keep(db, MAILBOX, people=people[:MAX_KEPT], held=held[:MAX_KEPT])


def status(db, cfg):
    """channels.status, with the address and servers it uses and who has written to it (whether each is let in, their
    mail is vouched for, and how many of their messages wait)."""
    s = channels.status(db, cfg, MAILBOX)
    k = channels.state(db, MAILBOX)
    m = settings_of(cfg)
    (ih, ip, isec), (sh, sp, ssec) = servers(m)
    s["address"] = m.get("address")
    s["imap"] = f"{ih}:{ip} ({isec})" if m.get("address") else None
    s["smtp"] = f"{sh}:{sp} ({ssec})" if m.get("address") else None
    held = [h for h in k.get("held") or [] if isinstance(h, dict)]
    s["people"] = [
        {
            "id": p["id"],
            "name": p.get("name") or p["id"],
            "allowed": allowed(cfg, p["id"]),
            "vouched": p.get("vouched"),
            "waiting": sum(1 for h in held if h.get("from") == p["id"]),
        }
        for p in k.get("people") or []
        if isinstance(p, dict) and p.get("id")
    ]
    return s
