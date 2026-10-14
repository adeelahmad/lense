"""An SMTP server for tests that speaks just enough ESMTP over a plain socket for smtplib: EHLO, AUTH PLAIN, MAIL,
RCPT, DATA, RSET, NOOP and QUIT. What it's sent lands in `sent` as (from, [to], parsed message)."""

from __future__ import annotations

import base64
import email
import socketserver
import threading
from email import policy


class Server(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, user="lens@x.io", password="mail password 1"):
        super().__init__(("127.0.0.1", 0), Handler)
        self.user, self.password, self.sent, self.logins = user, password, [], 0

    @property
    def port(self):
        return self.server_address[1]


class Handler(socketserver.StreamRequestHandler):
    def say(self, line):
        self.wfile.write(f"{line}\r\n".encode())

    def handle(self):
        srv, signed_in, sender, to = self.server, False, None, []
        self.say("220 fake SMTP ready")
        while True:
            line = self.rfile.readline()
            if not line:
                return
            cmd, _, arg = line.decode().rstrip("\r\n").partition(" ")
            cmd = cmd.upper()
            if cmd in ("EHLO", "HELO"):
                self.say("250-fake\r\n250-AUTH PLAIN\r\n250 8BITMIME")
            elif cmd == "AUTH":
                _, _, blob = arg.partition(" ")
                _, user, password = base64.b64decode(blob).decode().split("\0")
                srv.logins += 1
                signed_in = (user, password) == (srv.user, srv.password)
                self.say("235 signed in" if signed_in else "535 5.7.8 bad credentials")
            elif cmd == "MAIL":
                if not signed_in:
                    self.say("530 sign in first")
                    continue
                sender, to = arg.partition(":")[2].split()[0].strip("<>"), []
                self.say("250 ok")
            elif cmd == "RCPT":
                to.append(arg.partition(":")[2].split()[0].strip("<>"))
                self.say("250 ok")
            elif cmd == "DATA":
                self.say("354 go on")
                lines = []
                while True:
                    got = self.rfile.readline()
                    if got in (b".\r\n", b".\n", b""):
                        break
                    lines.append(got[1:] if got.startswith(b"..") else got)
                srv.sent.append((sender, to, email.message_from_bytes(b"".join(lines), policy=policy.default)))
                self.say("250 queued")
            elif cmd in ("RSET", "NOOP"):
                self.say("250 ok")
            elif cmd == "QUIT":
                self.say("221 bye")
                return
            else:
                self.say("502 not here")


def start(**kw):
    srv = Server(**kw)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv
