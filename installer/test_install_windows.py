"""install_windows.py, end to end with a stand-in docker and a local copy of the code (it runs on any system, so CI
tries it on Windows, macOS and Linux).

  python3 -m unittest discover installer
"""

from __future__ import annotations

import io
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import zipfile
from http.server import BaseHTTPRequestHandler, HTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import install_windows as iw  # noqa: E402

FAKE_DOCKER = """import os, sys
with open(os.environ["FAKE_DOCKER_LOG"], "a") as f:
    f.write(" ".join(sys.argv[1:]) + "\\n")
sys.exit(int(os.environ.get("FAKE_DOCKER_FAIL_UP", "0")) if "up" in sys.argv else 0)
"""


class Answer(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *a):
        pass


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Windows(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        bin_dir = os.path.join(t, "bin")
        os.mkdir(bin_dir)
        with open(os.path.join(bin_dir, "fake_docker.py"), "w") as f:
            f.write(FAKE_DOCKER)
        if sys.platform == "win32":
            with open(os.path.join(bin_dir, "docker.cmd"), "w") as f:
                f.write(f'@"{sys.executable}" "%~dp0fake_docker.py" %*\r\n')
        else:
            p = os.path.join(bin_dir, "docker")
            with open(p, "w") as f:
                f.write(f'#!/bin/sh\nexec "{sys.executable}" "$(dirname "$0")/fake_docker.py" "$@"\n')
            os.chmod(p, 0o755)
        # the code, as GitHub's archive has it: one top folder
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("lense-main/docker-compose.prod.yml", "services: {}\n")
            z.writestr("lense-main/install.sh", "#!/bin/sh\n")
            z.writestr("lense-main/../escape.txt", "never outside the folder\n")
        self.zip = os.path.join(t, "lense.zip")
        with open(self.zip, "wb") as f:
            f.write(buf.getvalue())
        self.port = free_port()
        self.web = HTTPServer(("127.0.0.1", self.port), Answer)
        threading.Thread(target=self.web.serve_forever, daemon=True).start()
        self.dir = os.path.join(t, "lens")
        self.log = os.path.join(t, "docker.log")
        path = bin_dir + os.pathsep + os.environ.get("PATH", "")
        if sys.platform != "win32":  # no git: the archive path, as on a fresh Windows
            path = bin_dir + os.pathsep + "/usr/bin" + os.pathsep + "/bin"
        self.env = {
            **os.environ,
            "PATH": path,
            "FAKE_DOCKER_LOG": self.log,
            "LENS_DIR": self.dir,
            "LENS_SOURCE": self.zip,
            "LENS_PORT": str(self.port),
            "LENS_TARGET": "lean",
            "LENS_SETUP_SETTINGS": "eyJsbG0iOnt9fQ",
            "LENS_WAIT_SECONDS": "20",
            "PYTHONIOENCODING": "utf-8",
        }

    def tearDown(self):
        self.web.shutdown()
        self.web.server_close()
        self.tmp.cleanup()

    def install(self, **extra):
        return subprocess.run(
            [sys.executable, os.path.join(HERE, "install_windows.py")],
            env={**self.env, **extra},
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_installs_and_hands_over_the_setup_address(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"Lens is running: http://localhost:{self.port}/setup?code=", r.stdout)
        self.assertIn("\x1b[1mGetting Lens into", r.stdout)
        self.assertTrue(os.path.exists(os.path.join(self.dir, "docker-compose.prod.yml")))
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "escape.txt")))
        env, _ = iw.read_env(os.path.join(self.dir, ".env"))
        for k in ("ACCESS_SECRET_KEY", "ARCHIVE_SECRET_KEY", "AUTH_SECRET", "SURREAL_PASS"):
            self.assertRegex(env[k], r"^[0-9a-f]{64}$")
        self.assertRegex(env["LENS_SETUP_CODE"], r"^[A-Za-z0-9]{12}$")
        self.assertEqual((env["LENS_TARGET"], env["LENS_BIND"], env["LENS_PORT"]), ("lean", "127.0.0.1", str(self.port)))
        self.assertEqual(env["LENS_SETUP_SETTINGS"], "eyJsbG0iOnt9fQ")
        with open(self.log) as f:
            self.assertIn("compose -f docker-compose.prod.yml up -d --build", f.read())

        # again: an update in place that keeps the secrets
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Updating Lens in", r.stdout)
        again, _ = iw.read_env(os.path.join(self.dir, ".env"))
        for k in ("ACCESS_SECRET_KEY", "AUTH_SECRET", "LENS_SETUP_CODE"):
            self.assertEqual(again[k], env[k])

    def test_a_failed_build_says_so(self):
        r = self.install(FAKE_DOCKER_FAIL_UP="1")
        self.assertEqual(r.returncode, 1)
        self.assertIn("Docker couldn't build or start Lens", r.stderr)

    def test_env_lines_keep_their_order(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, ".env")
            iw.write_env(p, ["# note", "A=1", "B=2"], {"B": "3", "C": "4"})
            with open(p) as f:
                self.assertEqual(f.read(), "# note\nA=1\nB=3\nC=4\n")


if __name__ == "__main__":
    unittest.main()
