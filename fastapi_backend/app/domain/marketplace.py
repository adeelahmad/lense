"""Marketplaces: plugins and skills people made for other assistants, installed as Lens extensions (extensions.py).

Lens reads Anthropic's open formats as they are, so a marketplace made for Claude works here unchanged:

- A marketplace is a repository with `.claude-plugin/marketplace.json`: its name and a list of plugins, each with a
  source (a folder in the same repository, a GitHub repository, a git link, or a folder of one: `github`, `url`,
  `git-subdir`). Anthropic's own are suggested (ANTHROPIC); any other is added by its address, which is how a team
  runs a custom one.
- A plugin is a folder: `.claude-plugin/plugin.json` (optional), `skills/<name>/SKILL.md`, `commands/*.md`,
  `agents/*.md`, `hooks/hooks.json` and `.mcp.json`. A marketplace entry with `strict: false` describes the plugin
  itself, and its `skills`, `commands` and `agents` name folders and files in the source.
- A skill is a folder with SKILL.md (frontmatter `name` and `description`, then the instructions) and any other files
  the instructions point at.

What becomes what: a skill is a skill, with its text files (references, examples, scripts to read; nothing runs); a
command is a skill the assistant follows when someone asks for it; an agent is a prompt tool with the agent's
instructions as its system prompt. Claude Code's hooks run shell commands on the computer, so they're left out; MCP
servers on the network come along when an admin installs it (servers that run as a program don't); `preview` lists
what's left out and why. A plugin becomes one Lens plugin, owned by the person
who installed it and private until they share it, and installing it again brings the new version.

Only admins add marketplaces: that's the approval. People who can write extensions install from the ones added, and
import a plugin or skill from a link or a .zip themselves, as they could write it by hand. Repositories are read from
GitHub or GitLab, or from a .zip or .tar.gz link, through netguard (public addresses only); nothing from them runs.
"""

from __future__ import annotations

import base64
import io
import json
import posixpath
import re
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile

import yaml

from . import activity, extensions, store

R = store.R
ANTHROPIC = (
    {"name": "anthropic-agent-skills", "title": "Anthropic skills", "source": "anthropics/skills"},
    {"name": "claude-plugins-official", "title": "Anthropic plugins", "source": "anthropics/claude-plugins-official"},
)
MANIFEST = ".claude-plugin/marketplace.json"
PLUGIN_JSON = ".claude-plugin/plugin.json"
MAX_INDEX = 5 * 2**20  # a marketplace.json
MAX_ARCHIVE = 200 * 2**20  # a repository's archive, downloaded
MAX_UPLOAD = 20 * 2**20  # a .zip sent from the web app
MAX_FILE = 1 * 2**20  # a file read out of one
MAX_READ = 30 * 2**20  # all the files read out of one
MAX_ENTRIES = 20000
TEXT = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".py",
    ".js",
    ".mjs",
    ".ts",
    ".sh",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".csv",
    ".html",
    ".css",
    ".sql",
}
CACHE_SECONDS = 600
UA = "Lens marketplace"
FIELDS = "record::id(id) AS id, name, title, description, metadata, source, added_by, created_at, fetched_at"
_cache: dict[tuple, tuple[float, dict]] = {}
_lock = threading.Lock()


# ---------- where things are ----------
def parse_source(text):
    """Where a marketplace, plugin or skill is, from what people paste: `owner/repo` (GitHub, `@ref` for a branch or
    tag), a GitHub or GitLab link (to the repository or a folder in it), a link to a marketplace.json, or a link to a
    .zip or .tar.gz. A dict {host, repo, ref, path} or {host: url|archive, url}; ValueError otherwise."""
    t = (text or "").strip()
    m = re.fullmatch(r"([A-Za-z0-9][\w.-]*)/([\w.-]+?)(?:\.git)?(?:@([\w./-]+))?", t)
    if m:
        return store.clean({"host": "github", "repo": f"{m[1]}/{m[2]}", "ref": m[3]})
    u = urllib.parse.urlsplit(t)
    if u.scheme not in ("http", "https") or not u.hostname:
        raise ValueError("give a GitHub repository (owner/repo) or a link that starts with https://")
    path = u.path.rstrip("/")
    if u.hostname in ("github.com", "www.github.com"):
        m = re.fullmatch(r"/([\w.-]+)/([\w.-]+?)(?:\.git)?(?:/(?:tree|blob)/([^/]+)(?:/(.*))?)?", path)
        if m:
            sub = m[4]
            if sub and sub.endswith((".json", ".md")):  # a file: its folder (a marketplace.json is two down)
                sub = posixpath.dirname(sub)
                sub = posixpath.dirname(sub) if sub.endswith(".claude-plugin") else sub
            return store.clean({"host": "github", "repo": f"{m[1]}/{m[2]}", "ref": m[3], "path": sub or None})
    if u.hostname == "raw.githubusercontent.com":
        m = re.fullmatch(r"/([\w.-]+)/([\w.-]+)/([^/]+)/(.*)", path)
        if m and m[4].endswith(MANIFEST):
            return store.clean({"host": "github", "repo": f"{m[1]}/{m[2]}", "ref": m[3], "path": m[4][: -len(MANIFEST)].strip("/") or None})
    if u.hostname == "gitlab.com":
        m = re.fullmatch(r"/(.+?)(?:\.git)?(?:/-/(?:tree|blob)/([^/]+)(?:/(.*))?)?", path)
        if m and "/" in m[1]:
            return store.clean({"host": "gitlab", "repo": m[1], "ref": m[2], "path": m[3] or None})
    if path.endswith((".zip", ".tar.gz", ".tgz", ".skill")):
        return {"host": "archive", "url": t}
    if path.endswith(".json"):
        return {"host": "url", "url": t}
    raise ValueError("Lens reads GitHub and GitLab repositories, links to a marketplace.json, and .zip or .tar.gz links")


def show_source(src):
    """A source as people would write it."""
    if src.get("host") in ("url", "archive"):
        return src["url"]
    base = f"https://{'github.com' if src['host'] == 'github' else 'gitlab.com'}/{src['repo']}"
    if src.get("path") or src.get("ref"):
        base += f"{'/-' if src['host'] == 'gitlab' else ''}/tree/{src.get('ref') or 'HEAD'}"
        base += f"/{src['path']}" if src.get("path") else ""
    return base


def plugin_source(entry, market):
    """Where a marketplace's plugin is: its `source`, read against the marketplace's own place."""
    s = entry.get("source")
    if isinstance(s, str):
        if not market or market.get("host") not in ("github", "gitlab"):
            raise ValueError("this plugin is a folder in its marketplace, which Lens can only read from GitHub or GitLab")
        root = (market.get("metadata") or {}).get("pluginRoot") or ""
        rel = s if s.startswith("./") or s.startswith("../") or not root else posixpath.join(root, s)
        path = posixpath.normpath(posixpath.join(market.get("path") or "", rel))
        if path.startswith(".."):
            raise ValueError("the plugin's folder is outside its marketplace")
        return store.clean(
            {"host": market["host"], "repo": market["repo"], "ref": market.get("ref"), "path": None if path == "." else path}
        )
    if not isinstance(s, dict):
        raise ValueError("the plugin has no source")
    kind = s.get("source")
    ref = s.get("sha") or s.get("ref")
    if kind == "github" and isinstance(s.get("repo"), str):
        return store.clean({**parse_source(s["repo"]), "ref": ref, "path": s.get("path")})
    if kind in ("url", "git-subdir", "git") and isinstance(s.get("url"), str):
        src = parse_source(s["url"])
        if src["host"] not in ("github", "gitlab"):
            raise ValueError("Lens reads plugins from GitHub and GitLab; this one is elsewhere")
        return store.clean({**src, "ref": ref or src.get("ref"), "path": s.get("path") or src.get("path")})
    raise ValueError(f"Lens can't read a plugin from a {kind or 'source like this'} source yet")


def _archive_url(src):
    ref = urllib.parse.quote(src.get("ref") or "HEAD", safe="")
    if src["host"] == "github":
        return f"https://codeload.github.com/{src['repo']}/tar.gz/{ref}"
    if src["host"] == "gitlab":
        return f"https://gitlab.com/{src['repo']}/-/archive/{ref}/archive.tar.gz"
    return src["url"]


def _raw_url(src, path):
    ref = urllib.parse.quote(src.get("ref") or "HEAD", safe="")
    path = urllib.parse.quote(posixpath.join(src.get("path") or "", path))
    if src["host"] == "github":
        return f"https://raw.githubusercontent.com/{src['repo']}/{ref}/{path}"
    return f"https://gitlab.com/{src['repo']}/-/raw/{ref}/{path}"


# ---------- fetching ----------
def _fetch(cfg, url, out, limit):
    """Download `url` into the open file `out` through netguard, at most `limit` bytes; ValueError saying why not."""
    from . import netguard, webcapture

    webcapture.check_url(cfg, url)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    host = urllib.parse.urlsplit(url).hostname
    with (
        activity.call("marketplace.fetch", cfg, detail={"host": host}),
        netguard.Guard(forward=True, networks=webcapture.networks(cfg), max_bytes=limit + 65536) as guard,
    ):
        try:
            with netguard.opener(guard).open(req, timeout=60) as r:
                n = 0
                while chunk := r.read(65536):
                    n += len(chunk)
                    if n > limit:
                        raise ValueError(f"{host} sent more than {limit // 2**20} MB")
                    out.write(chunk)
        except urllib.error.HTTPError as e:
            raise ValueError(
                f"{host} answered {e.code} {e.reason}" + (": is the address right, and public?" if e.code == 404 else "")
            ) from None
        except (urllib.error.URLError, OSError) as e:
            refused = f"; refused: {guard.refused[0]}" if guard.refused else ""
            raise ValueError(f"couldn't reach {host}: {getattr(e, 'reason', e)}{refused}") from None


def _get(cfg, url, limit):
    buf = io.BytesIO()
    _fetch(cfg, url, buf, limit)
    return buf.getvalue()


def _members(fileobj, name):
    """(path, size, read) for each regular file in a .tar.gz or .zip, whichever it is."""
    head = fileobj.read(4)
    fileobj.seek(0)
    if head.startswith(b"PK"):
        z = zipfile.ZipFile(fileobj)
        for i in z.infolist()[:MAX_ENTRIES]:
            if not i.is_dir() and not (i.external_attr >> 16) & 0o170000 == 0o120000:  # no symbolic links
                yield i.filename, i.file_size, lambda i=i: z.read(i)
        return
    try:
        t = tarfile.open(fileobj=fileobj, mode="r:*")
    except tarfile.TarError:
        raise ValueError(f"{name} isn't a .zip or .tar.gz") from None
    for n, i in enumerate(t):
        if n >= MAX_ENTRIES:
            break
        if i.isfile():
            yield i.name, i.size, lambda i=i: t.extractfile(i).read()


def _unpack(fileobj, name, sub=None, strip=None):
    """The files of an archive under folder `sub`, {path relative to it: bytes}, where only text is read (other files
    are None). `strip` drops the one folder a repository's archive puts everything in (None: drop it when everything
    is in one folder)."""
    entries = []
    for path, size, read in _members(fileobj, name):
        p = posixpath.normpath(path.lstrip("/"))
        if p.startswith("..") or p == ".":
            continue
        entries.append((p, size, read))
    if strip is None:
        tops = {p.split("/", 1)[0] for p, _, _ in entries}
        strip = len(tops) == 1 and all("/" in p for p, _, _ in entries)
    out, total = {}, 0
    prefix = (sub.strip("/") + "/") if sub else ""
    for p, size, read in entries:
        if strip:
            p = p.split("/", 1)[1] if "/" in p else ""
        if not p or not p.startswith(prefix):
            continue
        rel = p[len(prefix) :]
        if "__MACOSX/" in f"/{rel}" or rel.endswith(".DS_Store") or "/.git/" in f"/{rel}":
            continue
        if size > MAX_FILE or posixpath.splitext(rel)[1].lower() not in TEXT:
            out[rel] = None
            continue
        total += size
        if total > MAX_READ:
            raise ValueError(f"{name} has more than {MAX_READ // 2**20} MB of files in that folder")
        out[rel] = read()
    if not out:
        raise ValueError(f"{name} has nothing in {sub}" if sub else f"{name} is empty")
    return out


def files(cfg, src):
    """The files of a plugin or skill folder, {path: bytes}: from the repository's (or the link's) archive, kept a
    few minutes so a preview and an install download it once."""
    key = (_archive_url(src), src.get("path"))
    with _lock:
        hit = _cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
    with tempfile.TemporaryFile() as tmp:
        _fetch(cfg, key[0], tmp, MAX_ARCHIVE)
        tmp.seek(0)
        got = _unpack(tmp, show_source(src), src.get("path"), strip=True if src["host"] != "archive" else None)
    with _lock:
        for k in [k for k, v in _cache.items() if time.monotonic() - v[0] >= CACHE_SECONDS]:
            del _cache[k]
        _cache[key] = (time.monotonic(), got)
    return got


# ---------- reading Anthropic's formats ----------
def slug(name):
    """A name as Lens names tools and skills: lowercase letters, digits and _, starting with a letter."""
    s = re.sub(r"[^a-z0-9]+", "_", str(name or "").lower()).strip("_")
    if s and not s[0].isalpha():
        s = "x_" + s
    s = s[:41].rstrip("_")
    if not extensions.NAME_RX.match(s):
        raise ValueError(f"{name!r} can't be a name")
    return s


def frontmatter(text):
    """(head, body) of Markdown with YAML frontmatter; head {} when there is none."""
    text = text.lstrip("﻿")
    m = re.match(r"^---\s*\n(.*?)\n---\s*(?:\n(.*))?$", text, re.S)
    if not m:
        return {}, text.strip()
    try:
        head = yaml.safe_load(m.group(1)) or {}
    except yaml.YAMLError:
        head = {}
    return (head if isinstance(head, dict) else {}), (m.group(2) or "").strip()


def _text(raw):
    if raw is None:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _escape(text):
    """Text from elsewhere, with what reads as a Lens {{parameter}} broken up, so it stays as written."""
    return extensions.VAR_RX.sub(lambda m: "{ {" + m.group(0)[2:-2] + "} }", text)


def _skill(found, folder, report):
    """A skill folder (SKILL.md and its files) as a Lens skill item."""
    pre = f"{folder}/" if folder else ""
    raw = found.get(f"{pre}SKILL.md") or found.get(f"{pre}skill.md")
    head, body = frontmatter(_text(raw) or "")
    name = head.get("name") or posixpath.basename(folder) or "skill"
    desc = str(head.get("description") or "").strip()
    if not body or not desc:
        report.append({"kind": "skill", "name": str(name), "why": "its SKILL.md has no description or no instructions"})
        return None
    extra, skipped = {}, 0
    for path, data in sorted(found.items()):
        if not path.startswith(pre) or path[len(pre) :].lower() == "skill.md":
            continue
        rel = path[len(pre) :]
        text = _text(data)
        if text is None or len(extra) >= extensions.MAX_FILES or sum(map(len, extra.values())) + len(text) > extensions.MAX_FILES_CHARS:
            skipped += 1
            continue
        extra[rel] = text
    if skipped:
        report.append({"kind": "file", "name": str(name), "why": f"{skipped} of its files aren't text, or are past the size Lens keeps"})
    return store.clean(
        {
            "kind": "skill",
            "name": slug(name),
            "description": desc[:1024],
            "spec": store.clean({"when": desc[:1024], "instructions": body[: extensions.MAX_SKILL], "files": extra or None}),
        }
    )


def _command(path, raw, report):
    head, body = frontmatter(_text(raw) or "")
    stem = posixpath.splitext(posixpath.basename(path))[0]
    if not body:
        report.append({"kind": "command", "name": stem, "why": "it has no text"})
        return None
    desc = str(head.get("description") or f"the /{stem} command").strip()
    lead = f"This is the /{stem} command. $ARGUMENTS stands for what the person asked for.\n\n"
    return {
        "kind": "skill",
        "name": slug(stem),
        "description": desc[:1024],
        "spec": {"when": f"someone asks for /{stem}, or for this: {desc}"[:1024], "instructions": (lead + body)[: extensions.MAX_SKILL]},
    }


def _agent(path, raw, report):
    head, body = frontmatter(_text(raw) or "")
    name = head.get("name") or posixpath.splitext(posixpath.basename(path))[0]
    desc = str(head.get("description") or "").strip()
    if not body or not desc:
        report.append({"kind": "agent", "name": str(name), "why": "it has no description or no instructions"})
        return None
    return {
        "kind": "tool",
        "name": slug(name),
        "description": desc[:1024],
        "spec": {
            "params": [{"name": "task", "kind": "text", "required": True, "description": "what to ask of it, with what it needs to know"}],
            "effect": "read",
            "run": {"type": "prompt", "system": _escape(body)[: extensions.MAX_TEXT], "prompt": "{{task}}"},
        },
    }


def _paths(value):
    if value is None:
        return []
    return [value] if isinstance(value, str) else [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def _norm(p):
    p = posixpath.normpath(p.lstrip("/")) if p else "."
    return "" if p == "." else p


def _mcp(found, meta, admin, report):
    """A plugin's MCP servers (.mcp.json, or mcpServers in its manifest) as MCP server items, their tools listed now.
    Servers on the network only (HTTP); only admins add them."""
    conf = meta.get("mcpServers")
    if isinstance(conf, str):
        conf = found.get(_norm(conf))
    elif conf is None:
        conf = found.get(".mcp.json")
    if isinstance(conf, bytes):
        try:
            conf = json.loads(conf)
        except ValueError:
            conf = None
    if isinstance(conf, dict) and isinstance(conf.get("mcpServers"), dict):
        conf = conf["mcpServers"]
    if not isinstance(conf, dict) or not conf:
        return []
    if not admin:
        report.append(
            {"kind": "mcp", "name": ", ".join(conf)[:200], "why": "only admins add MCP servers: an admin installing it brings them"}
        )
        return []
    from . import mcp_client

    out = []
    for name, c in list(conf.items())[:20]:
        c = c if isinstance(c, dict) else {}
        kind = c.get("type") or ("stdio" if c.get("command") else "http")
        if kind == "stdio" or c.get("command"):
            report.append(
                {"kind": "mcp", "name": name, "why": "it runs a program on the computer; Lens reaches MCP servers on the network"}
            )
            continue
        if kind not in ("http", "streamable-http") or not isinstance(c.get("url"), str):
            report.append({"kind": "mcp", "name": name, "why": f"Lens speaks MCP over HTTP; this one uses {kind}"})
            continue
        headers = {k: v for k, v in (c.get("headers") or {}).items() if isinstance(k, str) and isinstance(v, str)}
        server = {"url": c["url"], "headers": headers or None}
        try:
            tools = mcp_client.discover(server)
        except ValueError as e:
            tools = []
            report.append({"kind": "mcp", "name": name, "why": f"its tools couldn't be listed ({e}); save it again once it can be reached"})
        out.append(
            {"kind": "mcp", "name": slug(name), "description": f"The {name} MCP server", "spec": store.clean({**server, "tools": tools})}
        )
    return out


def read_plugin(found, entry=None, admin=False):
    """A plugin folder's files ({path: bytes}) as a Lens plugin manifest, and what was left out: (manifest, skipped).
    `entry` is its marketplace entry, which describes it when there's no plugin.json (strict: false). MCP servers come
    along when an admin installs it."""
    entry = dict(entry or {})
    meta = {}
    if PLUGIN_JSON in found:
        try:
            meta = json.loads(found[PLUGIN_JSON])
        except ValueError:
            raise ValueError("its .claude-plugin/plugin.json isn't valid JSON") from None
        if not isinstance(meta, dict):
            meta = {}
    meta = {**meta, **{k: v for k, v in entry.items() if k not in ("source",)}}
    report, items = [], []
    dirs = sorted({posixpath.dirname(p) for p in found if posixpath.basename(p).lower() == "skill.md"})

    # skills: the folders listed (a marketplace lists the ones each plugin of a shared folder has), else the ones
    # under skills/, and the folder itself when it's one skill
    skill_dirs = []
    for p in _paths(meta.get("skills")):
        p = _norm(p)
        skill_dirs += [d for d in dirs if d == p or (posixpath.dirname(d) == p)]
    if meta.get("skills") is None:
        skill_dirs += [d for d in dirs if posixpath.dirname(d) == "skills"]
        if "" in dirs:
            skill_dirs.append("")
    for d in dict.fromkeys(skill_dirs):
        sub = {p: b for p, b in found.items() if (p.startswith(d + "/") if d else True)}
        if d == "":  # the folder is itself a skill: not the plugin's other parts
            sub = {p: b for p, b in sub.items() if not p.startswith((".claude-plugin/", "skills/", "commands/", "agents/", "hooks/"))}
        it = _skill(sub, d, report)
        if it:
            items.append(it)

    def markdown(field, default):
        out = []
        for p in _paths(meta.get(field)) + [default]:
            p = _norm(p)
            if p.endswith(".md") and p in found:
                out.append(p)
            else:
                out += sorted(f for f in found if posixpath.dirname(f) == p and f.endswith(".md"))
        return list(dict.fromkeys(out))

    for p in markdown("commands", "commands"):
        it = _command(p, found[p], report)
        if it:
            items.append(it)
    for p in markdown("agents", "agents"):
        it = _agent(p, found[p], report)
        if it:
            items.append(it)

    if meta.get("hooks") or "hooks/hooks.json" in found:
        report.append(
            {"kind": "hooks", "name": "hooks", "why": "Claude Code's hooks run commands on the computer; Lens hooks are made in Lens"}
        )
    items += _mcp(found, meta, admin, report)

    # names: each item's own, made different where two are the same
    seen = set()
    for it in items:
        base, n = it["name"], 2
        while it["name"] in seen:
            suffix = f"_{n}"
            it["name"], n = base[: 41 - len(suffix)] + suffix, n + 1
        seen.add(it["name"])
    if not items:
        raise ValueError(
            "nothing in it Lens can use: no skills, commands, agents or MCP servers" + (f" ({report[0]['why']})" if report else "")
        )
    if len(items) > extensions.MAX_ITEMS:
        raise ValueError(f"it has {len(items)} skills, commands and agents; a Lens plugin has at most {extensions.MAX_ITEMS}")
    name = meta.get("name") or (items[0]["name"] if len(items) == 1 else "plugin")
    desc = str(meta.get("description") or (items[0]["description"] if len(items) == 1 else "") or name).strip()
    manifest = store.clean({"name": slug(name), "kind": "plugin", "title": str(name)[:80], "description": desc[:1024], "items": items})
    return manifest, report


# ---------- marketplaces ----------
def _row(db, mid):
    d = db.one(f"SELECT {FIELDS}, plugins FROM $r", r=R("marketplace", int(mid)))
    if not d:
        raise KeyError(mid)
    return d


def _index(cfg, src):
    """A marketplace's marketplace.json, read and checked."""
    if src["host"] == "url":
        raw = _get(cfg, src["url"], MAX_INDEX)
    elif src["host"] in ("github", "gitlab"):
        raw = _get(cfg, _raw_url(src, MANIFEST), MAX_INDEX)
    else:
        raise ValueError("a marketplace is a repository, or a link to its marketplace.json")
    try:
        m = json.loads(raw)
    except ValueError:
        raise ValueError("its marketplace.json isn't valid JSON") from None
    if not isinstance(m, dict) or not isinstance(m.get("plugins"), list):
        raise ValueError("its marketplace.json has no list of plugins")
    plugins = []
    for p in m["plugins"][:2000]:
        if isinstance(p, dict) and isinstance(p.get("name"), str):
            plugins.append(json.loads(json.dumps(p)))
    return {
        "name": str(m.get("name") or src.get("repo") or "marketplace")[:100],
        "description": str((m.get("metadata") or {}).get("description") or m.get("description") or "")[:1000] or None,
        "metadata": m.get("metadata") if isinstance(m.get("metadata"), dict) else None,
        "plugins": plugins,
    }


def add(db, cfg, me, source, title=None):
    """An admin adds a marketplace by its address; its id. Its list of plugins is read now and on refresh."""
    if not me["admin"]:
        raise PermissionError("only admins add marketplaces")
    src = parse_source(source)
    if src["host"] == "archive":
        raise ValueError("a marketplace is a repository, or a link to its marketplace.json")
    shown = show_source(src)
    for d in db.rows("SELECT source FROM marketplace"):
        if show_source(d["source"]) == shown:
            raise ValueError("that marketplace is already added")
    got = _index(cfg, src)
    mid, t = db.next_id("marketplace"), store.now()
    db.q(
        "CREATE $r CONTENT $d",
        r=R("marketplace", mid),
        d=store.clean(
            {**got, "title": (title or "").strip()[:80] or None, "source": src, "added_by": me["email"], "created_at": t, "fetched_at": t}
        ),
    )
    return mid


def refresh(db, cfg, me, mid):
    if not me["admin"]:
        raise PermissionError("only admins refresh marketplaces")
    d = _row(db, mid)
    got = _index(cfg, d["source"])
    db.q("UPDATE $r MERGE $d", r=R("marketplace", d["id"]), d={**got, "fetched_at": store.now()})


def remove(db, me, mid):
    """Takes the marketplace away; what was installed from it stays."""
    if not me["admin"]:
        raise PermissionError("only admins remove marketplaces")
    _row(db, mid)
    db.q("DELETE $r", r=R("marketplace", int(mid)))


def listing(db):
    out = []
    for d in db.rows(f"SELECT {FIELDS}, array::len(plugins ?? []) AS count FROM marketplace ORDER BY id"):
        out.append(
            store.clean(
                {
                    "id": d["id"],
                    "name": d["name"],
                    "title": d.get("title"),
                    "description": d.get("description"),
                    "source": show_source(d["source"]),
                    "plugins": d.get("count") or 0,
                    "added_by": d.get("added_by"),
                    "fetched_at": d.get("fetched_at"),
                }
            )
        )
    return out


def suggested(db):
    """Anthropic's marketplaces that aren't added yet."""
    have = {show_source(d["source"]) for d in db.rows("SELECT source FROM marketplace")}
    return [
        {**a, "source": show_source(parse_source(a["source"]))} for a in ANTHROPIC if show_source(parse_source(a["source"])) not in have
    ]


def _installed(db, me):
    """{(marketplace, plugin): extension id} for this person's installs."""
    out = {}
    for d in db.rows(
        "SELECT record::id(id) AS id, source FROM extension WHERE deleted_at = NONE AND owner = $o AND source != NONE", o=me["id"]
    ):
        s = d.get("source") or {}
        if s.get("marketplace"):
            out[(s["marketplace"], s.get("plugin"))] = d["id"]
    return out


def plugins(db, me, mid):
    """A marketplace's plugins, each saying whether Lens can install it and whether this person has."""
    d = _row(db, mid)
    mine = _installed(db, me)
    out = []
    for p in d.get("plugins") or []:
        why = None
        try:
            plugin_source(p, {**d["source"], "metadata": d.get("metadata")})
        except ValueError as e:
            why = str(e)
        author = p.get("author")
        out.append(
            store.clean(
                {
                    "name": p["name"],
                    "description": p.get("description") if isinstance(p.get("description"), str) else None,
                    "version": str(p["version"]) if p.get("version") else None,
                    "category": p.get("category") if isinstance(p.get("category"), str) else None,
                    "author": author.get("name") if isinstance(author, dict) else author if isinstance(author, str) else None,
                    "homepage": p.get("homepage") if isinstance(p.get("homepage"), str) else None,
                    "installable": why is None,
                    "why_not": why,
                    "installed": mine.get((d["id"], p["name"])),
                }
            )
        )
    return out


def _entry(db, mid, name):
    d = _row(db, mid)
    for p in d.get("plugins") or []:
        if p["name"] == name:
            src = plugin_source(p, {**d["source"], "metadata": d.get("metadata")})
            return d, p, src
    raise KeyError(name)


def _read(cfg, src, entry=None, admin=False):
    found = files(cfg, src)
    # a plugin's own plugin.json describes it, unless the marketplace says it describes it (strict: false)
    if entry and entry.get("strict") is not False and PLUGIN_JSON in found:
        entry = {k: v for k, v in entry.items() if k in ("name", "description", "version")}
    return read_plugin(found, entry, admin)


def preview(db, cfg, me, mid, name):
    """What installing a marketplace's plugin would add to the assistant, and what it leaves out."""
    _, p, src = _entry(db, mid, name)
    manifest, skipped = _read(cfg, src, p, me["admin"])
    extensions.check_manifest(manifest, me, db)
    return {"manifest": manifest, "skipped": skipped, "source": show_source(src)}


def _save(db, me, manifest, source, existing=None):
    if existing:
        n = extensions.save_version(db, me, existing, manifest, f"from {source.get('url')}", origin="import")
        db.q("UPDATE $r MERGE $d", r=R("extension", existing), d={"source": source})
        return existing, n
    return extensions.create(db, me, manifest, origin="import", source=source), 1


def install(db, cfg, me, mid, name):
    """Install a marketplace's plugin as this person's own plugin (private until they share it); again, a new version.
    (extension id, version)."""
    d, p, src = _entry(db, mid, name)
    manifest, _ = _read(cfg, src, p, me["admin"])
    source = store.clean(
        {"marketplace": d["id"], "plugin": p["name"], "version": str(p["version"]) if p.get("version") else None, "url": show_source(src)}
    )
    return _save(db, me, manifest, source, _installed(db, me).get((d["id"], p["name"])))


def _same(db, me, key, value):
    """This person's extension imported from the same link or file, which importing again gives a new version."""
    for d in db.rows(
        "SELECT record::id(id) AS id, source FROM extension WHERE deleted_at = NONE AND owner = $o AND source != NONE", o=me["id"]
    ):
        s = d.get("source") or {}
        if s.get(key) == value and not s.get("marketplace"):
            return d["id"]
    return None


def import_link(db, cfg, me, link, dry=False):
    """A plugin or skill from a link (a repository, a folder in one, or a .zip): previewed, or installed as this
    person's own (again: a new version)."""
    src = parse_source(link)
    if src["host"] == "url":
        raise ValueError("that's a marketplace: an admin adds it under Marketplaces")
    manifest, skipped = read_plugin(files(cfg, src), admin=me["admin"])
    url = show_source(src)
    if dry:
        extensions.check_manifest(manifest, me, db)
        return {"manifest": manifest, "skipped": skipped, "source": url}
    eid, n = _save(db, me, manifest, {"url": url}, _same(db, me, "url", url))
    return {"id": eid, "version": n, "skipped": skipped}


def import_archive(db, me, filename, data, dry=False):
    """A plugin or skill from a .zip or .tar.gz (a .skill file is a .zip), sent as base64."""
    try:
        raw = base64.b64decode(data, validate=True)
    except ValueError:
        raise ValueError("send the file as base64") from None
    if len(raw) > MAX_UPLOAD:
        raise ValueError(f"the file is larger than {MAX_UPLOAD // 2**20} MB")
    name = (filename or "the file").strip()[:200]
    manifest, skipped = read_plugin(_unpack(io.BytesIO(raw), name), admin=me["admin"])
    if dry:
        extensions.check_manifest(manifest, me, db)
        return {"manifest": manifest, "skipped": skipped, "source": name}
    eid, n = _save(db, me, manifest, {"file": name}, _same(db, me, "file", name))
    return {"id": eid, "version": n, "skipped": skipped}
