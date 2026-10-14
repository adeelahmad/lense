"""An IMAP server for tests that speaks just enough IMAP4rev1 over a plain socket for imaplib: LOGIN, LIST, EXAMINE
and SELECT, UID FETCH (sizes, dates, header fields and whole messages, as literals), UID STORE and LOGOUT. It keeps
every command it was sent (`commands`), so a test can check nothing was changed or marked read.

`mailboxes` is {name: [(uid, raw message, INTERNALDATE)]}; `flags` gives a mailbox's LIST flags (e.g. \\Trash), and
`validity` its UIDVALIDITY (7 unless set), which a test changes to rebuild a mailbox. `stored` is {(mailbox, uid):
flags} set by UID STORE; `uidnext` False leaves UIDNEXT out of SELECT's answer, as some servers do.
"""

from __future__ import annotations

import email
import re
import socketserver
import threading
from email import policy


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, mailboxes, user="lens", password="mail password 1", flags=None):
        super().__init__(("127.0.0.1", 0), Handler)
        self.mailboxes, self.user, self.password, self.flags = mailboxes, user, password, flags or {}
        self.commands, self.validity, self.stored, self.uidnext = [], {}, {}, True

    @property
    def port(self):
        return self.server_address[1]


def _atoms(text):
    """IMAP arguments: quoted strings (unescaped) and atoms."""
    return [re.sub(r"\\(.)", r"\1", q) if q else a for q, a in re.findall(r'"((?:[^"\\]|\\.)*)"|(\S+)', text)]


def _quoted(name):
    return '"' + name.replace("\\", "\\\\").replace('"', '\\"') + '"'


class Handler(socketserver.StreamRequestHandler):
    def send(self, data):
        self.wfile.write(data if isinstance(data, bytes) else data.encode())

    def handle(self):
        srv, box, signed_in = self.server, None, False
        self.send("* OK [CAPABILITY IMAP4rev1] fake IMAP ready\r\n")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            line = line.decode().rstrip("\r\n")
            srv.commands.append(line)
            tag, _, rest = line.partition(" ")
            cmd, _, args = rest.partition(" ")
            cmd = cmd.upper()
            if cmd == "CAPABILITY":
                self.send(f"* CAPABILITY IMAP4rev1\r\n{tag} OK done\r\n")
            elif cmd == "LOGIN":
                user, password = _atoms(args)[:2]
                if (user, password) == (srv.user, srv.password):
                    signed_in = True
                    self.send(f"{tag} OK signed in\r\n")
                else:
                    self.send(f"{tag} NO [AUTHENTICATIONFAILED] Invalid credentials\r\n")
            elif cmd == "LOGOUT":
                self.send(f"* BYE\r\n{tag} OK bye\r\n")
                return
            elif not signed_in:
                self.send(f"{tag} BAD sign in first\r\n")
            elif cmd == "LIST":
                for name in srv.mailboxes:
                    flags = " ".join(["\\HasNoChildren", *srv.flags.get(name, [])])
                    self.send(f'* LIST ({flags}) "/" {_quoted(name)}\r\n')
                self.send(f"{tag} OK listed\r\n")
            elif cmd in ("SELECT", "EXAMINE"):
                name = _atoms(args)[0]
                if name not in srv.mailboxes:
                    self.send(f"{tag} NO no such mailbox\r\n")
                    continue
                box = name
                validity = srv.validity.get(box, 7)
                nxt = max((m[0] for m in srv.mailboxes[box]), default=0) + 1
                more = f"* OK [UIDNEXT {nxt}] ok\r\n" if srv.uidnext else ""
                mode = "READ-ONLY" if cmd == "EXAMINE" else "READ-WRITE"
                self.send(
                    f"* {len(srv.mailboxes[box])} EXISTS\r\n* OK [UIDVALIDITY {validity}] ok\r\n{more}{tag} OK [{mode}] {cmd} done\r\n"
                )
            elif cmd == "UID" and box is not None and args.upper().startswith("STORE "):
                _, which, how, flags = args.split(" ", 3)
                for uid in which.split(","):
                    srv.stored.setdefault((box, int(uid)), set()).update(flags.strip("()").split())
                self.send(f"{tag} OK stored\r\n")
            elif cmd == "UID" and box is not None:
                self.uid(tag, args, srv.mailboxes[box])
            else:
                self.send(f"{tag} BAD not here\r\n")

    def uid(self, tag, args, messages):
        sub, _, args = args.partition(" ")
        if sub.upper() != "FETCH":
            self.send(f"{tag} BAD only UID FETCH\r\n")
            return
        which, _, items = args.partition(" ")
        if which.endswith(":*"):  # n:* is every message from n on, and at least the last one (as IMAP has it)
            chosen = [m for m in messages if m[0] >= int(which[:-2])] or messages[-1:]
        elif which == "*":
            chosen = messages[-1:]
        else:
            chosen = [m for m in messages if str(m[0]) == which]
        for seq, (uid, raw, arrived) in enumerate(chosen, 1):
            if "BODY.PEEK[]" in items:
                self.send(f"* {seq} FETCH (UID {uid} BODY[] {{{len(raw)}}}\r\n".encode() + raw + b")\r\n")
                continue
            if "HEADER.FIELDS" not in items:
                self.send(f"* {seq} FETCH (UID {uid})\r\n")
                continue
            msg = email.message_from_bytes(raw, policy=policy.default)
            fields = re.search(r"HEADER\.FIELDS \(([^)]*)\)", items).group(1)
            head = "".join(f"{k}: {v}\r\n" for k in fields.split() for v in msg.get_all(k) or []).encode() + b"\r\n"
            self.send(
                f'* {seq} FETCH (UID {uid} RFC822.SIZE {len(raw)} INTERNALDATE "{arrived}" '
                f"BODY[HEADER.FIELDS ({fields})] {{{len(head)}}}\r\n".encode()
                + head
                + b")\r\n"
            )
        self.send(f"{tag} OK fetched\r\n")


def start(mailboxes, **kw):
    srv = Server(mailboxes, **kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
