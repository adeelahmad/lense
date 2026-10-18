"""MCP servers' settings (docs/assistant.md, MCP servers): configs pasted from other apps, sealed secrets, and a switch
for each tool. The servers themselves are extensions of kind `mcp` (extensions.py), reached by mcp_client.py.

- `read` takes the JSON other apps keep their MCP servers in (Claude's `.mcp.json` and `mcpServers`, VS Code's
  `servers`, a list of them, or one server on its own) and gives back each server as an MCP extension keeps it, with
  what it left out and why. Nothing is reached or run until an admin tests or saves one.
- Header and env values whose names carry credentials (Authorization, API keys, tokens, cookies: actions_web.secret)
  are sealed when an extension is saved (`seal`), bound to the extension, the server's name in it, the value's name and
  where it goes (the address's scheme, host and port, or the command it runs with its arguments), so a sealed value
  can't be moved to another extension or sent to another host or program. MCP servers belong to the whole install, not
  a namespace (only admins add them), so they're sealed with the server's key (settings.seal), as notification targets
  and sources are. They're opened only to reach the server (`open_run`) and never shown: the API and manifests give
  the name and "••••", and saving "••••" again keeps the value saved before, as long as the server still goes to the
  same place (`opened`).
- `off` (the tools switched off) and `ask` ({tool: whether it asks first}, where that differs from the server's own
  read-only hint) sit in the server's spec, so they survive its tools being listed again and travel with its manifest.
- `presets` are servers ready to add: the browser Lens can run next to it (the optional `browser` profile, Playwright
  MCP) and GitHub's own server, with a token to type in.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import urllib.parse

import yaml

from . import settings, store

HIDDEN = "••••"
SEALED = "lens-sealed:"
MAX_SERVERS = 20
FIELDS = (("headers", "header"), ("env", "env"))
PLACEHOLDER_RX = re.compile(r"\$\{(?:input:|env:)?([A-Za-z_][A-Za-z0-9_.-]*)(?::-[^}]*)?\}")
EXAMPLE = '{"mcpServers": {"github": {"type": "http", "url": "https://api.githubcopilot.com/mcp/"}}}'


def secret(name):
    """Whether a value's name says it carries a credential, as automations' web requests decide it."""
    from .actions_web import secret as named  # loaded here: the automation actions load the assistant's tools

    return named(name)


def is_sealed(value):
    return isinstance(value, str) and value.startswith(SEALED)


def where(server):
    """Where a server's values go: its address's scheme, host and port, or the program it runs as (its command and
    arguments, so a token given to one package isn't handed to another)."""
    if server.get("url"):
        u = urllib.parse.urlsplit(str(server["url"]))
        try:
            port = u.port
        except ValueError:
            port = None
        return f"{u.scheme}://{(u.hostname or '').lower()}:{port or (443 if u.scheme == 'https' else 80)}"
    run = json.dumps([server.get("command") or "", *(server.get("args") or [])])
    return "command:" + hashlib.sha256(run.encode()).hexdigest()[:32]


def _key(db):
    """What settings.seal needs to find the server's key: its data folder (or ARCHIVE_SECRET_KEY)."""
    return {"data_dir": db.data_dir}


def _context(eid, item, field, name, server):
    return f"extension:{eid}.mcp:{item}.{field}:{name.lower() if field == 'header' else name}@{where(server)}"


def servers_in(m):
    """The MCP servers in a manifest or a saved extension, each with the name its tools go by: [(name, server)]. The
    server dicts are the manifest's own, so changing one changes it."""
    if not isinstance(m, dict):
        return []
    body = m["spec"] if isinstance(m.get("spec"), dict) else m
    if m.get("kind") == "mcp":
        return [(str(m.get("name") or ""), body)]
    if m.get("kind") != "plugin" or not isinstance(body.get("items"), list):
        return []
    return [
        (str(it.get("name") or ""), it["spec"] if isinstance(it.get("spec"), dict) else it)
        for it in body["items"]
        if isinstance(it, dict) and it.get("kind") == "mcp"
    ]


def _open(db, eid, item, field, name, server, value):
    if not is_sealed(value):
        return value
    if eid is None:
        raise ValueError(f"enter the {name} value of {item} again: it was sealed for another extension")
    try:
        return settings.unseal(_key(db), value[len(SEALED) :], _context(eid, item, field, name, server))
    except Exception:  # noqa: BLE001 - sealed for another extension or address, or with another server key
        raise ValueError(f"enter the {name} value of {item} again: it was sealed for another extension or address") from None


def opened(db, manifest, eid=None, before=None):
    """A manifest about to be checked and saved, with its secrets as they're sent: "••••" becomes the value `before`
    (the extension's current version) had, when the server still goes to the same place, and sealed values are opened.
    ValueError names a value to enter again."""
    m = json.loads(json.dumps(manifest))
    old = dict(servers_in(before)) if before else {}
    for item, server in servers_in(m):
        for field, label in FIELDS:
            values = server.get(field)
            if not isinstance(values, dict):
                continue
            for k, v in list(values.items()):
                if v == HIDDEN:
                    prev = old.get(item)
                    pv = ((prev or {}).get(field) or {}).get(k)
                    if pv is None:
                        raise ValueError(f"enter the {k} value of {item}: “{HIDDEN}” keeps one saved before, and there is none")
                    if where(prev) != where(server):
                        raise ValueError(f"enter the {k} value of {item} again: the server goes somewhere else now")
                    values[k] = _open(db, eid, item, label, k, prev, pv)
                elif is_sealed(v):
                    values[k] = _open(db, eid, item, label, k, server, v)
    return m


def seal(db, eid, name, kind, spec):
    """A checked spec as it's kept: the values whose names carry credentials sealed. Sealed ones are kept as they are."""
    spec = json.loads(json.dumps(spec))
    for item, server in servers_in({"name": name, "kind": kind, "spec": spec}):
        for field, label in FIELDS:
            for k, v in (server.get(field) or {}).items():
                if v and isinstance(v, str) and secret(k) and not is_sealed(v):
                    server[field][k] = SEALED + settings.seal(_key(db), v, _context(eid, item, label, k, server))
    return spec


def masked(name, kind, spec):
    """A spec as it's shown: values with credentials, sealed or not, as "••••"."""
    spec = json.loads(json.dumps(spec))
    for _, server in servers_in({"name": name, "kind": kind, "spec": spec}):
        for field, _ in FIELDS:
            for k, v in (server.get(field) or {}).items():
                if is_sealed(v) or (v and secret(k)):
                    server[field][k] = HIDDEN
    return spec


def hidden(manifest):
    """Whether a manifest has values it can't send as they are ("••••" or sealed), so its tools can't be listed now."""
    return any(
        v == HIDDEN or is_sealed(v)
        for _, server in servers_in(manifest)
        for field, _ in FIELDS
        for v in (server.get(field) or {}).values()
        if isinstance(server.get(field), dict)
    )


def open_run(db, run):
    """An MCP tool's run (extensions.mcp_tools) with its sealed values opened, just before the server is reached."""
    run = dict(run)
    for field, label in FIELDS:
        if run.get(field):
            run[field] = {k: _open(db, run.get("ext"), run.get("server"), label, k, run, v) for k, v in run[field].items()}
    return run


# ---------- configs pasted from other apps ----------
def entry(c):
    """One server of a config, as an MCP extension keeps it: (spec, None), or (None, why it's left out)."""
    if not isinstance(c, dict):
        return None, "it isn't an object with a command or an address"
    kind = str(c.get("type") or c.get("transport") or "").lower().replace("_", "-")
    if c.get("command") is not None or kind == "stdio":
        if not isinstance(c.get("command"), str) or not c["command"].strip():
            return None, "it says it runs a program but not which"
        args = [str(x) for x in c.get("args") or [] if isinstance(x, (str, int, float))]
        env = {k: str(v) for k, v in (c.get("env") or {}).items() if isinstance(k, str) and isinstance(v, (str, int, float))}
        return store.clean({"command": c["command"].strip(), "args": args or None, "env": env or None}), None
    if kind == "sse":
        return None, "it uses the older SSE transport; Lens speaks MCP over Streamable HTTP (many servers have both: try its /mcp address)"
    if kind and kind not in ("http", "streamable-http", "streamablehttp"):
        return None, f"Lens speaks MCP over HTTP; this one uses {kind}"
    url = c.get("url") or c.get("serverUrl") or c.get("httpUrl")
    if not isinstance(url, str) or not url.strip():
        return None, "it has neither a command nor an address"
    headers = {k: str(v) for k, v in (c.get("headers") or {}).items() if isinstance(k, str) and isinstance(v, (str, int, float))}
    return store.clean({"url": url.strip(), "headers": headers or None}), None


def guess_name(server):
    """A name for a server given without one: its package (`@modelcontextprotocol/server-github`: github), or the
    site its address is on."""
    if server.get("url"):
        labels = [x for x in (urllib.parse.urlsplit(server["url"]).hostname or "").split(".") if x]
        word = labels[-2] if len(labels) > 1 else labels[0] if labels else ""
    else:
        args = [a for a in server.get("args") or [] if not a.startswith("-")]
        word = next((a for a in args if re.search(r"mcp|server", a, re.I)), args[0] if args else server.get("command") or "")
        word = re.sub(r"@[^/@]*$", "", word.rstrip("/"))  # a version: @latest
        parts = word.rsplit("/", 1)
        word = parts[-1]
        if word.lower() in ("mcp", "server", "mcp-server") and len(parts) > 1:  # @playwright/mcp: playwright
            word = parts[0].lstrip("@").rsplit("/", 1)[-1]
        word = re.sub(r"^(mcp[-_])?(server[-_])?|([-_]mcp)?([-_]server)?$", "", word, flags=re.I) or word
    return word


def _fill(server):
    found = []
    for v in [server.get("url"), server.get("command"), *(server.get("args") or [])]:
        found += PLACEHOLDER_RX.findall(str(v or ""))
    for field, _ in FIELDS:
        for v in (server.get(field) or {}).values():
            found += PLACEHOLDER_RX.findall(str(v))
    return list(dict.fromkeys(found))


def _servers(conf):
    """[(the name it was given, its settings)] from a config, whichever app's shape it has."""
    if isinstance(conf, dict):
        vscode = conf.get("mcp") if isinstance(conf.get("mcp"), dict) else {}
        for got in (conf.get("mcpServers"), conf.get("servers"), vscode.get("servers")):
            if isinstance(got, (dict, list)):
                conf = got
                break
        else:
            if any(k in conf for k in ("command", "url", "serverUrl", "httpUrl")):  # one server on its own
                conf = [conf]
    if isinstance(conf, list):  # [{name, command, ...}], as some apps keep them
        return [(str(c.get("name") or "") if isinstance(c, dict) else "", c) for c in conf]
    return list(conf.items()) if isinstance(conf, dict) else []


def read(text):
    """The servers in a config pasted from another app: {servers: [{name, description, spec, fill}], skipped: [{name,
    why}]}. `fill` names the values written as ${NAME}, which that app fills in from its environment and Lens can't."""
    from .marketplace import slug

    text = (text or "").strip()
    try:
        conf = json.loads(text)
    except ValueError as e:
        if text[:1] in "{[":
            raise ValueError(f"that isn't JSON: {e}") from None
        try:
            conf = yaml.safe_load(text)  # some apps keep theirs in YAML
        except yaml.YAMLError:
            raise ValueError(f"paste an MCP config, such as {EXAMPLE}") from None
    servers = _servers(conf)
    if not servers:
        raise ValueError(f"paste an MCP config, such as {EXAMPLE}")
    out, skipped, names = [], [], set()
    for given, c in servers[:MAX_SERVERS]:
        spec, why = entry(c)
        if spec is None:
            skipped.append({"name": given or "a server", "why": why})
            continue
        try:
            name = slug(given or guess_name(spec))
        except ValueError:
            name = "mcp_server"
        base, n = name, 2
        while name in names:
            name, n = f"{base[:38]}_{n}", n + 1
        names.add(name)
        out.append({"name": name, "description": f"The {given or name} MCP server", "spec": spec, "fill": _fill(spec)})
    skipped += [{"name": n or "a server", "why": f"a config adds at most {MAX_SERVERS} servers at once"} for n, _ in servers[MAX_SERVERS:]]
    return {"servers": out, "skipped": skipped}


# ---------- ready-made servers (Add MCP server) ----------
# The browser's tools that only go somewhere, and needn't ask before the conversation has read a page (they ask after,
# as every tool that reaches another host does); clicking, typing and filling in forms always ask
BROWSER_GOES = ("browser_navigate", "browser_navigate_back", "browser_wait_for")


def presets():
    """Servers to add with a click or two: [{name, label, description, spec, fill, needs}]. `fill` names the values to
    type in first, written ${NAME} in the spec; `needs` is the optional profile it runs in (services.py), if any."""
    browser = (os.environ.get("LENS_BROWSER_URL") or "http://browser:8931").rstrip("/")
    return [
        {
            "name": "browser",
            "label": "Browser",
            "description": "Opens web pages, reads them, clicks, types and fills in forms (Playwright MCP, in Lens's browser container)",
            # one session for all its calls: the page it has open stays open from one call to the next. Running code
            # in the browser's own server process starts off: it reaches past the browser
            "spec": {
                "url": f"{browser}/mcp",
                "keep": True,
                "off": ["browser_run_code_unsafe"],
                "ask": {t: False for t in BROWSER_GOES},
            },
            "fill": [],
            "needs": "browser",
        },
        {
            "name": "github",
            "label": "GitHub",
            "description": "Issues, pull requests and code on GitHub, with a personal access token (GitHub's own MCP server)",
            "spec": {"url": "https://api.githubcopilot.com/mcp/", "headers": {"Authorization": "Bearer ${GITHUB_TOKEN}"}},
            "fill": ["GITHUB_TOKEN"],
            "needs": None,
        },
    ]


def filled(spec, values):
    """A server's spec with the values typed in for its ${NAME}s put in their place (address, args, headers, env), and
    the names still missing a value."""
    missing = []

    def put(text):
        def one(m):
            if isinstance(values.get(m.group(1)), str) and values[m.group(1)]:
                return values[m.group(1)]
            missing.append(m.group(1))
            return m.group(0)

        return PLACEHOLDER_RX.sub(one, text) if isinstance(text, str) else text

    out = dict(spec)
    if out.get("url"):
        out["url"] = put(out["url"])
    if out.get("args"):
        out["args"] = [put(a) for a in out["args"]]
    for field, _ in FIELDS:
        if out.get(field):
            out[field] = {k: put(v) for k, v in out[field].items()}
    return out, sorted(set(missing))


# ---------- upgrading ----------
def migrate_seal(db):
    """Upgrade step: seal the credentials MCP servers kept in plain text in their headers and env, in every version of
    every MCP server and plugin. Safe to run twice: sealed values are left as they are."""
    for d in db.rows("SELECT record::id(id) AS id, name, kind FROM extension WHERE kind IN ['mcp', 'plugin']"):
        for v in db.rows("SELECT record::id(id) AS id, spec FROM extension_version WHERE extension = $e", e=d["id"]):
            if not isinstance(v.get("spec"), dict):
                continue
            spec = seal(db, d["id"], d["name"], d["kind"], v["spec"])
            if spec != v["spec"]:
                db.q("UPDATE $r SET spec = $s", r=store.R("extension_version", v["id"]), s=spec)
