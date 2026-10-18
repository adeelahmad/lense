"""Secrets kept outside Lens: a secret field can hold a reference to an item in a password manager instead of the secret.

A secret field in Settings (an API key, a token, a password: settings.SECRETS) holds the secret itself, sealed with the
server's key (the built-in provider, the default), or a reference to where it is kept, such as
``vaultwarden://OpenAI/password``: the provider, the item and the field to read. Lens reads the value when it builds its
settings (settings.effective) and keeps it in memory for secrets.cache_minutes; after that it reads it again in the
background, so a key changed in the vault reaches Lens without anyone saving a setting. Only the reference is stored.

People and agents see references, never what they point at: Settings, the API (``GET /settings``, ``GET /secrets``)
and the assistant's and MCP's ``list_secrets`` show a field's reference and whether it could be read, and listing a
vault shows its items' names and field names, not their values. A provider reads only the items it was allowed:
Vaultwarden, those in one folder (secrets.vaultwarden_folder, "Lens" by default).

A vault that can't be reached for a while keeps the values read before it; an item that is gone, or moved out of the
folder, stops being used at the next read. Providers: builtin (the sealed store, always there) and vaultwarden
(vaultwarden_secrets.py). A new one is a Provider in PROVIDERS, with its own scheme.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import logging
import re
import threading
import time
import urllib.parse

log = logging.getLogger("lens")

RETRY_SECONDS = 60  # a reference that couldn't be read is tried again after this
_now = time.time  # the clock (tests move it on)
FIELD_RX = re.compile(r"^[^\x00-\x1f]{1,200}$")


class Missing(ValueError):
    """The vault answered: there's no such item or field where Lens may read (the reference stops being used)."""


class Unavailable(RuntimeError):
    """The vault couldn't be asked: not set up, not installed yet, unreachable, or it refused Lens's sign-in."""


@dataclasses.dataclass(frozen=True)
class Ref:
    provider: str
    item: str
    field: str | None
    text: str


class Provider:
    """Where secrets can be kept. `scheme` is how references to it start (None: it's not referred to)."""

    id = ""
    label = ""
    scheme: str | None = None

    def configured(self, cfg) -> bool:
        return False

    def describe(self, cfg) -> dict:
        """What Settings, the API and agents see about it: never a secret."""
        return {"id": self.id, "label": self.label, "scheme": self.scheme, "configured": self.configured(cfg)}

    def fingerprint(self, cfg) -> str:
        """What values read from it are kept under: another server, account or scope reads them again."""
        return self.id

    def read(self, cfg, refs: list[Ref]) -> dict[str, str | Exception]:
        """Each reference's value, or why it couldn't be read (Missing); raises Unavailable when nothing could."""
        raise Unavailable(f"{self.label} can't be referred to")

    def items(self, cfg, search=None, limit=200) -> list[dict]:
        """The items a reference may point at: names, field names and the reference to each; never their values."""
        return []

    def test(self, cfg) -> dict:
        return {"ok": True}

    def reset(self):
        """Drop any sign-in held: its settings changed."""


class Builtin(Provider):
    """Secrets typed into Settings, kept in the database sealed with the server's key (settings.seal)."""

    id = "builtin"
    label = "Saved in Lens"

    def configured(self, cfg):
        return True

    def describe(self, cfg):
        return {**super().describe(cfg), "scope": "encrypted with this server's key (AES-256-GCM)"}


def by_scheme(scheme):
    return next((p for p in PROVIDERS.values() if p.scheme and p.scheme == scheme), None)


# ---------- references ----------
def _quote(part):
    return part.replace("%", "%25").replace("/", "%2F")


def reference(provider, item, field=None):
    """The reference to an item's field, e.g. vaultwarden://OpenAI/password (a / or % in a name is escaped)."""
    p = PROVIDERS[provider]
    return f"{p.scheme}://{_quote(item)}" + (f"/{_quote(field)}" if field else "")


def parse(value) -> Ref | None:
    """The reference a secret field holds, or None when it holds a secret (or nothing)."""
    if not isinstance(value, str) or "://" not in value:
        return None
    text = value.strip()
    scheme, _, rest = text.partition("://")
    p = by_scheme(scheme.lower())
    if p is None:
        return None
    item, _, field = rest.partition("/")
    return Ref(p.id, urllib.parse.unquote(item), urllib.parse.unquote(field) or None, text)


def is_reference(value) -> bool:
    return parse(value) is not None


def check(value) -> Ref:
    """A reference as a secret field may hold it; ValueError says what's wrong."""
    ref = parse(value)
    if ref is None:
        raise ValueError("not a reference: write it like vaultwarden://Item name/field")
    if not ref.item or not FIELD_RX.match(ref.item):
        raise ValueError(f"{ref.text} doesn't name an item: write it like {PROVIDERS[ref.provider].scheme}://Item name/field")
    if ref.field is not None and not FIELD_RX.match(ref.field):
        raise ValueError(f"{ref.text} names no field it can read")
    return ref


# ---------- what was read, kept in this process ----------
@dataclasses.dataclass
class _Entry:
    value: str | None
    error: str | None
    read_at: float | None  # when the value was read
    tried_at: float  # when it was last asked for
    missing: bool = False  # the vault answered that it isn't there (not: the vault couldn't be asked)


_CACHE: dict[tuple[str, str], _Entry] = {}
_LOCK = threading.Lock()  # guards _CACHE and the counters
_READING = threading.Lock()  # one read of a vault at a time in this process
_STATE = {"generation": 0, "next": 0.0, "busy": False}


def generation():
    """Changes whenever a value read from a vault (or why it couldn't be) changes: settings are built again."""
    return _STATE["generation"]


def forget():
    """Drop everything read and every sign-in held: the secrets settings changed (or a test starts afresh)."""
    with _LOCK:
        _CACHE.clear()
        _STATE.update(next=0.0, busy=False)
        _STATE["generation"] += 1
    for p in PROVIDERS.values():
        p.reset()


def _why(e) -> str:
    return str(e)[:300] or type(e).__name__


def _read(cfg, provider, refs):
    """Read some references from one provider now, and keep what was read."""
    fp = provider.fingerprint(cfg)
    with _READING:
        t0 = _now()
        try:
            if not provider.configured(cfg):
                raise Unavailable(f"{provider.label} isn't set up: Settings › Secrets")
            got = provider.read(cfg, refs)
        except Exception as e:  # noqa: BLE001 - the whole vault: every reference keeps what it had
            log.warning("secrets: couldn't read %s: %s", provider.label, _why(e))
            got = {r.text: e for r in refs}
    changed = False
    with _LOCK:
        for r in refs:
            old = _CACHE.get((fp, r.text))
            v = got.get(r.text)
            if isinstance(v, str):
                new = _Entry(v, None, t0, t0)
            elif isinstance(v, Missing):  # gone, or out of the folder: Lens stops using it
                new = _Entry(None, _why(v), None, t0, missing=True)
            else:  # couldn't ask: keep what was read before, if anything
                err = _why(v) if v is not None else f"{provider.label} didn't return it"
                new = _Entry(old.value if old else None, err, old.read_at if old else None, t0)
            if old is None or (old.value, old.error) != (new.value, new.error):
                changed = True
            _CACHE[(fp, r.text)] = new
        if changed:
            _STATE["generation"] += 1
        _STATE["next"] = 0.0


def resolve(cfg, found: dict) -> dict:
    """The values of references, {where: reference} -> {where: value or None}; what hasn't been read yet is read now."""
    by_provider: dict[str, list[tuple[object, Ref]]] = {}
    for where, text in found.items():
        ref = parse(text)
        if ref is not None:
            by_provider.setdefault(ref.provider, []).append((where, ref))
    out = {}
    for pid, items in by_provider.items():
        p = PROVIDERS[pid]
        fp = p.fingerprint(cfg)
        with _LOCK:
            missing = list({r.text: r for _, r in items if (fp, r.text) not in _CACHE}.values())
        if missing:
            _read(cfg, p, missing)
        with _LOCK:
            for where, r in items:
                e = _CACHE.get((fp, r.text))
                out[where] = e.value if e else None
    return out


def _due(cfg, texts, now):
    """The references to read again: kept longer than secrets.cache_minutes, or not read and last tried a while ago."""
    ttl = 60 * int(((cfg.get("secrets") or {}).get("cache_minutes")) or 10)
    due, nxt = {}, now + ttl
    for text in texts:
        ref = parse(text)
        if ref is None:
            continue
        p = PROVIDERS[ref.provider]
        e = _CACHE.get((p.fingerprint(cfg), text))
        at = (e.read_at + ttl) if e and e.value is not None and e.read_at else ((e.tried_at if e else 0) + RETRY_SECONDS)
        if at <= now:
            due.setdefault(ref.provider, []).append(ref)
        else:
            nxt = min(nxt, at)
    return due, nxt


def tick(cfg, texts):
    """Called as settings are used: once a value is due, it's read again in the background (the old one serves until
    then), and the settings are built again when something changed. Cheap when nothing is due."""
    now = _now()
    with _LOCK:
        if _STATE["busy"] or now < _STATE["next"]:
            return
        due, nxt = _due(cfg, texts, now)
        if not due:
            _STATE["next"] = nxt
            return
        _STATE["busy"] = True

    def run():
        try:
            for pid, refs in due.items():
                _read(cfg, PROVIDERS[pid], refs)
        finally:
            with _LOCK:
                _STATE["busy"] = False

    threading.Thread(target=run, daemon=True, name="secrets-refresh").start()


def verify(cfg, value):
    """Check a reference before it's saved: its provider is set up, and the vault has the item and field; ValueError
    says what's wrong, so nothing is saved that can't be read."""
    ref = check(value)
    p = PROVIDERS[ref.provider]
    if not p.configured(cfg):
        raise ValueError(f"set up {p.label} in Settings › Secrets before referring to it")
    _read(cfg, p, [ref])
    with _LOCK:
        e = _CACHE.get((p.fingerprint(cfg), ref.text))
    if e is None or e.value is None:
        raise ValueError((e.error if e else None) or f"{p.label} didn't return {ref.text}")
    return ref


def status(cfg, value) -> dict:
    """Whether a reference could be read in this process, and when; never the value."""
    ref = parse(value)
    if ref is None:
        return {}
    p = PROVIDERS[ref.provider]
    with _LOCK:
        e = _CACHE.get((p.fingerprint(cfg), ref.text))
    if e is None:
        return {"ref": ref.text, "provider": ref.provider, "read": None}
    when = dt.datetime.fromtimestamp(e.read_at, dt.timezone.utc).isoformat(timespec="seconds") if e.read_at else None
    return {"ref": ref.text, "provider": ref.provider, "read": e.value is not None, "error": e.error, "read_at": when, "missing": e.missing}


def mask(cfg, value) -> dict:
    """How a set secret field is shown: that it's set, and its reference when it holds one."""
    out = {"secret": True, "set": True}
    if parse(value) is not None:
        out.update(status(cfg, value))
    return out


def providers(cfg) -> list[dict]:
    return [p.describe(cfg) for p in PROVIDERS.values()]


PROVIDERS: dict[str, Provider] = {"builtin": Builtin()}

# each provider adds itself to PROVIDERS; at the end, since it builds on the classes above (and either module may be
# imported first)
from . import vaultwarden_secrets  # noqa: E402,F401
