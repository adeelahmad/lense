"""Lens on Windows: what install.sh does elsewhere, for Docker Desktop.

The web installer (lens_installer.py) runs this on Windows with the answers in the environment, as it runs install.sh
on Linux and macOS: it installs Docker Desktop if it's missing (with winget; Windows asks for permission), turns on
WSL if Docker needs it, gets the code (git, else the release archive), writes the secrets once into .env, builds and
starts the stack with Docker Compose, waits until the web app answers and prints the setup address. Run again, it
updates in place and keeps the secrets. Headlines are printed in bold, as install.sh's are, for the installer to show.

Settings (environment): LENS_DIR, LENS_REPO, LENS_REF, LENS_PORT, LENS_PUBLIC, LENS_TARGET, LENS_NAMESPACE,
LENS_SETUP_SETTINGS, GITHUB_TOKEN; LENS_SOURCE (a .zip file or URL to take the code from instead of GitHub; tests).
"""

from __future__ import annotations

import io
import os
import re
import secrets
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile

WINDOWS = sys.platform == "win32"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
DOCKER_DIRS = (r"C:\Program Files\Docker\Docker\resources\bin",)
DOCKER_APP = r"C:\Program Files\Docker\Docker\Docker Desktop.exe"
RESTART = 3  # exit code: Windows must restart before Docker can run (WSL was just turned on)


class Stop(Exception):
    pass


def say(text):
    print(f"\033[1m{text}\033[0m", flush=True)


def which(cmd):
    found = shutil.which(cmd)
    if not found and WINDOWS:
        for d in DOCKER_DIRS:
            p = os.path.join(d, cmd + ".exe")
            if os.path.exists(p):
                return p
    return found


def run(args, timeout=None, stream=False, cwd=None):
    """Exit status and output; with stream, the output goes straight on to ours."""
    exe = which(args[0]) or args[0]
    try:
        if stream:
            p = subprocess.Popen(
                [exe, *args[1:]],
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
            assert p.stdout is not None
            for raw in iter(p.stdout.readline, b""):
                print(raw.decode("utf-8", errors="replace").rstrip("\r\n"), flush=True)
            return p.wait(), ""
        p = subprocess.run(
            [exe, *args[1:]], cwd=cwd, capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL, creationflags=NO_WINDOW
        )
        return p.returncode, (p.stdout or "") + (p.stderr or "")
    except (OSError, subprocess.TimeoutExpired):
        return 127, ""


def add_docker_to_path():
    for d in DOCKER_DIRS:
        if os.path.isdir(d) and d not in os.environ.get("PATH", ""):
            os.environ["PATH"] = d + os.pathsep + os.environ.get("PATH", "")


def ensure_docker():
    add_docker_to_path()
    if not which("docker"):
        if not which("winget"):
            raise Stop(
                "Docker Desktop is needed. Install it from https://docs.docker.com/desktop/setup/install/windows-install/, then try again."
            )
        say("Installing Docker Desktop (winget; Windows asks for permission)...")
        code, _ = run(
            [
                "winget",
                "install",
                "-e",
                "--id",
                "Docker.DockerDesktop",
                "--silent",
                "--accept-package-agreements",
                "--accept-source-agreements",
            ],
            stream=True,
        )
        add_docker_to_path()
        if not which("docker"):
            raise Stop(f"Docker Desktop didn't install (winget exit {code}). Install it yourself, then try again.")
    if run(["docker", "info"], timeout=60)[0] == 0:
        return
    if WINDOWS and run(["wsl", "--status"], timeout=30)[0] != 0:
        say("Turning on WSL, which Docker Desktop runs on (Windows asks for permission)...")
        ps = "Start-Process wsl -ArgumentList '--install','--no-distribution' -Verb RunAs -Wait"
        run(["powershell", "-NoProfile", "-Command", ps], timeout=1800)
        if run(["wsl", "--status"], timeout=30)[0] != 0:
            print("WSL is on, but Windows has to restart before Docker Desktop can use it.", flush=True)
            raise Stop("Restart Windows, then open Install Lens again: it carries on from here.")
    if WINDOWS and os.path.exists(DOCKER_APP):
        say("Starting Docker Desktop...")
        subprocess.Popen([DOCKER_APP], creationflags=NO_WINDOW, close_fds=True)
    say("Waiting for Docker to start...")
    for _ in range(150):  # 5 minutes: the first start sets up its virtual machine
        if run(["docker", "info"], timeout=30)[0] == 0:
            return
        time.sleep(2)
    raise Stop("Docker isn't running. Start Docker Desktop, wait until it says it's running, then try again.")


def get_code(folder, repo, ref):
    token = os.environ.get("GITHUB_TOKEN")
    source = os.environ.get("LENS_SOURCE")
    if os.path.isdir(os.path.join(folder, ".git")) and which("git"):
        say(f"Updating Lens in {folder}...")
        url = f"https://github.com/{repo}.git"
        if run(["git", "-C", folder, "fetch", "-q", "--depth", "1", url, ref], timeout=600)[0] != 0:
            raise Stop(f"Couldn't get the code from GitHub ({ref}). Check the network, and try again.")
        run(["git", "-C", folder, "checkout", "-q", "-B", ref, "FETCH_HEAD"], timeout=120)
        return
    fresh = not os.path.exists(os.path.join(folder, "docker-compose.prod.yml"))
    say(("Getting Lens into " if fresh else "Updating Lens in ") + folder + "...")
    if fresh and not source and which("git"):
        url = f"https://x-access-token:{token}@github.com/{repo}.git" if token else f"https://github.com/{repo}.git"
        if run(["git", "clone", "-q", "--depth", "1", "--branch", ref, url, folder], timeout=1800)[0] == 0:
            run(["git", "-C", folder, "remote", "set-url", "origin", f"https://github.com/{repo}.git"])
            return
    # the archive of that branch or tag, unpacked over what's there (.env and the data volumes stay)
    try:
        if source and os.path.exists(source):
            with open(source, "rb") as f:
                data = f.read()
        else:
            req = urllib.request.Request(source or f"https://github.com/{repo}/archive/{ref}.zip", headers={"User-Agent": "lens-installer"})
            if token and not source:
                req.add_header("Authorization", f"Bearer {token}")
            with urllib.request.urlopen(req, timeout=600) as r:
                data = r.read()
        unpack(data, folder)
    except (OSError, urllib.error.URLError, zipfile.BadZipFile) as e:
        raise Stop(f"Couldn't get the code from GitHub ({ref}): {e}. Check the network, and try again.") from None


def unpack(data, folder):
    """A GitHub archive (one top folder) into folder."""
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        for info in z.infolist():
            parts = info.filename.split("/", 1)
            if len(parts) < 2 or not parts[1]:
                continue
            target = os.path.normpath(os.path.join(folder, parts[1]))
            if not target.startswith(os.path.normpath(folder) + os.sep):
                continue  # never outside the folder
            if info.is_dir():
                os.makedirs(target, exist_ok=True)
                continue
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with z.open(info) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)


def port_free(port):
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex(("127.0.0.1", port)) != 0


def read_env(path):
    out, lines = {}, []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
    for line in lines:
        k, sep, v = line.partition("=")
        if sep:
            out[k] = v
    return out, lines


def write_env(path, lines, values):
    """Set keys in .env, keeping the other lines and their order."""
    done = set()
    out = []
    for line in lines:
        k = line.partition("=")[0]
        if k in values:
            out.append(f"{k}={values[k]}")
            done.add(k)
        else:
            out.append(line)
    out += [f"{k}={v}" for k, v in values.items() if k not in done]
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")


def lan_address():
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))
            return s.getsockname()[0]
    except OSError:
        return None


def answers(url, timeout=3):
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=timeout) as r:
            return 200 <= r.status < 400
    except (OSError, urllib.error.URLError):
        return False


def main():
    folder = os.path.abspath(os.path.expanduser(os.environ.get("LENS_DIR") or "~/lens"))
    repo = os.environ.get("LENS_REPO") or "adeelahmad/lense"
    ref = os.environ.get("LENS_REF") or "main"
    target = os.environ.get("LENS_TARGET") or "full"
    try:
        ensure_docker()
        compose = ["docker", "compose"]
        if run(compose + ["version"], timeout=60)[0] != 0:
            raise Stop("Docker Compose is missing. Update Docker Desktop, then try again.")
        os.makedirs(folder, exist_ok=True)
        get_code(folder, repo, ref)

        env_path = os.path.join(folder, ".env")
        env, lines = read_env(env_path)
        port = os.environ.get("LENS_PORT") or env.get("LENS_PORT")
        if not port:
            port = next((str(p) for p in range(3000, 3101) if port_free(p)), None)
            if not port:
                raise Stop("No free port between 3000 and 3100. Choose one in the installer.")
        public = os.environ.get("LENS_PUBLIC", "1" if env.get("LENS_BIND") == "0.0.0.0" else "0") == "1"
        host = (lan_address() or "localhost") if public else "localhost"
        values = {}
        if not lines:  # once: changing them later would sign everyone out and make stored credentials unreadable
            lines = ["# Written by the Lens installer. Keep it with the volumes."]
            for k in ("ACCESS_SECRET_KEY", "ARCHIVE_SECRET_KEY", "AUTH_SECRET", "SURREAL_PASS"):
                values[k] = secrets.token_hex(32)
        if "LENS_SETUP_CODE" not in env:
            values["LENS_SETUP_CODE"] = "".join(
                secrets.choice("ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789") for _ in range(12)
            )
        values.update(
            {
                "LENS_BACKEND": "docker",
                "LENS_TARGET": target,
                "LENS_BIND": "0.0.0.0" if public else "127.0.0.1",
                "LENS_PORT": port,
                "FRONTEND_URL": f"http://{host}:{port}",
            }
        )
        for k in ("LENS_NAMESPACE", "LENS_SETUP_SETTINGS"):
            if os.environ.get(k):
                values[k] = os.environ[k]
        write_env(env_path, lines, values)
        code = values.get("LENS_SETUP_CODE") or env["LENS_SETUP_CODE"]

        say("Building and starting Lens (the first build takes a few minutes)...")
        status, _ = run(compose + ["-f", "docker-compose.prod.yml", "up", "-d", "--build"], cwd=folder, stream=True)
        if status != 0:
            raise Stop("Docker couldn't build or start Lens; the details are above.")

        say("Waiting for the web app...")
        base = f"http://127.0.0.1:{port}"
        deadline = time.time() + float(os.environ.get("LENS_WAIT_SECONDS") or 600)
        while not (answers(base + "/login") and answers(base + "/api/v1/auth/status")):
            if time.time() > deadline:
                raise Stop(f"Lens didn't answer in time. See what happened: in {folder}, docker compose -f docker-compose.prod.yml logs")
            time.sleep(2)
        say(f"Lens is running: http://{host}:{port}/setup?code={code}")
        return 0
    except Stop as e:
        print(str(e), file=sys.stderr, flush=True)
        return RESTART if "Restart Windows" in str(e) else 1


if __name__ == "__main__":
    if not re.match(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", os.environ.get("LENS_REPO") or "a/b"):
        sys.exit("LENS_REPO is owner/name")
    sys.exit(main())
