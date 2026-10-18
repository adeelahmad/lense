"""`lens ext`: extensions' manifests as files (docs/assistant.md, Manifests as files).

`list` the extensions, `pull` their manifests into a folder, `push` a folder's changes back as new versions (and the new
ones as extensions), and `check` manifests without saving them. It talks to a Lens server over its API with an API
token (`--url` or LENS_URL, `--token` or LENS_TOKEN), or, with no address, to this install's database as one of its
accounts (`--as`, by default the first admin), as the other `lens` commands do.

A pulled file carries `version: N`, the version it was pulled at (the check ignores it). That is how a push knows the
extension changed in Lens since, and a pull knows the file changed here since: neither overwrites the other's changes
unless told to (`--force`).
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request

from .domain import auth, extensions, store

SUFFIXES = (".md", ".yaml", ".yml", ".json")
HEAD_KEYS = ("name", "kind", "title", "description")
SHARING = ("visibility", "namespaces", "version")  # in a file, not in the manifest Lens gives back
FRONTMATTER = re.compile(r"^(---[ \t]*\n)(.*?\n)(---[ \t]*(?:\n.*)?)$", re.S)
VERSION_LINE = re.compile(r"^version:.*(?:\n|$)", re.M)


class Api:
    """A Lens server, through /api/v1/extensions with an API token."""

    def __init__(self, url, token):
        self.base = url.rstrip("/").removesuffix("/api/v1") + "/api/v1/extensions"
        self.token = token

    def _call(self, method, path="", body=None, **query):
        url = self.base + path + ("?" + urllib.parse.urlencode(query) if query else "")
        req = urllib.request.Request(
            url,
            method=method,
            data=None if body is None else json.dumps(body).encode(),
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json", "Accept": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r:
                return json.loads(r.read() or b"null")
        except urllib.error.HTTPError as e:
            try:
                detail = json.loads(e.read() or b"{}").get("detail")
            except ValueError:
                detail = None
            if isinstance(detail, list):  # the request's shape (422)
                detail = "; ".join(str(d.get("msg", d)) if isinstance(d, dict) else str(d) for d in detail)
            if e.code == 401:
                raise SystemExit(f"{self.base}: the token was refused (make one in Settings › API tokens)") from None
            if e.code in (400, 403, 404, 422):
                raise ValueError(detail or e.reason) from None
            raise SystemExit(f"{url}: {e.code} {detail or e.reason}") from None
        except urllib.error.URLError as e:
            raise SystemExit(f"can't reach {self.base}: {e.reason}") from None

    def list(self, kind=None):
        return self._call("GET", **({"kind": kind} if kind else {}))

    def get(self, eid, version=None):
        return self._call("GET", f"/{eid}", **({"version": version} if version else {}))

    def check(self, text):
        return self._call("POST", "/check", {"text": text})["manifest"]

    def create(self, text):
        return self._call("POST", "", {"text": text})["id"]

    def save(self, eid, text, notes):
        return self._call("POST", f"/{eid}/versions", {"text": text, "notes": notes})["version"]

    def share(self, eid, visibility, namespaces):
        self._call("PATCH", f"/{eid}", {"visibility": visibility, "namespaces": namespaces})


class Local:
    """This install's database, as one of its accounts: the one given, or the first admin. Saving is audited as the
    API audits it."""

    def __init__(self, db, email=None, need_account=True):
        self.db = db
        if email:
            acct = auth.find_account(db, email)
            if not acct:
                raise SystemExit(f"no account for {email}")
        else:
            rows = db.rows("SELECT record::id(id) AS id, email, admin, disabled, removed_at FROM account WHERE admin = true ORDER BY id")
            acct = next((r for r in rows if not r.get("disabled") and not r.get("removed_at")), None)
        if acct:
            self.me = extensions.who(acct["id"], acct["email"], acct.get("admin"), auth.roles(db, acct))
        elif need_account:
            raise SystemExit("no admin account yet: sign up in the web app first, or give --url and --token for a Lens server")
        else:  # checking saves nothing, so a new install checks as an admin would
            self.me = extensions.who(None, "lens ext", True, {})

    def _audit(self, action, eid, detail):
        auth.audit(self.db, self.me, action, f"extension:{eid}", {**detail, "via": "cli"})

    def list(self, kind=None):
        return extensions.visible(self.db, self.me, kind)

    def get(self, eid, version=None):
        try:
            return extensions.visible_one(self.db, self.me, eid, version)
        except KeyError:
            raise ValueError(f"no extension {eid}" + (f" v{version}" if version else "")) from None

    def check(self, text):
        return extensions.check_manifest(extensions.parse_manifest(text), self.me, self.db)

    def create(self, text):
        eid = extensions.create(self.db, self.me, extensions.parse_manifest(text))
        self._audit("extension.create", eid, {"origin": "code"})
        return eid

    def save(self, eid, text, notes):
        try:
            n = extensions.save_version(self.db, self.me, eid, extensions.parse_manifest(text), notes)
        except (PermissionError, KeyError) as e:
            raise ValueError(str(e)) from None
        self._audit("extension.save", eid, {"version": n, "origin": "code"})
        return n

    def share(self, eid, visibility, namespaces):
        try:
            extensions.update(self.db, self.me, eid, visibility=visibility, namespaces=namespaces)
        except (PermissionError, KeyError) as e:
            raise ValueError(str(e)) from None
        self._audit("extension.update", eid, {"visibility": visibility, "namespaces": namespaces})


# ---------- manifests as files ----------
def _read(text):
    """A manifest's settings, flat (the spec's keys next to name and kind), read without checking."""
    m = extensions.parse_manifest(text)
    if isinstance(m.get("spec"), dict):
        m = {**{k: m[k] for k in m if k != "spec"}, **m["spec"]}
    return m


def _core(text):
    m = _read(text)
    for k in SHARING:
        m.pop(k, None)
    return m


def _version(m):
    try:
        return int(m["version"]) if m.get("version") is not None else None
    except (TypeError, ValueError):
        return None


def stamped(text, version, add=True):
    """The manifest with `version: N` in its head (its frontmatter, or the whole YAML): replacing the line that's
    there, or, with `add`, adding one. None when it has none and `add` is off, or is JSON."""
    fm = FRONTMATTER.match(text)
    before, head, after = (fm.group(1), fm.group(2), fm.group(3)) if fm else ("", text, "")
    line = f"version: {version}\n"
    head, n = VERSION_LINE.subn(line, head, count=1)
    if not n:
        if not add or head.lstrip().startswith(("{", "[")):
            return None
        head += ("" if not head or head.endswith("\n") else "\n") + line
    return before + head + after


def manifest_files(paths):
    """The files named, and the manifests in the folders named (not their subfolders): .md files with frontmatter
    (a README is left alone), .yaml, .yml and .json."""
    out = []
    for p in map(pathlib.Path, paths):
        if p.is_dir():
            for f in sorted(p.iterdir()):
                if not f.is_file() or f.name.startswith(".") or f.suffix not in SUFFIXES:
                    continue
                if f.suffix == ".md" and not f.read_text(encoding="utf-8").lstrip("﻿").startswith("---"):
                    continue
                out.append(f)
        elif p.is_file():
            out.append(p)
        else:
            raise SystemExit(f"no such file or folder: {p}")
    return out


def _held(files):
    """{name: file} for the manifests that read, and [(file, problem)] for the others (unreadable, or a name two files
    have)."""
    have: dict[str, pathlib.Path] = {}
    bad: list[tuple[pathlib.Path, str]] = []
    twice: dict[str, pathlib.Path] = {}
    for f in files:
        try:
            name = str(_read(f.read_text(encoding="utf-8")).get("name") or "")
        except ValueError as e:
            bad.append((f, str(e)))
            continue
        if not name:
            bad.append((f, "a manifest starts with its name, kind and description"))
        elif name in have or name in twice:
            bad.append((f, f"{name} is in {(have.get(name) or twice[name]).name} too"))
            if name in have:
                twice[name] = have.pop(name)  # neither is pushed: which one is meant is the person's call
                bad.append((twice[name], f"{name} is in {f.name} too"))
        else:
            have[name] = f
    return have, bad


# ---------- the commands ----------
def cmd_list(be, a):
    items = be.list(a.kind)
    if a.json:
        keep = ("id", "name", "kind", "title", "description", "current", "enabled", "visibility", "namespaces", "owner_email", "editable")
        print(json.dumps([{k: x.get(k) for k in keep} for x in items], ensure_ascii=False, indent=1))
        return 0
    for x in items:
        shared = x.get("visibility") + (f" ({', '.join(x['namespaces'])})" if x.get("namespaces") else "")
        state = "on" if x.get("enabled") else "off"
        mine = "" if x.get("editable") else "  read only"
        print(f"{x['id']:>5}  {x['name']:<28} {x['kind']:<7} v{x['current']:<4} {state:<4} {shared:<20} {x.get('owner_email') or ''}{mine}")
    if not items:
        print("no extensions yet: write a manifest and run lens ext push")
    return 0


def _pull_one(be, x, folder, have, force):
    cur = be.get(x["id"])
    text = stamped(cur["manifest"], cur["current"])
    suffix = ".md" if FRONTMATTER.match(text) else ".yaml"
    old = have.get(x["name"])
    if old is not None:
        mine = old.read_text(encoding="utf-8")
        if mine == text:
            return f"unchanged {old.name} (v{cur['current']})"
        if not force and _core(mine) != _core(cur["manifest"]):
            base = _version(_read(mine))
            try:
                clean = base is not None and _core(mine) == _core(be.get(x["id"], base)["manifest"])
            except ValueError:
                clean = False
            if not clean:
                raise ValueError(f"{old.name} has changes that aren't in Lens: push them, or pull --force to take Lens's")
    keep = old is not None and (old.suffix == suffix or (suffix == ".yaml" and old.suffix == ".yml"))
    path = old if keep else folder / f"{x['name']}{suffix}"
    if path != old and path.exists() and not force:
        raise ValueError(f"{path.name} is there already and holds something else: move it, or pull --force")
    path.write_text(text, encoding="utf-8")
    if old is not None and path != old:
        old.unlink()
        return f"pulled {x['name']} into {path.name}, instead of {old.name} (v{cur['current']})"
    return f"pulled {path.name} ({x['kind']}, v{cur['current']})"


def cmd_pull(be, a):
    folder = pathlib.Path(a.dir)
    folder.mkdir(parents=True, exist_ok=True)
    items = be.list(a.kind)
    if a.names:
        by = {x["name"]: x for x in items} | {str(x["id"]): x for x in items}
        missing = [n for n in a.names if n not in by]
        if missing:
            raise SystemExit(f"no extension called {', '.join(missing)} that you can see (lens ext list)")
        items = [by[n] for n in dict.fromkeys(a.names)]
    elif not a.all:
        items = [x for x in items if x.get("editable")]  # yours (admins: all); --all adds the ones shared with you
    have, _ = _held(manifest_files([folder]))
    failed = 0
    for x in items:
        try:
            print(" ", _pull_one(be, x, folder, have, a.force))
        except ValueError as e:
            failed += 1
            print(f"  {x['name']}: {e}", file=sys.stderr)
    if not items:
        print("nothing to pull" + ("" if a.all else ": none of the extensions are yours (--all pulls the ones shared with you)"))
    return 1 if failed else 0


def _same(be, mine, theirs):
    """Whether a file says what Lens has: read as they are, or, when they're written differently, as the check
    tidies them (defaults filled in), so a hand-written file doesn't make a new version on every push."""
    if _core(mine) == _core(theirs):
        return True
    try:
        a, b = be.check(mine), be.check(theirs)
    except ValueError:
        return False
    for m in (a, b):
        for k in SHARING:
            m.pop(k, None)
    return a == b


def _push_one(be, path, x, notes, force):
    text = path.read_text(encoding="utf-8")
    m = _read(text)
    said = []
    if x is None:
        eid, n = be.create(text), 1
        said.append(f"created {m['name']} ({m.get('kind')}, id {eid}) from {path.name}")
        cur = None
    else:
        if not x.get("editable"):
            raise ValueError(f"{x['name']} is {x.get('owner_email') or 'someone else'}'s: only its owner or an admin changes it")
        if m.get("kind") != x["kind"]:
            raise ValueError(f"{x['name']} is a {x['kind']} in Lens; a manifest can't change its kind (give it another name)")
        eid, cur = x["id"], be.get(x["id"])
        n = cur["current"]
        if _same(be, text, cur["manifest"]):
            said.append(f"unchanged {path.name} (v{n})")
        else:
            base = _version(m)
            if base is not None and base != n and not force:
                raise ValueError(f"{x['name']} changed in Lens since v{base} (now v{n}): pull it first, or push --force")
            n = be.save(eid, text, notes or f"lens ext push {path.name}")
            said.append(f"saved {x['name']} v{n} from {path.name}")
    if cur is not None and ("visibility" in m or "namespaces" in m):
        vis = m.get("visibility") or cur["visibility"]
        ns = sorted(m["namespaces"] if m.get("namespaces") is not None else cur.get("namespaces") or [])
        was = sorted(cur.get("namespaces") or []) if cur["visibility"] == "namespace" else []
        if (vis, ns if vis == "namespace" else []) != (cur["visibility"], was):
            be.share(eid, vis, ns)
            said.append(f"shared {x['name']}: {vis}" + (f" ({', '.join(ns)})" if vis == "namespace" else ""))
    again = stamped(text, n, add=False)  # a file that says which version it is says the new one
    if again is not None and again != text:
        path.write_text(again, encoding="utf-8")
    return "; ".join(said)


def cmd_push(be, a):
    files = manifest_files(a.paths or ["."])
    have, bad = _held(files)
    by = {x["name"]: x for x in be.list()}
    failed = len(bad)
    for f, why in bad:
        print(f"  {f.name}: {why}", file=sys.stderr)
    for name, f in have.items():
        try:
            print(" ", _push_one(be, f, by.get(name), a.notes, a.force))
        except ValueError as e:
            failed += 1
            print(f"  {f.name}: {e}", file=sys.stderr)
    if not files:
        print("no manifests here: .md files with frontmatter, .yaml, .yml or .json")
    return 1 if failed else 0


def cmd_check(be, a):
    files = manifest_files(a.paths or ["."])
    _, bad = _held(files)
    dup = {f for f, _ in bad}
    failed = 0
    for f in files:
        try:
            if f in dup:
                raise ValueError(next(why for g, why in bad if g == f))
            m = be.check(f.read_text(encoding="utf-8"))
            print(f"  ok {f.name}: {m['name']} ({m['kind']})")
        except ValueError as e:
            failed += 1
            print(f"  {f.name}: {e}", file=sys.stderr)
    if not files:
        print("no manifests here: .md files with frontmatter, .yaml, .yml or .json")
    return 1 if failed else 0


COMMANDS = {"list": cmd_list, "pull": cmd_pull, "push": cmd_push, "check": cmd_check}


def parser():
    ap = argparse.ArgumentParser(
        prog="lens ext",
        description="Extensions' manifests as files: list them, pull them into a folder, push changes back, check them.",
    )
    ap.add_argument(
        "--url", default=os.environ.get("LENS_URL"), help="a Lens server's address (default: $LENS_URL; none: this install's database)"
    )
    ap.add_argument(
        "--token", default=os.environ.get("LENS_TOKEN"), help="an API token for it, write scoped to push (default: $LENS_TOKEN)"
    )
    ap.add_argument("--as", dest="email", help="without --url: the account to act as (default: the first admin)")
    sub = ap.add_subparsers(dest="action", required=True)
    p = sub.add_parser("list", help="the extensions you can see")
    p.add_argument("--kind", choices=extensions.KINDS)
    p.add_argument("--json", action="store_true", help="as JSON, for scripts and agents")
    p = sub.add_parser("pull", help="write manifests into a folder, each at the version Lens has")
    p.add_argument("names", nargs="*", help="extensions by name or id (default: yours; admins: all)")
    p.add_argument("--dir", default=".", help="the folder (default: this one)")
    p.add_argument("--kind", choices=extensions.KINDS)
    p.add_argument("--all", action="store_true", help="the ones shared with you too")
    p.add_argument("--force", action="store_true", help="overwrite files that have changes Lens doesn't")
    p = sub.add_parser("push", help="save changed manifests as new versions, and new ones as extensions")
    p.add_argument("paths", nargs="*", help="manifest files or folders (default: this folder)")
    p.add_argument("--notes", help="what changed, kept with each new version")
    p.add_argument("--force", action="store_true", help="save over changes made in Lens since the file was pulled")
    p = sub.add_parser("check", help="read and check manifests without saving them")
    p.add_argument("paths", nargs="*", help="manifest files or folders (default: this folder)")
    return ap


def main(argv, config=None):
    a = parser().parse_args(argv)
    if a.url:
        if not a.token:
            raise SystemExit("give an API token with --token or LENS_TOKEN (make one in Settings › API tokens)")
        return COMMANDS[a.action](Api(a.url, a.token), a)
    db = store.connect(store.load_config(config))
    try:
        return COMMANDS[a.action](Local(db, a.email, need_account=a.action != "check"), a)
    finally:
        db.close()
