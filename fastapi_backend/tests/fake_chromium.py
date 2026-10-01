"""A Chromium for tests that speaks just enough of the DevTools protocol over its pipe (its fds 3 and 4, JSON ending in
a NUL) to print a page: it makes a target, navigates, says the page loaded and went quiet, and prints a "PDF". Its mode
says how it behaves:

- able: it prints.
- no-userns: it can't start its sandbox (as with Ubuntu's AppArmor): it prints only with --no-sandbox.
- broken: it fails as it starts.
- slow-bus: on a system bus that's slow to start what it asks for (as on GitHub's runners), it waits 70 s unless it
  has no bus to reach; it prints its profile's Preferences after the PDF header.
"""

from __future__ import annotations

import sys

SCRIPT = r"""#!@PYTHON@
import base64, json, os, sys, time

MODE = @MODE@
args = sys.argv[1:]
profile = next((a.split("=", 1)[1] for a in args if a.startswith("--user-data-dir=")), "")
if MODE == "broken":
    print("[1:1:FATAL:zygote_host_impl_linux.cc(1)] It broke.", file=sys.stderr)
    sys.exit(1)
if MODE == "no-userns" and "--no-sandbox" not in args:
    print("[1:1:FATAL:credentials.cc(127)] Check failed: . : Permission denied (13)", file=sys.stderr)
    sys.exit(1)
pdf = b"%PDF-1.4\n"
if MODE == "slow-bus":
    nowhere = f"unix:path={profile}/no-bus"
    if (os.environ.get("DBUS_SYSTEM_BUS_ADDRESS"), os.environ.get("DBUS_SESSION_BUS_ADDRESS")) != (nowhere, nowhere):
        time.sleep(70)
    try:
        pdf += open(os.path.join(profile, "Default", "Preferences"), "rb").read()
    except OSError:
        pass


def send(m):
    os.write(4, json.dumps(m).encode() + b"\0")


buf = b""
while True:
    while b"\0" not in buf:
        chunk = os.read(3, 65536)
        if not chunk:
            sys.exit(0)
        buf += chunk
    raw, buf = buf.split(b"\0", 1)
    m = json.loads(raw)
    method, result = m["method"], {}
    if method == "Target.createTarget":
        result = {"targetId": "T"}
    elif method == "Target.attachToTarget":
        result = {"sessionId": "S"}
    elif method == "Page.navigate":
        result = {"frameId": "T", "loaderId": "L"}
    elif method == "Page.printToPDF":
        result = {"data": base64.b64encode(pdf).decode()}
    send({"id": m["id"], "result": result, **({"sessionId": m["sessionId"]} if "sessionId" in m else {})})
    if method == "Page.navigate":
        for name in ("load", "networkIdle"):
            send({"method": "Page.lifecycleEvent", "params": {"frameId": "T", "loaderId": "L", "name": name}, "sessionId": "S"})
    if method == "Browser.close":
        sys.exit(0)
"""


def make(path, mode):
    """A fake Chromium at `path` (a pathlib.Path) behaving as `mode` says; its path, as a string."""
    path.write_text(SCRIPT.replace("@PYTHON@", sys.executable).replace("@MODE@", repr(mode)))
    path.chmod(0o755)
    return str(path)
