"""The HTTP call node of asset and graph workflows (scopes `recording` and `graph`; docs/processing.md, Workflows).

`http` sends GET, POST, PUT, PATCH or DELETE to a web address and passes on the answer: {status, ok, url (where it
ended), content_type, body (its text, up to MAX_ANSWER), json (when it is JSON), truncated, untrusted}. A 4xx or 5xx
answer is passed on with `ok` false, so a condition can branch on it; an address that can't be reached fails the node.

It goes out the way the assistant's web tools do (extensions.http_call, web.read): through netguard's proxy, to public
addresses on the web's ports only (or networks an admin allowed for web pages, `documents.web_networks`), every
redirect checked again, and never on to another server with its credentials. Each call is a `workflow.http` row in
the activity ledger, with the host and method, never what was sent or answered.

The address, the header values and the body are templates, rendered like a template node's (what came in is
`{{ input }}`, beside the asset or the namespaces), but the scheme, host and port are written out and can't come from
a template, so what flows through a workflow never decides where it is sent. Header values that carry credentials
(Authorization, cookies, API keys and tokens: actions_web.secret) are sealed with the server's key when the workflow is
saved, bound to the header and to the scheme, host and port they were saved for, so a sealed value goes nowhere else;
they aren't templates, and are opened only to send.

A try on the canvas sends a GET (it only reads) and says what it would send for the other methods. Automations call
web addresses with their own action (actions_web.py, `http_request`), which asks first; this node is not in their
scope.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from . import activity, flow, mcp_servers, settings, templates

OWN_NODES = ("http",)
CONFIG = {"http": {"method", "url", "headers", "body", "seconds"}}
METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE")
READS = ("GET",)  # what a try on the canvas sends
URL_RX = re.compile(r"^https?://[^/?#{}\s]+(?:[/?#]|$)")  # the host is written out, not a template
MAX_URL, MAX_BODY, MAX_HEADERS = 2000, 64 * 1024, 30
MAX_ANSWER = 200_000  # the most of an answer that is read, as the assistant's web tools read
SECONDS = 20
AGENT = "Lens workflow"


def _secret(name):
    from .actions_web import secret  # loaded here: the automation actions load what workflows load

    return secret(name)


def _origin(url):
    u = urllib.parse.urlsplit(url)
    return f"{u.scheme}://{(u.hostname or '').lower()}:{u.port or (443 if u.scheme == 'https' else 80)}"


def _context(name, url):
    return f"workflow-http:{name.lower()}@{_origin(url)}"


def _key(db):
    return {"data_dir": db.data_dir}


def check_config(db, nid, cfg):
    """ValueError naming what is wrong with an HTTP node's settings. Credential headers given in plain text are sealed
    in `cfg` as it is kept; sealed ones must have been sealed for this address."""
    from .actions_web import FIXED, NAME_RX

    method = str(cfg.get("method") or "GET").upper()
    if method not in METHODS:
        raise ValueError(f"http node {nid}: the method is one of {', '.join(METHODS)}")
    cfg["method"] = method
    url = cfg.get("url")
    if not isinstance(url, str) or len(url) > MAX_URL or not URL_RX.match(url.strip()):
        raise ValueError(
            f"http node {nid}: give a web address that starts with http:// or https:// and a host written out "
            "(templates go after it, in its path or query)"
        )
    cfg["url"] = url.strip()
    _template(nid, "address", cfg["url"])
    headers = cfg.get("headers") or {}
    if not isinstance(headers, dict) or len(headers) > MAX_HEADERS:
        raise ValueError(f"http node {nid}: headers are up to {MAX_HEADERS} names and values")
    kept = {}
    for k, v in headers.items():
        if not NAME_RX.match(str(k)) or str(k).lower() in FIXED:
            raise ValueError(f"http node {nid}: {k} isn't a header it can set")
        if not isinstance(v, str) or "\r" in v or "\n" in v or len(v) > 8000:
            raise ValueError(f"http node {nid}: the {k} header is one line of text")
        if _secret(k) and v:
            v = _seal(db, nid, k, v, cfg["url"])
        elif not mcp_servers.is_sealed(v):
            _template(nid, f"{k} header", v)
        kept[str(k)] = v
    if kept:
        cfg["headers"] = kept
    else:
        cfg.pop("headers", None)
    body = cfg.get("body")
    if body is not None:
        if not isinstance(body, str) or len(body) > MAX_BODY:
            raise ValueError(f"http node {nid}: the body is a template of up to {MAX_BODY // 1024} KB")
        if body and method in ("GET", "DELETE"):
            raise ValueError(f"http node {nid}: a {method} sends no body")
        _template(nid, "body", body)
    seconds = cfg.get("seconds")
    if seconds is not None and not (isinstance(seconds, int) and not isinstance(seconds, bool) and 1 <= seconds <= 60):
        raise ValueError(f"http node {nid}: it waits 1 to 60 seconds")


def _template(nid, what, text):
    try:
        templates.check(text)
    except templates.TemplateProblem as e:
        raise ValueError(f"http node {nid}: its {what}: {e}") from None


def _seal(db, nid, name, value, url):
    """A credential header's value as it is kept: sealed for this header and address (a sealed one is checked)."""
    ctx = _context(name, url)
    if not mcp_servers.is_sealed(value):
        return mcp_servers.SEALED + settings.seal(_key(db), value, ctx)
    try:
        settings.unseal(_key(db), value[len(mcp_servers.SEALED) :], ctx)
    except Exception:  # noqa: BLE001 - sealed for another header or address, or with another server key
        raise ValueError(f"http node {nid}: enter the {name} header again: it was sealed for another address") from None
    return value


def _open(db, name, value, url):
    try:
        return settings.unseal(_key(db), value[len(mcp_servers.SEALED) :], _context(name, url))
    except Exception:  # noqa: BLE001
        raise ValueError(f"enter the {name} header again: it was sealed for another address") from None


def shown(headers):
    """Headers as they're said: credentials, sealed or not, as "••••"."""
    return {k: mcp_servers.HIDDEN if _secret(k) or mcp_servers.is_sealed(v) else v for k, v in (headers or {}).items()}


def run_node(r, f, node, value, vals):
    """What an HTTP node does: {out: the answer}."""
    from . import webcapture

    c, name = node["config"], flow._label(node)
    tctx = {**flow._tctx(r), "input": None if value is r.ctx else value}
    method = c.get("method") or "GET"
    url = templates.render_body(c["url"], tctx, "prompt").strip()
    if urllib.parse.urlsplit(url).netloc.lower() != urllib.parse.urlsplit(c["url"]).netloc.lower() or re.search(r"\s", url):
        raise ValueError(f"{name}: what it was given can't change where its request goes")
    try:
        url = webcapture.check_url(r.cfg, url)
    except ValueError as e:
        raise ValueError(f"{name}: {e}") from None
    headers = {"User-Agent": AGENT}
    for k, v in (c.get("headers") or {}).items():
        headers[k] = _open(r.db, k, v, c["url"]) if mcp_servers.is_sealed(v) else templates.render_body(v, tctx, "prompt")
        if "\r" in headers[k] or "\n" in headers[k]:
            raise ValueError(f"{name}: the {k} header came out as more than one line")
    data = None
    if c.get("body"):
        text = templates.render_body(c["body"], tctx, "prompt")
        data = text.encode("utf-8")
        if len(data) > MAX_BODY:
            raise ValueError(f"{name}: the body is over {MAX_BODY // 1024} KB")
        if not any(k.lower() == "content-type" for k in headers):
            headers["Content-Type"] = "application/json" if _is_json(text) else "text/plain; charset=utf-8"
    sent = {"method": method, "url": url, "headers": shown({k: v for k, v in headers.items() if k != "User-Agent"})}
    if r.dry and method not in READS:
        r.say(f"{name}: would {method} {url}")
        return {"out": {**sent, "status": None, "would": f"{method} {url}"}}
    out = call(r.cfg, method, url, headers, data, c.get("seconds") or SECONDS, _refs(r))
    r.say(f"{name}: {method} {urllib.parse.urlsplit(url).hostname} answered {out['status']}")
    return {"out": out}


def _is_json(text):
    if text.lstrip()[:1] not in ("{", "["):
        return False
    try:
        json.loads(text)
    except ValueError:
        return False
    return True


def _refs(r):
    rid = r.extra.get("rid")
    return [f"recording:{int(rid)}"] if rid is not None else [f"space:{s}" for s in sorted(r.extra.get("spaces") or [])]


def call(cfg, method, url, headers, data, seconds, refs=()):
    """One request through netguard (url already checked by webcapture.check_url): the answer as the node passes it
    on. ValueError when it can't be reached."""
    from . import feeds, netguard, webcapture

    host = urllib.parse.urlsplit(url).hostname
    req = urllib.request.Request(url, data=data, method=method, headers=headers)
    with (
        activity.call("workflow.http", cfg, refs=refs, detail={"host": host, "method": method}),
        netguard.Guard(forward=True, networks=webcapture.networks(cfg), max_bytes=MAX_ANSWER + 65536) as guard,
    ):
        try:
            with feeds._opener(guard).open(req, timeout=seconds) as resp:
                status, final, ctype, raw = resp.status, resp.geturl(), resp.headers.get("Content-Type", ""), resp.read(MAX_ANSWER + 1)
        except feeds.OtherServer as e:
            raise ValueError(f"{host}: {e.reason}") from None
        except urllib.error.HTTPError as e:
            if guard.refused:  # the proxy turned it away (a redirect to an inside address, another port)
                raise ValueError(f"{host} couldn't be reached; refused: {guard.refused[0]}") from None
            status, final, raw = e.code, url, e.read(MAX_ANSWER + 1) if e.fp is not None else b""
            ctype = e.headers.get("Content-Type", "") if e.headers else ""
        except (urllib.error.URLError, OSError) as e:
            refused = f"; refused: {guard.refused[0]}" if guard.refused else ""
            raise ValueError(f"couldn't reach {host}: {getattr(e, 'reason', e)}{refused}") from None
    more = len(raw) > MAX_ANSWER
    text = raw[:MAX_ANSWER].decode("utf-8", "replace")
    out = {
        "status": status,
        "ok": 200 <= status < 300,
        "url": final,
        "content_type": ctype,
        "body": text,
        "truncated": more,
        "untrusted": True,  # what another server answered: outside text
    }
    if not more and "json" in ctype.lower():
        try:
            out["json"] = json.loads(text)
        except ValueError:
            pass
    return out
