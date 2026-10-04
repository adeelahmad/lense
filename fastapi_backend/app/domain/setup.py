"""First-run setup: what a fresh install still needs, from the environment (.env) or the web wizard.

A fresh install (no accounts when the API first starts) is marked with setup:wizard. The web app then walks the first
admin through the rest (the first namespace, the model provider, storage) until they finish or skip it; installs that
already had accounts never see the wizard. Whatever the environment sets is applied at startup and wins: the wizard
shows those fields locked.

  LENS_ADMIN_EMAIL, LENS_ADMIN_NAME                        the first admin, created at startup (no setup code needed);
                                                           the log prints a link for adding their passkey
  LENS_ADMIN_PASSWORD                                      ... with a password instead (passwords stay on)
  LENS_NAMESPACE                                           the first namespace, created at startup while there is none
  LENS_LLM_BASE_URL, LENS_LLM_MODEL, LENS_LLM_API_KEY      the model provider (settings.ENV_OVERRIDES)
  LENS_TELEMETRY, LENS_TELEMETRY_ENDPOINT                  opt-in telemetry (off unless set on; docs/telemetry.md)
  LENS_SETUP_WIZARD=off                                    never show the wizard (everything comes from .env)

The first admin still needs the one-time setup code (or the environment), so a stranger can't claim a public server.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from . import auth, settings, sources, store

R = store.R
WIZARD = ("setup", "wizard")
log = logging.getLogger("lens")


def _off():
    return os.environ.get("LENS_SETUP_WIZARD", "").strip().lower() in ("off", "false", "0", "no", "done")


def pending(db):
    """Whether the web wizard is still to be finished."""
    if _off():
        return False
    row = db.one("SELECT done_at FROM $r", r=R(*WIZARD))
    return bool(row) and not row.get("done_at")


def mark_fresh(db, cfg=None):
    """Called at startup while no accounts exist: this install gets the wizard (once; finishing it is kept). A new
    archive (nothing in it yet) also encrypts the files it keeps from the start (docs/encryption.md); one that has
    files already keeps its setting, and `lens encrypt` converts it."""
    if not db.one("SELECT id FROM $r", r=R(*WIZARD)):
        db.q("UPSERT $r CONTENT $d", r=R(*WIZARD), d={"created_at": store.now()})
        empty = not db.values("SELECT VALUE id FROM recording LIMIT 1")
        if cfg is not None and empty and not db.one("SELECT id FROM $r", r=R("app_setting", "encryption")):
            settings.save(db, cfg, "encryption", {"files": True}, user="setup")


def finish(db, user=None, skipped=False):
    db.q("UPDATE $r SET done_at = $t, done_by = $u, skipped = $s", r=R(*WIZARD), t=store.now(), u=user, s=skipped)


def env_admin():
    email, password = os.environ.get("LENS_ADMIN_EMAIL", "").strip(), os.environ.get("LENS_ADMIN_PASSWORD", "")
    return (email, password or None, os.environ.get("LENS_ADMIN_NAME", "").strip() or None) if email else None


def env_namespace():
    return os.environ.get("LENS_NAMESPACE", "").strip().lower() or None


def apply_env(db):
    """Create what the environment names while the archive has none: the first admin, the first namespace. Only then,
    so one deleted later doesn't come back on the next start."""
    admin = env_admin()
    if admin and auth.account_count(db) == 0:
        try:
            uid = auth.create_account(db, admin[0], admin[1], admin[2], admin=True)
        except ValueError as e:
            log.error("LENS_ADMIN_EMAIL / LENS_ADMIN_PASSWORD: %s; create the first admin in the web app instead", e)
        else:
            auth.audit(db, {"id": uid, "email": admin[0]}, "setup", detail=["environment"])
            log.info("Created the first admin %s from the environment", admin[0])
            if not admin[1]:
                from . import passkeys

                raw = passkeys.create_link(db, uid)
                log.warning("Add the first admin's passkey at %s", passkeys.link_url(raw))
    name = env_namespace()
    if name and not store.space_names(db):
        if not store.NS_RX.match(name):
            log.error("LENS_NAMESPACE %r: namespace names use lowercase letters, digits, - and _", name)
        else:
            store.ns_id(db, name)


def view(db, cfg):
    """The wizard's starting point: what is set already, and which fields the environment or archive.yaml locks."""
    shown = settings.effective(db, cfg, reveal=False)
    llm = {k: shown["llm"].get(k) for k in ("base_url", "model", "api_key", "vision_model")}
    if not isinstance(llm["api_key"], dict):
        llm["api_key"] = {"secret": True, "set": False}
    names = [r["name"] for r in db.rows("SELECT name FROM space ORDER BY name")]
    admin = env_admin()
    return {
        "pending": pending(db),
        "admin": {"from_env": bool(admin)},
        "namespace": {
            "existing": names,
            # archive.yaml or LENS_NAMESPACE named the namespaces: the wizard shows them instead of asking
            "locked": bool(env_namespace() or cfg["namespaces"]),
        },
        "llm": {"values": llm, "locked": settings.locked("llm")},
        "storage": {
            "data_dir": cfg["data_dir"],
            "database": db.url,
            "embedded": db.embedded,
            "local_roots": cfg["sources"].get("local_roots") or [],
            "max_upload_mb": shown["uploads"]["max_mb"],
            "watches": len(db.values("SELECT VALUE id FROM watch_path")),
        },
        "telemetry": {
            "enabled": bool(shown["telemetry"].get("enabled")),
            "endpoint": shown["telemetry"].get("endpoint"),
            "locked": [k for k in settings.locked("telemetry") if k in ("enabled", "endpoint")],
        },
    }


def save_namespace(db, name, graph="shared"):
    name = (name or "").strip().lower()
    if not store.NS_RX.match(name):
        raise ValueError("namespace names use lowercase letters, digits, - and _")
    if graph not in ("shared", "isolated"):
        raise ValueError("graph is shared or isolated")
    sid = store.ns_id(db, name)
    db.q("UPDATE $r SET graph = $g", r=R("space", sid), g=graph)
    return sid


def save_llm(db, cfg, values, user=None):
    """Save the model provider, leaving out what the environment sets (it would win anyway)."""
    keep = {k: v for k, v in values.items() if k not in settings.locked("llm")}
    if keep:
        settings.save(db, cfg, "llm", keep, user)
    return sorted(keep)


def save_telemetry(db, cfg, enabled, endpoint, user=None):
    """Opt in to telemetry (or stay out), leaving out what the environment sets. On needs an endpoint."""
    locked = settings.locked("telemetry")
    endpoint = (endpoint or "").strip() or None
    if enabled and not endpoint and "endpoint" not in locked:
        raise ValueError("telemetry needs an endpoint to send to, like http://localhost:4318")
    values = {"enabled": bool(enabled), **({"endpoint": endpoint} if endpoint or enabled else {})}  # off keeps the address
    keep = {k: v for k, v in values.items() if k not in locked}
    if keep:
        settings.save(db, cfg, "telemetry", keep, user)
    return sorted(keep)


def save_storage(db, cfg, max_upload_mb=None, folder=None, namespace=None, user=None):
    """The upload limit, and optionally a folder on this machine (inside sources.local_roots) watched into a
    namespace. Returns the watch's id, if one was made."""
    if max_upload_mb is not None:
        settings.save(db, cfg, "uploads", {"max_mb": max_upload_mb}, user)
    if not folder:
        return None
    if not namespace:
        raise ValueError("choose the namespace the folder's files go into")
    sid = store.ns_id(db, namespace, create=False)
    src = next((s["id"] for s in sources.list_sources(db) if s.get("type") == "local"), None)
    if src is None:
        src = sources.create(db, cfg, "Folders on this machine", "local", {}, {}, user)
    return sources.create_watch(db, cfg, src, folder, sid, user)


# Model servers people run themselves, on the ports they listen on by default. Looked for on this machine, on the
# Docker host (host.docker.internal; the compose files map it on Linux too) and as a compose service named ollama.
LOCAL_SERVERS = (("Ollama", 11434), ("LM Studio", 1234), ("llama.cpp", 8080), ("vLLM", 8000), ("LocalAI", 8081))
LOCAL_HOSTS = ("localhost", "host.docker.internal", "ollama")
NOT_CHAT = ("embed", "rerank", "whisper", "tts", "clip", "bge-", "minilm")


_DIRECT = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # local addresses: never through a proxy


def _models_at(base_url, timeout):
    try:
        with _DIRECT.open(base_url + "/models", timeout=timeout) as r:
            data = json.load(r).get("data")
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, AttributeError):
        return None
    if not isinstance(data, list):
        return None
    return sorted({str(m["id"]) for m in data if isinstance(m, dict) and m.get("id")})


def detect_llm(timeout=1.5, hosts=None, servers=None):
    """OpenAI-compatible model servers that answer on their usual ports, with their models: what the wizard offers
    so nobody has to type an address. One entry per server (the first address that reaches it), chat models first,
    with the one to suggest."""
    tries = [(kind, f"http://{h}:{port}/v1") for kind, port in servers or LOCAL_SERVERS for h in hosts or LOCAL_HOSTS]
    with ThreadPoolExecutor(max_workers=len(tries)) as pool:
        answers = list(pool.map(lambda t: _models_at(t[1], timeout), tries))
    found, seen = [], set()
    for (kind, url), models in zip(tries, answers, strict=True):
        if not models or (kind, tuple(models)) in seen:
            continue
        seen.add((kind, tuple(models)))
        chat = [m for m in models if not any(w in m.lower() for w in NOT_CHAT)]
        found.append(
            {"kind": kind, "base_url": url, "models": chat + [m for m in models if m not in chat], "suggested": (chat or models)[0]}
        )
    return found
