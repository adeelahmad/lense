"""anytopdf (github.com/adeelahmad/anytopdf-rs), the sister project, as a converter: documents and images made into
searchable PDFs that Lens then reads like any other (docs/api.md#documents-and-images).

It runs one of two ways, chosen in Settings → Documents:
- here: the anytopdf program, a single static binary that components.py downloads on first use (checked against its
  release checksum) unless documents.anytopdf names one. It reads emails, web pages, text and photographed pages with no
  Chromium, and flattens a photographed page before OCR; Office files still need LibreOffice beside it. Where Chromium
  and poppler's pdftoppm are here too, it renders a web page or email into page images (--html-render, offline) with
  the page's text as their search layer, so it keeps its look; without them it reads the page's text alone.
- on a conversion node: another machine running `anytopdf queue serve` and `anytopdf queue work` (documents.anytopdf_url,
  with its bearer token, documents.anytopdf_token). The node has LibreOffice and whatever else it needs, so a small
  server (a Raspberry Pi) reads Office files without installing anything.

documents.converter says when: `auto` (the default) uses it only for what Lens can't convert itself, `anytopdf` for
every document and image, `lens` never. Run here, it has no network, no runtime plugins (faces, objects and speech
stay Lens's own steps) and no config file of its own; its PDF has no provenance page, and its search layer holds the
document's text only, so what Lens reads from it is what the document says.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import shutil
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from . import netguard

VERSION = "0.4.0"
RELEASES = "https://github.com/adeelahmad/anytopdf-rs/releases/download"
# the release archives Lens fetches, by (system, machine), with the sha256 its release published (SHA256SUMS)
ARCHIVES = {
    ("Linux", "x86_64"): ("x86_64-unknown-linux-musl", "6f112cb8a961734dee5dbe32dc52418fb1dae59a13bbd7157e9cf03b11d98d1a"),
    ("Linux", "aarch64"): ("aarch64-unknown-linux-musl", "2478227d025b57e83df919de8eac1e413769a477ec55324ef461afdbe3e4e08f"),
    ("Darwin", "x86_64"): ("x86_64-apple-darwin", "9226586135d8bd49bee9b7a57e39af6913f39c06d31c3187856cd53a939d0e81"),
    ("Darwin", "arm64"): ("aarch64-apple-darwin", "6b0a803ab4cc1cf8cba3bb21adb874df5a9bea521b5149773b0f5c7ec7248cc5"),
}
MACHINES = {"amd64": "x86_64", "arm64": "arm64", "aarch64": "aarch64"}
# its search layer: the document's own text, not the dates, colours and places it would add
QUIET = ["--no-provenance-page", "--no-entities", "--colors", "off", "--location", "off"]
POLL_SECONDS = 1.0


class Unavailable(RuntimeError):
    """anytopdf isn't here, nor a conversion node set."""


def _opts(cfg):
    return cfg.get("documents") or {}


def archive(system=None, machine=None):
    """This machine's release archive: (file name, sha256), or None where anytopdf has no build."""
    system, machine = system or platform.system(), machine or platform.machine()
    found = ARCHIVES.get((system, MACHINES.get(machine.lower(), machine)))
    if system == "Darwin" and not found and machine.lower() == "aarch64":
        found = ARCHIVES.get(("Darwin", "arm64"))
    return (f"anytopdf-{VERSION}-{found[0]}.tar.gz", found[1]) if found else None


def fetched_path(cfg):
    """Where components.py keeps the downloaded program."""
    return pathlib.Path(cfg["data_dir"]) / "models" / f"anytopdf-{VERSION}" / "anytopdf"


def binary(cfg):
    """The anytopdf program to run here: documents.anytopdf, else the one downloaded, else one on PATH."""
    own = _opts(cfg).get("anytopdf")
    if own:
        return own if os.path.isfile(own) and os.access(own, os.X_OK) else None
    got = fetched_path(cfg)
    if got.is_file() and os.access(got, os.X_OK):
        return str(got)
    return shutil.which("anytopdf")


def node(cfg):
    """The conversion node's address, or None."""
    url = (_opts(cfg).get("anytopdf_url") or "").strip().rstrip("/")
    return url or None


def available(cfg):
    """Whether documents can be made into PDFs by anytopdf, and where: "node", "here" or None."""
    if node(cfg):
        return "node"
    return "here" if binary(cfg) else None


def mode(cfg):
    return _opts(cfg).get("converter") or "auto"


def fetch(cfg, say=None):
    """Download this machine's anytopdf release, check it against its checksum, and keep only the program."""
    import hashlib

    found = archive()
    if not found:
        raise Unavailable(f"anytopdf has no build for {platform.system()} {platform.machine()}")
    name, sha = found
    dest = fetched_path(cfg)
    if dest.is_file():
        return str(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if say:
        say(f"downloading anytopdf {VERSION}")
    with tempfile.TemporaryDirectory(dir=dest.parent) as tmp:
        part, h = pathlib.Path(tmp) / name, hashlib.sha256()
        with urllib.request.urlopen(f"{RELEASES}/v{VERSION}/{name}", timeout=60) as r, open(part, "wb") as f:
            while chunk := r.read(1 << 20):
                h.update(chunk)
                f.write(chunk)
        if h.hexdigest() != sha:
            raise RuntimeError(f"{name} didn't match its checksum")
        want = f"{name.removesuffix('.tar.gz')}/anytopdf"
        with tarfile.open(part) as t:
            member = t.getmember(want)  # the program only: its plugins stay behind, and nothing else is unpacked
            if not member.isfile():
                raise RuntimeError(f"{want} in {name} isn't a file")
            src = t.extractfile(member)
            out = pathlib.Path(tmp) / "anytopdf"
            with open(out, "wb") as f:
                shutil.copyfileobj(src, f)
        out.chmod(0o755)
        out.replace(dest)
    return str(dest)


def _args(cfg, scan=False):
    """What every conversion asks for: no OCR when Lens reads no text from pictures, a photographed page flattened."""
    args = list(QUIET)
    if (cfg.get("video") or {}).get("ocr_engine") == "none":
        args += ["--ocr", "off"]
    if scan:
        args += ["--scan-mode", "auto"]
    return args


def renderer(cfg):
    """The browser anytopdf renders a page of HTML with here (--html-render), or None where it can't: it needs Chromium
    and poppler's pdftoppm, which a small server (a Raspberry Pi) usually hasn't."""
    from . import convert, documents

    browser = convert.chromium(cfg)
    return browser if browser and documents._poppler()[0] else None


def to_pdf(cfg, src, out, scan=False, render=False):
    """Make `out`, the PDF of the file at `src`, on the conversion node if one is set, else here; `render` asks for a
    page of HTML drawn as it looks, where that can be done. Returns who made it: "anytopdf"."""
    where = available(cfg)
    if where == "node":
        _remote(cfg, pathlib.Path(src), pathlib.Path(out))
    elif where == "here":
        _local(cfg, pathlib.Path(src), pathlib.Path(out), scan, renderer(cfg) if render else None)
    else:
        raise Unavailable("anytopdf isn't installed here, and no conversion node is set (documents.anytopdf_url)")
    return "anytopdf"


def _local(cfg, src, out, scan, browser=None):
    from . import convert

    exe = binary(cfg)
    seconds = _opts(cfg).get("convert_seconds") or 300
    with tempfile.TemporaryDirectory(prefix="lens-anytopdf-") as tmp:
        doc = pathlib.Path(tmp) / f"document{src.suffix.lower()}"
        shutil.copyfile(src, doc)
        made = pathlib.Path(tmp) / "out.pdf"
        env = netguard.nowhere_env({**os.environ, "HOME": tmp, "ANYTOPDF_DATA_DIR": f"{tmp}/data", "TMPDIR": tmp})
        env = {k: v for k, v in env.items() if not k.startswith("ANYTOPDF_") or k == "ANYTOPDF_DATA_DIR"}
        argv = [exe, "--no-plugins", "--no-config", "convert", str(doc), "-o", str(made), *_args(cfg, scan)]
        if browser:  # drawn offline: the page Lens made has nothing to fetch, and the browser couldn't anyway
            argv.append("--html-render")
            env["ANYTOPDF_CHROME"] = browser
        r = convert._run(argv, seconds, env=env, cwd=tmp)
        if r.returncode != 0 or not made.is_file() or made.stat().st_size == 0:
            said = convert._last_said(r.stderr or r.stdout)
            raise ValueError(f"anytopdf couldn't read it ({said or f'exit {r.returncode}'})")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made), out)


def _call(cfg, method, path, body=None, query=None):
    url = node(cfg) + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
    token = _opts(cfg).get("anytopdf_token") or ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Length"] = str(len(body))
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    return urllib.request.urlopen(req, timeout=60)


def _said(e):
    try:
        return json.loads(e.read() or b"{}").get("error") or e.reason
    except (ValueError, AttributeError):
        return getattr(e, "reason", str(e))


def _remote(cfg, src, out):
    """The file sent to the conversion node as a job, waited for, and its PDF fetched."""
    seconds = _opts(cfg).get("convert_seconds") or 300
    try:
        with _call(cfg, "POST", "/v1/jobs", src.read_bytes(), {"filename": f"document{src.suffix.lower()}"}) as r:
            job = json.loads(r.read())
        jid, end = job["job_id"], time.monotonic() + seconds
        while job.get("state") not in ("succeeded", "failed"):
            if time.monotonic() > end:
                raise ValueError(f"the conversion node took longer than {seconds} s (documents.convert_seconds)")
            time.sleep(POLL_SECONDS)
            with _call(cfg, "GET", f"/v1/jobs/{urllib.parse.quote(jid)}") as r:
                job = json.loads(r.read())
        if job["state"] == "failed":
            raise ValueError(f"the conversion node couldn't read it (exit {job.get('exit_code')})")
        out.parent.mkdir(parents=True, exist_ok=True)
        part = out.with_suffix(".part")
        with _call(cfg, "GET", f"/v1/jobs/{urllib.parse.quote(jid)}/output") as r, open(part, "wb") as f:
            shutil.copyfileobj(r, f)
        part.replace(out)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("the conversion node refused Lens's token (documents.anytopdf_token)") from None
        raise ValueError(f"the conversion node said {e.code}: {_said(e)}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"the conversion node can't be reached ({getattr(e, 'reason', e)})") from None
