#!/usr/bin/env python3
"""Nebula for Lens: the optional `mesh` container (docs/mesh.md), the only part of Lens that holds NET_ADMIN.

It runs Nebula with the config Lens's server writes, and relays the ports the mesh serves on this machine to the
containers behind them. It makes this machine's Nebula key pair itself, so the private key never leaves it: Lens
signs the public key and sends back the certificate inside the config.

Files in the folder it shares with Lens's server (LENS_MESH_DIR, /mesh):

* `lens.json`, from Lens: {"on", "ip", "public_key", "version", "config"}: the Nebula config without the private key,
  and the public key Lens signed it for (none yet: Lens is waiting for this machine's). No file, or "on" false: the
  mesh is off, and Nebula stops.
* `host.pub`, from here: this machine's public key, for Lens to sign.
* `status.json`, from here: whether Nebula runs, the relays, Nebula's last lines.

In its own volume (LENS_MESH_STATE, /state): Nebula's programs, fetched on first use from Nebula's release with their
checksum checked against the ones kept here (the same as fastapi_backend/app/domain/mesh.py's), the private key and
the config Nebula runs.

LENS_MESH_RELAYS says what to relay: "8000=surrealdb:8000,8080=backend:8000" listens on this machine's mesh address,
port 8000 and 8080, and passes each connection to the database and the API. Nebula's firewall, in the config Lens
writes, decides which machines reach each one. LENS_MESH_LISTEN_PORT is the UDP port Nebula listens on in here (4242;
Docker publishes it).

Python's standard library only, so it runs in the plain python image.
"""

from __future__ import annotations

import hashlib
import json
import os
import pathlib
import platform
import shutil
import signal
import socket
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

NEBULA_VERSION = "1.11.2"
RELEASES = f"https://github.com/slackhq/nebula/releases/download/v{NEBULA_VERSION}/"
# Nebula's own SHASUM256.txt for these downloads (kept equal to mesh.py's by tests/api/test_mesh_service.py)
SHA256 = {
    "nebula-linux-amd64.tar.gz": "6140d33f2ec21ce7f6b655b5bc820e93a684d97e51d0ddcf907324b5b28aac1e",
    "nebula-linux-arm64.tar.gz": "85d10e7bc2d121193c1392a1a919172ded7c413f46e602138281cfa9fa1b0231",
    "nebula-linux-arm-7.tar.gz": "dc9c890d7ec014ab709e0444592b33f35db4f396f28245c95887844b1565ff41",
    "nebula-linux-arm-6.tar.gz": "d8f86452620eb6ab76bdf9ed9841a309ba8122ac2980c2b7c7e2d1aa8d160e84",
}
LOG_LINES = 60
TICK = 2.0
RETRY = (5, 15, 30, 60, 120)
# a change to these needs Nebula started again; the rest it takes on SIGHUP
RESTART_KEYS = ("tun", "listen", "cipher")


def say(*parts):
    print("lens-mesh:", *parts, flush=True)


# ---------- Nebula's programs ----------
def release_name(machine=None):
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "arm-7", "armv6l": "arm-6"}.get(
        (machine or platform.machine()).lower()
    )
    if not arch:
        raise RuntimeError(f"Nebula has no Linux release for {machine or platform.machine()}")
    return f"nebula-linux-{arch}.tar.gz"


def programs(state, fetch=True, opener=None):
    """{"nebula": path, "nebula-cert": path}: LENS_NEBULA_DIR's, or the release fetched into the state folder."""
    given = os.environ.get("LENS_NEBULA_DIR")
    if given:
        return {p: os.path.join(given, p) for p in ("nebula", "nebula-cert")}
    out = pathlib.Path(state) / "bin" / NEBULA_VERSION
    found = {p: str(out / p) for p in ("nebula", "nebula-cert")}
    if all(os.access(p, os.X_OK) for p in found.values()):
        return found
    if not fetch:
        return None
    name = release_name()
    out.mkdir(parents=True, exist_ok=True)
    part = out / (name + ".part")
    say(f"downloading Nebula {NEBULA_VERSION} ({name})")
    h = hashlib.sha256()
    with (opener or urllib.request.urlopen)(RELEASES + name, timeout=300) as r, open(part, "wb") as f:
        while chunk := r.read(1 << 16):
            h.update(chunk)
            f.write(chunk)
    try:
        if h.hexdigest() != SHA256[name]:
            raise RuntimeError("the Nebula download doesn't match its checksum")
        with tarfile.open(part) as t:
            for prog in ("nebula", "nebula-cert"):
                src = t.extractfile(t.getmember(prog))
                if src is None:
                    raise RuntimeError(f"the Nebula download has no {prog}")
                with src, open(out / f"{prog}.new", "wb") as f:
                    shutil.copyfileobj(src, f)
                (out / f"{prog}.new").chmod(0o755)
                (out / f"{prog}.new").replace(out / prog)
    finally:
        part.unlink(missing_ok=True)
    return found


def key_pair(state, cert_tool):
    """This machine's key pair, made once: (private key path, public key text)."""
    key, pub = pathlib.Path(state) / "host.key", pathlib.Path(state) / "host.pub"
    if not (key.exists() and pub.exists()):
        for p in (key, pub):
            p.unlink(missing_ok=True)
        old = os.umask(0o077)
        try:
            subprocess.run([cert_tool, "keygen", "-out-key", str(key), "-out-pub", str(pub)], check=True, capture_output=True, timeout=60)
        finally:
            os.umask(old)
        key.chmod(0o600)
    return str(key), pub.read_text()


def write(path, text, mode=0o644):
    """Write a file whole, so a reader never sees half of it."""
    path = pathlib.Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(text)
    tmp.chmod(mode)
    tmp.replace(path)


# ---------- the config ----------
def read_lens(shared):
    """lens.json while the mesh is on, else None (off, or Lens hasn't written it yet)."""
    try:
        d = json.loads((pathlib.Path(shared) / "lens.json").read_text())
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) and d.get("on") else None


def nebula_config(lens, key_path, listen_port=None):
    """The config Nebula runs: Lens's, with this machine's private key, and the port Nebula listens on in here."""
    c = json.loads(json.dumps(lens["config"]))
    c.setdefault("pki", {})["key"] = key_path
    if listen_port:
        c.setdefault("listen", {})["port"] = int(listen_port)
    return c


def needs_restart(old, new):
    return any((old or {}).get(k) != new.get(k) for k in RESTART_KEYS)


def relays(spec):
    """ "8000=surrealdb:8000,8080=backend:8000" as [(8000, ("surrealdb", 8000)), ...]."""
    out = []
    for part in (spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        port, _, to = part.partition("=")
        host, _, to_port = to.rpartition(":")
        if not (port.isdigit() and to_port.isdigit() and host):
            raise ValueError(f"LENS_MESH_RELAYS: {part!r} isn't port=host:port")
        out.append((int(port), (host, int(to_port))))
    return out


# ---------- relays ----------
class Relay:
    """Listens on the mesh address and passes each connection on to `to`; the mesh's firewall decides who connects."""

    def __init__(self, port, to):
        self.port, self.to = port, to
        self.ip = None
        self.sock: socket.socket | None = None
        self.error = None
        self.open = 0

    def view(self):
        return {
            "port": self.port,
            "to": f"{self.to[0]}:{self.to[1]}",
            "listening": self.sock is not None,
            "open": self.open,
            "error": self.error,
        }

    def ensure(self, ip):
        """Listen on ip:port (the mesh address appears once Nebula is up, so this is tried each tick)."""
        if self.sock and self.ip == ip:
            return
        self.close()
        try:
            s = socket.create_server((ip, self.port), reuse_port=False)
        except OSError as e:
            self.error = f"can't listen on {ip}:{self.port} yet: {e.strerror or e}"
            return
        self.ip, self.sock, self.error = ip, s, None
        threading.Thread(target=self._accept, args=(s,), daemon=True, name=f"relay-{self.port}").start()

    def close(self):
        s, self.sock = self.sock, None
        if s:
            try:  # wakes the accept() blocked on it, which close() alone doesn't on Linux
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            s.close()

    def _accept(self, s):
        while self.sock is s:
            try:
                conn, _ = s.accept()
            except OSError:
                return
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn):
        try:
            up = socket.create_connection(self.to, timeout=10)
        except OSError as e:
            self.error = f"{self.to[0]}:{self.to[1]} didn't answer: {e.strerror or e}"
            conn.close()
            return
        up.settimeout(None)
        self.open += 1
        done = threading.Event()

        def pump(a, b):
            try:
                while chunk := a.recv(1 << 16):
                    b.sendall(chunk)
            except OSError:
                pass
            finally:
                done.set()

        threading.Thread(target=pump, args=(up, conn), daemon=True).start()
        pump(conn, up)
        done.wait(1)
        for x in (conn, up):
            try:
                x.close()
            except OSError:
                pass
        self.open -= 1


# ---------- the service ----------
class Service:
    def __init__(self, shared, state, relay_spec="", listen_port=None, fetch=True):
        self.shared, self.state = pathlib.Path(shared), pathlib.Path(state)
        self.relays = [Relay(p, to) for p, to in relays(relay_spec)]
        self.listen_port = listen_port
        self.fetch = fetch
        self.proc: subprocess.Popen | None = None
        self.running_config = None
        self.lines: list[str] = []
        self.error = None
        self.state_word = "off"
        self.since = None
        self.restarts = 0
        self.fails = 0
        self.next_try = 0.0
        self.config_version = None
        self.lock = threading.Lock()

    # what Lens reads
    def status(self):
        with self.lock:
            lines = list(self.lines[-LOG_LINES:])
        return {
            "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "state": self.state_word,
            "nebula": self.proc is not None and self.proc.poll() is None,
            "pid": self.proc.pid if self.proc and self.proc.poll() is None else None,
            "since": self.since,
            "restarts": self.restarts,
            "version": NEBULA_VERSION,
            "config_version": self.config_version,
            "relays": [r.view() for r in self.relays],
            "error": self.error,
            "log": lines,
        }

    def save(self):
        write(self.shared / "status.json", json.dumps(self.status(), indent=1))

    def _read(self, proc):
        for raw in proc.stdout:
            line = raw.rstrip()
            if line:
                print(line, flush=True)  # docker logs shows it too
                with self.lock:
                    self.lines = (self.lines + [line[:400]])[-LOG_LINES:]

    def stop(self):
        p, self.proc = self.proc, None
        for r in self.relays:
            r.close()
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()
        self.running_config = None

    def _start(self, exe, path, config):
        self.proc = subprocess.Popen([exe, "-config", str(path)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1)
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="nebula-log").start()
        self.running_config = config
        self.since = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        say(f"started Nebula (pid {self.proc.pid})")

    def _failed(self, msg):
        self.error = msg
        self.state_word = "error"
        self.fails += 1
        self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]

    def tick(self):
        self.state.mkdir(parents=True, exist_ok=True)
        lens = read_lens(self.shared)
        if not lens:
            if self.proc:
                say("the mesh is off: stopping Nebula")
            self.stop()
            self.state_word, self.error, self.config_version = "off", None, None
            return
        if time.monotonic() < self.next_try:
            return
        try:
            exe = programs(self.state, fetch=self.fetch)
            if not exe:
                return self._failed("Nebula isn't here yet")
            key, pub = key_pair(self.state, exe["nebula-cert"])
            mine = self.shared / "host.pub"
            if not mine.exists() or mine.read_text() != pub:
                write(mine, pub)
        except Exception as e:  # noqa: BLE001 - says why in status.json, tries again later
            return self._failed(f"{type(e).__name__}: {e}")
        if not isinstance(lens.get("config"), dict) or not lens.get("ip") or (lens.get("public_key") or "").strip() != pub.strip():
            self.stop()
            self.state_word, self.error = "waiting", "waiting for Lens to sign this machine's key"
            return
        config = nebula_config(lens, key, self.listen_port)
        path = self.state / "nebula.json"
        alive = self.proc is not None and self.proc.poll() is None
        if self.proc and not alive:  # it stopped by itself: say why, and start it again after a while
            code = self.proc.returncode
            with self.lock:
                last = self.lines[-1] if self.lines else "no output"
            self.restarts += 1
            self.stop()
            return self._failed(f"Nebula stopped (exit {code}): {last}")
        if config != self.running_config or not alive:
            nxt = self.state / "nebula.next.json"
            write(nxt, json.dumps(config, indent=1), 0o600)
            test = subprocess.run([exe["nebula"], "-test", "-config", str(nxt)], capture_output=True, text=True, timeout=60)
            if test.returncode != 0:
                return self._failed("Nebula refused the config: " + (test.stderr or test.stdout).strip()[-300:])
            nxt.replace(path)
            if alive and not needs_restart(self.running_config, config):
                self.proc.send_signal(signal.SIGHUP)
                self.running_config = config
                say("the config changed: Nebula reloaded it")
            else:
                self.stop()
                self._start(exe["nebula"], path, config)
            self.config_version = lens.get("version")
        for r in self.relays:
            r.ensure(lens["ip"])
        waiting = [r for r in self.relays if not r.sock]
        self.state_word = "starting" if waiting else "running"
        self.error = waiting[0].error if waiting else None
        if not waiting:
            self.fails = 0

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep running; the error is in status.json
                self._failed(f"{type(e).__name__}: {e}")
            try:
                self.save()
            except OSError as e:
                say(f"can't write status.json: {e}")
            stop.wait(TICK)
        self.stop()
        self.state_word = "off"
        try:
            self.save()
        except OSError:
            pass


def main():
    shared = pathlib.Path(os.environ.get("LENS_MESH_DIR") or "/mesh")
    state = pathlib.Path(os.environ.get("LENS_MESH_STATE") or "/state")
    shared.mkdir(parents=True, exist_ok=True)
    shared.chmod(0o1777)  # Lens's server runs as another user: it writes lens.json here and reads the rest
    os.umask(0o022)
    svc = Service(shared, state, os.environ.get("LENS_MESH_RELAYS", ""), os.environ.get("LENS_MESH_LISTEN_PORT") or None)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    say(f"waiting for Lens to write {shared / 'lens.json'} (it does once a node is added)")
    svc.loop(stop)
    return 0


if __name__ == "__main__":
    sys.exit(main())
