"""The web installer, without installing anything: a stand-in install.sh plays the part.

python3 -m unittest discover installer
"""

from __future__ import annotations

import base64
import http.client
import json
import os
import stat
import sys
import tempfile
import threading
import time
import unittest
from http.server import ThreadingHTTPServer
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import lens_installer as li  # noqa: E402

FAKE = r"""#!/bin/sh
# stands in for install.sh: says what it's doing as install.sh does, writes .env, hands over the setup address
set -eu
mkdir -p "$LENS_DIR"
printf '\033[1m%s\033[0m\n' "Getting Lens into $LENS_DIR..."
{
  echo "ACCESS_SECRET_KEY=x"
  echo "LENS_BACKEND=$LENS_BACKEND"
  echo "LENS_SETUP_SETTINGS=${LENS_SETUP_SETTINGS:-}"
  echo "SEEN=$LENS_WIZARD$LENS_YES$LENS_NO_OPEN"
} >"$LENS_DIR/.env"
chmod 600 "$LENS_DIR/.env"
[ "${FAIL:-0}" = 1 ] && { echo "Docker isn't running. Start it, then run this again." >&2; exit 1; }
if [ "${FAIL:-0}" = 2 ]; then
  echo "The last lines Lens logged:"
  echo "[lens] api stopped; starting it again in 5 seconds"
  echo "cannot open SurrealDB: authentication failed"
  echo "Lens's API keeps stopping in its microsandbox. See everything: msb logs -f lens" >&2
  exit 1
fi
printf '\033[1m%s\033[0m\n' "Lens is running: http://localhost:$LENS_PORT/setup?code=abc123"
"""


def facts(**kw):
    return {"msb_ok": False, "dir": "/tmp/lens-x", "ref": "main", **kw}


class Pure(unittest.TestCase):
    def test_settings_are_kept_to_what_lens_takes(self):
        got = li.clean_settings(
            {
                "llm": {"base_url": "http://localhost:11434/v1", "model": "llama3", "api_key": "", "evil": "x"},
                "server": {"allowed_hosts": "*"},  # not something the installer sets
                "mail": {"port": 587, "password": {"nested": 1}},
                "embeddings": {"enabled": False},
                "budget": {"hard_usd": 25, "period": "month", "price_in": 3.0, "price_out": 15, "usd": 1},
            }
        )
        self.assertEqual(
            got,
            {
                "llm": {"base_url": "http://host.docker.internal:11434/v1", "model": "llama3"},
                "mail": {"port": 587},
                "embeddings": {"enabled": False},
                "budget": {"hard_usd": 25, "period": "month", "price_in": 3.0, "price_out": 15},
            },
        )

    def test_this_machine_as_lens_reaches_it(self):
        self.assertEqual(li.for_lens("http://127.0.0.1:1234/v1"), "http://host.docker.internal:1234/v1")
        self.assertEqual(li.for_lens("http://localhost"), "http://host.docker.internal")
        self.assertEqual(li.for_lens("https://localhost.example.com/v1"), "https://localhost.example.com/v1")

    def test_answers_become_install_sh_settings(self):
        env = li.validate(
            {
                "backend": "docker",
                "target": "lean",
                "port": "3001",
                "public": True,
                "dir": "/tmp/lens-new-x",
                "namespace": "Home",
                "settings": {"llm": {"model": "m"}},
            },
            facts(),
        )
        self.assertEqual(env["LENS_TARGET"], "lean")
        self.assertEqual(env["LENS_PORT"], "3001")
        self.assertEqual(env["LENS_PUBLIC"], "1")
        self.assertEqual(env["LENS_NAMESPACE"], "home")
        raw = env["LENS_SETUP_SETTINGS"]
        self.assertRegex(raw, r"^[A-Za-z0-9_-]+$")  # safe in .env and in install.sh's sed
        self.assertEqual(json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))), {"llm": {"model": "m"}})

    def test_answers_that_dont_check_out(self):
        for answers, why in (
            ({"backend": "podman"}, "Docker or microsandbox"),
            ({"backend": "microsandbox"}, "can't run"),
            ({"target": "huge"}, "full or lean"),
            ({"port": 80}, "between 1024"),
            ({"port": "x"}, "number"),
            ({"namespace": "no spaces"}, "lowercase"),
            ({"ref": "main; rm -rf /"}, "branch or tag"),
        ):
            with self.subTest(answers=answers), self.assertRaisesRegex(ValueError, why):
                li.validate(answers, facts())

    def test_a_folder_with_other_files_is_refused(self):
        with tempfile.TemporaryDirectory() as d:
            open(os.path.join(d, "notes.txt"), "w").close()
            with self.assertRaisesRegex(ValueError, "other files"):
                li.validate({"dir": d}, facts())
            os.mkdir(os.path.join(d, ".git"))  # an install already: installed again in place
            self.assertEqual(li.validate({"dir": d}, facts())["LENS_DIR"], d)

    def test_install_sh_headlines(self):
        self.assertEqual(li.step_of("\x1b[1mWaiting for the web app...\x1b[0m"), "Waiting for the web app...")
        self.assertIsNone(li.step_of("plain output"))

    def test_drop_env_keeps_the_rest_and_the_mode(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            with open(p, "w") as f:
                f.write("A=1\nLENS_SETUP_SETTINGS=abc\nB=2\n")
            os.chmod(p, 0o600)
            self.assertTrue(li.drop_env(p, "LENS_SETUP_SETTINGS"))
            with open(p) as f:
                self.assertEqual(f.read(), "A=1\nB=2\n")
            if os.name == "posix":  # Windows has no such modes
                self.assertEqual(stat.S_IMODE(os.stat(p).st_mode), 0o600)
            self.assertFalse(li.drop_env(p, "LENS_SETUP_SETTINGS"))

    def test_plan_lists_docker_when_it_is_missing(self):
        docker = {"installed": False, "running": False, "needs_sudo": False, "compose": False}
        items = li.plan("docker", docker, "password")
        if sys.platform.startswith("linux"):
            self.assertEqual(items, [{"what": "Docker Engine with Compose (from get.docker.com)", "admin": True}])
        self.assertEqual(li.plan("docker", {**docker, "installed": True, "running": True, "compose": True}, "root"), [])

    def test_log_tail_is_what_lens_logged_without_the_reason(self):
        lines = ["Waiting for the web app...", li.TAIL_HEAD, "[lens] Building Lens...", "error: boom", "", "Lens couldn't start."]
        self.assertEqual(li.log_tail(lines, "Lens couldn't start."), ["[lens] Building Lens...", "error: boom"])
        self.assertEqual(li.log_tail(["Docker isn't running."], "Docker isn't running."), [])

    def test_lens_app_offers_microsandbox_first_where_it_runs(self):
        inst = li.Installer("/tmp/lens-x", "adeelahmad/lense", "main", token="t")
        for wanted, can, expect in (("microsandbox", True, "microsandbox"), ("microsandbox", False, "docker"), ("", True, "docker")):
            with mock.patch.dict(os.environ, {"LENS_BACKEND_DEFAULT": wanted}), mock.patch.object(li, "msb_ok", return_value=can):
                inst._facts = None
                self.assertEqual(inst.facts()["backend_default"], expect)


class Server(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.script = os.path.join(self.tmp.name, "install.sh")
        with open(self.script, "w") as f:
            f.write(FAKE)
        self.dir = os.path.join(self.tmp.name, "lens")
        self.inst = li.Installer(self.dir, "adeelahmad/lense", "main", self.script, token="t0ken")
        self.inst._facts = {**self.inst.facts(), "plan": {"docker": [], "microsandbox": []}, "sudo": "root"}
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), None)
        self.httpd.RequestHandlerClass = li.make_handler(self.inst, {"localhost", "127.0.0.1"}, self.httpd.shutdown)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = self.httpd.server_address[1]

    def tearDown(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.tmp.cleanup()

    def call(self, method, path, body=None, host=None, cookie=True, header=True):
        c = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": host or f"localhost:{self.port}"}
        if cookie:
            h["Cookie"] = "lens_installer=t0ken"
        if header and method == "POST":
            h["X-Lens-Installer"] = "1"
        if body is not None:
            h["Content-Type"] = "application/json"
        c.request(method, path, json.dumps(body) if body is not None else None, h)
        r = c.getresponse()
        data = r.read()
        c.close()
        return r.status, r.getheader("Set-Cookie"), data

    def test_only_the_link_opens_it(self):
        self.assertEqual(self.call("GET", "/api/system", cookie=False)[0], 403)
        self.assertEqual(self.call("GET", "/?t=wrong", cookie=False)[0], 403)
        status, cookie, _ = self.call("GET", "/?t=t0ken", cookie=False)
        self.assertEqual(status, 303)
        self.assertIn("lens_installer=t0ken", cookie)
        self.assertIn("HttpOnly", cookie)
        self.assertEqual(self.call("GET", "/")[0], 200)

    def test_other_sites_cant_use_it(self):
        self.assertEqual(self.call("GET", "/api/system", host="evil.example:80")[0], 403)  # DNS rebinding
        self.assertEqual(self.call("POST", "/api/install", {}, header=False)[0], 403)  # a form from elsewhere

    def test_install_runs_install_sh_with_the_answers(self):
        status, _, data = self.call("GET", "/api/system")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["dir"], self.dir)
        answers = {"backend": "docker", "port": 3123, "dir": self.dir, "settings": {"llm": {"api_key": "sk-1"}}}
        self.assertEqual(self.call("POST", "/api/install", answers)[0], 202)
        progress = self.wait()
        self.assertEqual(progress["state"], "done", progress)
        self.assertEqual(progress["url"], "http://localhost:3123/setup?code=abc123")
        self.assertEqual(progress["steps"][0], f"Getting Lens into {self.dir}...")
        env = li.read_env(os.path.join(self.dir, ".env"))
        self.assertEqual(env["SEEN"], "011")  # asks nothing, installs without asking, opens no browser itself
        self.assertNotIn("LENS_SETUP_SETTINGS", env)  # Lens has it by now: the key doesn't stay on disk

    def test_a_failed_install_says_why(self):
        os.environ["FAIL"] = "1"
        try:
            self.assertEqual(self.call("POST", "/api/install", {"dir": self.dir})[0], 202)
            progress = self.wait()
        finally:
            del os.environ["FAIL"]
        self.assertEqual(progress["state"], "failed")
        self.assertIn("Docker isn't running", progress["error"])

    def test_a_failed_start_shows_what_lens_logged(self):
        os.environ["FAIL"] = "2"
        try:
            self.assertEqual(self.call("POST", "/api/install", {"dir": self.dir})[0], 202)
            progress = self.wait()
        finally:
            del os.environ["FAIL"]
        self.assertEqual(progress["state"], "failed")
        self.assertIn("API keeps stopping", progress["error"])
        self.assertEqual(
            progress["tail"], ["[lens] api stopped; starting it again in 5 seconds", "cannot open SurrealDB: authentication failed"]
        )

    def test_bad_answers_are_refused(self):
        status, _, data = self.call("POST", "/api/install", {"backend": "podman"})
        self.assertEqual(status, 400)
        self.assertIn("Docker or microsandbox", json.loads(data)["error"])

    def test_admin_password_is_needed_when_the_plan_asks(self):
        self.inst._facts["plan"]["docker"] = [{"what": "Docker", "admin": True}]
        status, _, data = self.call("POST", "/api/install", {"dir": self.dir})
        self.assertEqual(status, 400)
        self.assertIn("password", json.loads(data)["error"])

    def wait(self):
        for _ in range(100):
            _, _, data = self.call("GET", "/api/progress")
            p = json.loads(data)
            if p["state"] not in ("idle", "running"):
                return p
            time.sleep(0.1)
        return p


if __name__ == "__main__":
    unittest.main()
