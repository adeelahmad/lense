"""A stand-in for `rclone authorize <type> [options] --auth-no-open-browser` (rclone_signin.py's tests): it prints the
link to its page, sends that page on to the service, and waits for the browser to come back with a code. Code "good"
gets a token, printed between rclone's arrows (encoded with the options, as rclone does when given some); anything
else, or an error, ends it as rclone would. It listens on a free port rather than rclone's own, and says which.

Run as a program (RCLONE_BINARY points tests at it); it imports nothing from Lens.
"""

from __future__ import annotations

import base64
import http.server
import json
import secrets
import sys
import urllib.parse

SERVICE = "https://accounts.example.test/o/oauth2/auth"


def main(argv):
    if argv[:1] == ["--config"]:
        argv = argv[2:]
    args = [a for a in argv if not a.startswith("--")]
    if args[:1] != ["authorize"] or len(args) < 2:
        print("Fatal error: unknown command", file=sys.stderr)
        return 1
    typ, blob = args[1], (args[2] if len(args) > 2 else "")
    opts = json.loads(base64.b64decode(blob + "=" * (-len(blob) % 4))) if blob else {}
    state = secrets.token_urlsafe(16)
    done: dict = {}

    class Page(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _answer(self, status, text, location=None):
            self.send_response(status)
            if location:
                self.send_header("Location", location)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(text.encode())

        def do_GET(self):
            u = urllib.parse.urlsplit(self.path)
            q = dict(urllib.parse.parse_qsl(u.query))
            port = self.server.server_address[1]
            if u.path == "/auth":
                if q.get("state") != state:
                    return self._answer(403, "Forbidden")
                there = {"client_id": f"rclone-{typ}", "redirect_uri": f"http://127.0.0.1:{port}/", "state": state, **opts}
                return self._answer(307, "", SERVICE + "?" + urllib.parse.urlencode(there))
            if u.path != "/":
                return self._answer(404, "Not found")
            if not q.get("code"):
                why = "No code returned by remote server: " + q.get("error", "")
                done["error"] = f'config failed to refresh token: Error: Auth Error\nCode: ""\nDescription: {why}\nHelp: '
                return self._answer(400, "Failure!")
            if q.get("state") != state:
                done["error"] = "Auth state doesn't match"
                return self._answer(400, "Failure!")
            self._answer(200, "Success!")
            if q["code"] != "good":
                done["error"] = 'config failed to refresh token: failed to get token: oauth2: "invalid_grant" "Bad Request"'
                return
            tok = {"access_token": "ya29.fake", "token_type": "Bearer", "refresh_token": "1//fake", "expiry": "2030-01-01T00:00:00Z"}
            text = json.dumps(tok)
            if blob:
                text = base64.b64encode(json.dumps({"token": text}).encode()).decode().rstrip("=")
            done["token"] = text

    server = http.server.HTTPServer(("127.0.0.1", 0), Page)
    port = server.server_address[1]
    for line in (
        f'Make sure your Redirect URL is set to "http://127.0.0.1:{port}/" in your custom config.',
        f"Please go to the following link: http://127.0.0.1:{port}/auth?state={state}",
        "Log in and authorize rclone for access",
        "Waiting for code...",
    ):
        print(f"2026/10/15 11:30:31 NOTICE: {line}", file=sys.stderr, flush=True)
    while not done:
        server.handle_request()
    server.server_close()
    if "error" in done:
        print(f"2026/10/15 11:30:40 CRITICAL: Fatal error: {done['error']}", file=sys.stderr, flush=True)
        return 1
    print(f"Paste the following into your remote machine --->\n{done['token']}\n<---End paste", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
