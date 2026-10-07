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

A conversion node gets the document and its token at the address the admin set and nowhere else: only http(s), no
user name or password in the address, and no redirect is followed, so the token never goes on to another server.
What it answers is bounded (MAX_JSON for a job, MAX_PDF for its PDF, which must start like one) before Lens reads it.

analyze() asks its face and object plugins, kept beside the program, what is in a picture: sandboxed, with no network,
reading only the picture and the models Lens names.
"""

from __future__ import annotations

import json
import os
import pathlib
import platform
import re
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
MAX_JSON = 1 << 20  # the most of a conversion node's job answer that is read
MAX_PDF = 2 << 30  # the largest PDF taken from a conversion node
JOB_ID = re.compile(r"[A-Za-z0-9_.:-]{1,128}")
# the plugins Lens keeps beside the program, for analyze(): the rest of the release's plugins stay behind
PLUGINS = ("anytopdf-plugin-faces", "anytopdf-plugin-face-id", "anytopdf-plugin-objects", "anytopdf-plugin-clip")


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
    """Download this machine's anytopdf release, check it against its checksum, and keep only the program and its
    face, object and CLIP plugins (PLUGINS)."""
    import hashlib

    found = archive()
    if not found:
        raise Unavailable(f"anytopdf has no build for {platform.system()} {platform.machine()}")
    name, sha = found
    dest = fetched_path(cfg)
    asked = dest.parent / "plugins" / ".asked"  # the plugins it was fetched for: fetched again when Lens keeps more
    if dest.is_file() and asked.is_file() and set(PLUGINS) <= set(asked.read_text().split()):
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
        top = name.removesuffix(".tar.gz")
        wanted = {f"{top}/anytopdf": "anytopdf", **{f"{top}/plugins/{p}": f"plugins/{p}" for p in PLUGINS}}
        got = pathlib.Path(tmp) / "got"
        with tarfile.open(part) as t:
            for want, to in wanted.items():  # these only: nothing else in the archive is unpacked
                try:
                    member = t.getmember(want)
                except KeyError:
                    if to == "anytopdf":
                        raise
                    continue  # a plugin this release doesn't ship: analyze() goes without it
                if not member.isfile():
                    raise RuntimeError(f"{want} in {name} isn't a file")
                out = got / to
                out.parent.mkdir(parents=True, exist_ok=True)
                with t.extractfile(member) as src, open(out, "wb") as f:
                    shutil.copyfileobj(src, f)
                out.chmod(0o755)
        if (dest.parent / "plugins").exists():
            shutil.rmtree(dest.parent / "plugins")
        (got / "plugins").mkdir(exist_ok=True)
        (got / "plugins" / ".asked").write_text("\n".join(PLUGINS) + "\n")
        (got / "plugins").replace(dest.parent / "plugins")
        (got / "anytopdf").replace(dest)
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
        argv = [exe, "--no-plugins", "--no-config", "convert", str(doc), "-o", str(made), *_args(cfg, scan)]
        env = _env(tmp)
        if browser:  # drawn offline: the page Lens made has nothing to fetch, and the browser couldn't anyway
            argv.append("--html-render")
            env["ANYTOPDF_CHROME"] = browser
        r = convert._run(argv, seconds, env=env, cwd=tmp)
        if r.returncode != 0 or not made.is_file() or made.stat().st_size == 0:
            said = convert._last_said(r.stderr or r.stdout)
            raise ValueError(f"anytopdf couldn't read it ({said or f'exit {r.returncode}'})")
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(made), out)


def _env(tmp):
    """No network, and no anytopdf settings but its own data folder, in `tmp`."""
    env = netguard.nowhere_env({**os.environ, "HOME": tmp, "ANYTOPDF_DATA_DIR": f"{tmp}/data", "TMPDIR": tmp})
    return {k: v for k, v in env.items() if not k.startswith("ANYTOPDF_") or k == "ANYTOPDF_DATA_DIR"}


def graph(cfg, src, args):
    """What anytopdf finds in the file at `src` (run here, offline, with no plugins): its document graph, {sources,
    units: [{kind, visible_text, annotations: [{kind, text, confidence, attributes}]}]}, from `--dump-graph`. The PDF
    it makes on the way is thrown away. Unavailable when it isn't here; ValueError when it can't read the file."""
    from . import convert

    exe = binary(cfg)
    if not exe:
        raise Unavailable("anytopdf isn't installed here")
    seconds = _opts(cfg).get("convert_seconds") or 300
    with tempfile.TemporaryDirectory(prefix="lens-anytopdf-") as tmp:
        doc = pathlib.Path(tmp) / f"document{pathlib.Path(src).suffix.lower()}"
        shutil.copyfile(src, doc)
        made, dump = pathlib.Path(tmp) / "out.pdf", pathlib.Path(tmp) / "graph.json"
        argv = [exe, "--no-plugins", "--no-config", "convert", str(doc), "-o", str(made), "--dump-graph", str(dump), *args]
        r = convert._run(argv, seconds, env=_env(tmp), cwd=tmp)
        if r.returncode != 0 or not dump.is_file():
            said = convert._last_said(r.stderr or r.stdout)
            raise ValueError(f"anytopdf couldn't read it ({said or f'exit {r.returncode}'})")
        try:
            return json.loads(dump.read_text())
        except ValueError:
            raise ValueError("anytopdf's document graph wasn't JSON") from None


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A conversion node that answers with a redirect is refused: the token goes to the address set, never on."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "it redirected elsewhere, and Lens doesn't follow", headers, fp)


_OPENER = urllib.request.build_opener(_NoRedirect)


def _url(cfg):
    """The node's address, refused unless it is http(s) with a host and no user name or password."""
    url = node(cfg)
    u = urllib.parse.urlsplit(url or "")
    if u.scheme not in ("http", "https") or not u.hostname or u.username is not None or u.password is not None:
        raise ValueError("documents.anytopdf_url is a conversion node's http(s) address, such as https://convert.home:8640")
    return url


def _call(cfg, method, path, body=None, query=None):
    url = _url(cfg) + path + (f"?{urllib.parse.urlencode(query)}" if query else "")
    token = _opts(cfg).get("anytopdf_token") or ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    if body is not None:
        headers["Content-Length"] = str(len(body))
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    return _OPENER.open(req, timeout=60)


def _job(r):
    """A job the node answered with, read no further than MAX_JSON, with an id that is only an id."""
    raw = r.read(MAX_JSON + 1)
    if len(raw) > MAX_JSON:
        raise ValueError("the conversion node's answer was too long")
    try:
        job = json.loads(raw)
    except ValueError:
        raise ValueError("the conversion node didn't answer like anytopdf queue serve") from None
    if not isinstance(job, dict) or not JOB_ID.fullmatch(str(job.get("job_id") or "")):
        raise ValueError("the conversion node didn't answer like anytopdf queue serve")
    return job


def _save_pdf(r, part):
    """The node's PDF, written to `part`: refused when it doesn't start like a PDF or runs past MAX_PDF."""
    size, head = 0, b""
    with open(part, "wb") as f:
        while chunk := r.read(1 << 20):
            if not size:
                head = chunk[:5]
            size += len(chunk)
            if size > MAX_PDF:
                raise ValueError(f"the conversion node's PDF ran past {MAX_PDF >> 30} GB")
            f.write(chunk)
    if head != b"%PDF-":
        raise ValueError("the conversion node sent something that isn't a PDF")


def _said(e):
    if 300 <= e.code < 400:
        return e.reason
    try:
        return str(json.loads(e.read(MAX_JSON) or b"{}").get("error") or e.reason)[:300]
    except (ValueError, AttributeError):
        return getattr(e, "reason", str(e))


def _remote(cfg, src, out):
    """The file sent to the conversion node as a job, waited for, and its PDF fetched."""
    seconds = _opts(cfg).get("convert_seconds") or 300
    part = out.with_suffix(".part")
    try:
        with _call(cfg, "POST", "/v1/jobs", src.read_bytes(), {"filename": f"document{src.suffix.lower()}"}) as r:
            job = _job(r)
        jid, end = job["job_id"], time.monotonic() + seconds
        while job.get("state") not in ("succeeded", "failed"):
            if time.monotonic() > end:
                raise ValueError(f"the conversion node took longer than {seconds} s (documents.convert_seconds)")
            time.sleep(POLL_SECONDS)
            with _call(cfg, "GET", f"/v1/jobs/{urllib.parse.quote(jid)}") as r:
                job = _job(r)
        if job["state"] == "failed":
            raise ValueError(f"the conversion node couldn't read it (exit {job.get('exit_code')})")
        out.parent.mkdir(parents=True, exist_ok=True)
        with _call(cfg, "GET", f"/v1/jobs/{urllib.parse.quote(jid)}/output") as r:
            _save_pdf(r, part)
        part.replace(out)
    except urllib.error.HTTPError as e:
        if e.code == 401:
            raise ValueError("the conversion node refused Lens's token (documents.anytopdf_token)") from None
        raise ValueError(f"the conversion node said {e.code}: {_said(e)}") from None
    except (urllib.error.URLError, OSError) as e:
        raise ValueError(f"the conversion node can't be reached ({getattr(e, 'reason', e)})") from None
    finally:
        part.unlink(missing_ok=True)


def check(cfg):
    """Whether the conversion node answers at its address and takes Lens's token: None when it does, else what's
    wrong. It asks for a job that isn't there, which a node that took the token answers with 404."""
    if not node(cfg):
        return "set the conversion node's address first (documents.anytopdf_url)"
    try:
        with _call(cfg, "GET", "/v1/jobs/lens-check"):
            return None
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        if e.code == 401:
            return "it refused Lens's token (documents.anytopdf_token)"
        return f"it said {e.code}: {_said(e)}"
    except ValueError as e:
        return str(e)
    except (urllib.error.URLError, OSError) as e:
        return f"it can't be reached ({getattr(e, 'reason', e)})"


def analyze(cfg, picture, models=None, seconds=None):
    """What anytopdf's plugins find in a picture, run here: [{"kind": "face" | "object", "label", "box": [x, y, w, h]
    fractions, "score", "attributes"}], one per thing found where it is (their summaries left out).

    `models` are the plugins' settings, such as {"ANYTOPDF_OBJECTS_MODEL": "/models/yolox_s.onnx"}: a plugin that
    needs a model runs only when it's given one, and may read nothing else (the plugins run sandboxed, with no network
    and no config file). Faces are found by the bundled YuNet whatever is given."""
    from . import convert

    exe = binary(cfg)
    if not exe:
        raise Unavailable("anytopdf isn't installed here (documents.anytopdf)")
    models = {k: str(v) for k, v in (models or {}).items() if k.startswith("ANYTOPDF_") and v}
    seconds = seconds or _opts(cfg).get("convert_seconds") or 300
    with tempfile.TemporaryDirectory(prefix="lens-anytopdf-") as tmp:
        pic = pathlib.Path(tmp) / f"picture{pathlib.Path(picture).suffix.lower() or '.jpg'}"
        shutil.copyfile(picture, pic)
        env = netguard.nowhere_env({**os.environ, "HOME": tmp, "TMPDIR": tmp})
        env = {k: v for k, v in env.items() if not k.startswith("ANYTOPDF_")}
        env.update(models, ANYTOPDF_DATA_DIR=f"{tmp}/data")
        reads = [a for v in models.values() if os.path.exists(v) for a in ("--plugin-sandbox-allow-read", v)]
        graph = pathlib.Path(tmp) / "graph.json"
        argv = [exe, "--no-config", "--plugin-sandbox", "strict", *reads, "--plugin-timeout", str(int(seconds))]
        argv += ["convert", str(pic), "-o", f"{tmp}/out.pdf", "--dump-graph", str(graph), "--ocr", "off", *QUIET]
        r = convert._run(argv, seconds, env=env, cwd=tmp)
        if r.returncode != 0 or not graph.is_file():
            said = convert._last_said(r.stderr or r.stdout)
            raise ValueError(f"anytopdf couldn't look at it ({said or f'exit {r.returncode}'})")
        found = json.loads(graph.read_text())
    out = []
    for unit in found.get("units") or []:
        for a in unit.get("annotations") or []:
            box = a.get("region")
            if a.get("kind") not in ("face", "object") or not box:
                continue
            attrs = a.get("attributes") or {}
            out.append(
                {
                    "kind": a["kind"],
                    "label": attrs.get("label") or a["kind"],
                    "box": [float(box["x"]), float(box["y"]), float(box["width"]), float(box["height"])],
                    "score": float(a.get("confidence") or 0.0),
                    "attributes": attrs,
                }
            )
    return out
