"""Secrets read from a Vaultwarden or Bitwarden vault (secret_providers.py; docs/secrets.md).

Lens signs in as one account of the vault with that account's API key (client id and secret, from the vault's
Settings › Security › Keys) and its master password, which the vault's end-to-end encryption needs: the server only
ever holds the items encrypted, so without the password there is nothing to read. Make an account for Lens, put the
items it may read (or share them) into one folder of it (secrets.vaultwarden_folder, "Lens" by default), and keep
everything else out: Lens reads only that folder. Each read syncs the vault, decrypts the folder's item names to find
the item, and the one field referred to; the sign-in is held in memory, never written down, and the values read are
kept in memory for secrets.cache_minutes.

A reference is ``vaultwarden://<item name>`` (the item's password, or a note's text) or ``vaultwarden://<item
name>/<field>``: a login's ``username``, ``password``, ``totp`` (the key as kept), ``uri`` or ``notes``, a card's
``number`` or ``code``, or one of the item's own custom fields by its name.

The client is python-vaultwarden (Apache-2.0, its crypto from bitwardentools), fetched into the data folder as a
component the first time a Vaultwarden address is set (components.py), so the default install carries nothing for it.
"""

from __future__ import annotations

import hashlib
import logging
import threading
import uuid

from . import components
from .secret_providers import PROVIDERS, Missing, Provider, Unavailable, reference

log = logging.getLogger("lens")

TYPES = {1: "login", 2: "note", 3: "card", 4: "identity"}
# a type's own fields, in the part of the item they're kept in; "notes" is on every item
OWN = {
    1: ("login", ("username", "password", "totp", "uri")),
    2: ("secureNote", ()),
    3: ("card", ("cardholderName", "number", "brand", "expMonth", "expYear", "code")),
    4: (
        "identity",
        (
            "title", "firstName", "middleName", "lastName", "address1", "address2", "address3", "city", "state",
            "postalCode", "country", "company", "email", "phone", "ssn", "username", "passportNumber", "licenseNumber",
        ),
    ),
}  # fmt: skip
DEFAULT_FIELD = {1: "password", 2: "notes", 3: "number", 4: None}
TIMEOUT = 20


def settings(cfg):
    s = (cfg or {}).get("secrets") or {}
    return {
        "url": (s.get("vaultwarden_url") or "").strip().rstrip("/"),
        "email": (s.get("vaultwarden_email") or "").strip(),
        "client_id": (s.get("vaultwarden_client_id") or "").strip(),
        "client_secret": s.get("vaultwarden_client_secret") or "",
        "password": s.get("vaultwarden_password") or "",
        "folder": (s.get("vaultwarden_folder") or "Lens").strip(),
    }


def _get(d, *names):
    """A field of what the server sent, whichever case it uses (Vaultwarden answers camelCase, Bitwarden did PascalCase)."""
    for n in names:
        for k in (n, n[:1].upper() + n[1:]):
            if isinstance(d, dict) and d.get(k) is not None:
                return d[k]
    return None


def _hash(*parts):
    """A key in memory for settings, some of them secret: never shown or kept."""
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:16]


def device_id(cfg):
    """A device identifier of this server's own, the same every time, derived from its secret key (nothing kept)."""
    from . import settings as app_settings

    h = hashlib.sha256(b"lens vaultwarden device " + app_settings.secret_key(cfg)).digest()
    return str(uuid.UUID(bytes=h[:16], version=4))


class _Item:
    """One item of the folder, with its fields decrypted only when asked for."""

    def __init__(self, raw, key, decrypt):
        self.raw, self.key, self._decrypt = raw, key, decrypt
        self.type = int(_get(raw, "type") or 0)
        self.name = self._text(_get(raw, "name")) or ""

    def _text(self, value):
        if not value:
            return None
        return self._decrypt(value, self.key).decode("utf-8")

    def fields(self):
        """The names of the fields that hold something: the type's own, notes, and the custom ones."""
        part, own = OWN.get(self.type, (None, ()))
        sub = _get(self.raw, part) if part else None
        names = [f for f in own if _get(sub, f) or (f == "uri" and _get(sub, "uris"))]
        if _get(self.raw, "notes"):
            names.append("notes")
        for f in _get(self.raw, "fields") or []:
            n = self._text(_get(f, "name"))
            if n and _get(f, "value"):
                names.append(n)
        return names

    def value(self, field):
        """The field's text; Missing when the item has no such field (or it's empty)."""
        part, own = OWN.get(self.type, (None, ()))
        sub = _get(self.raw, part) if part else None
        if field in own:
            if field == "uri":
                uris = _get(sub, "uris") or []
                raw = _get(uris[0], "uri") if uris else _get(sub, "uri")
            else:
                raw = _get(sub, field)
            text = self._text(raw)
            if text:
                return text
        elif field == "notes":
            text = self._text(_get(self.raw, "notes"))
            if text:
                return text
        else:
            for f in _get(self.raw, "fields") or []:
                if self._text(_get(f, "name")) == field:
                    text = self._text(_get(f, "value"))
                    if text:
                        return text
                    break
        raise Missing(f"the item {self.name} has no {field} (it has: {', '.join(self.fields()) or 'nothing'})")


class _Vault:
    """What one sync found in the folder: items by name."""

    def __init__(self, folder, items):
        self.folder, self.items = folder, items
        self.by_name = {}
        for it in items:
            self.by_name.setdefault(it.name.casefold(), []).append(it)

    def item(self, name):
        found = self.by_name.get(name.strip().casefold()) or []
        if not found:
            raise Missing(f"there's no item called {name} in the folder {self.folder}")
        if len(found) > 1:
            raise Missing(f"there are {len(found)} items called {name} in the folder {self.folder}: rename one")
        return found[0]

    def value(self, name, field):
        it = self.item(name)
        f = field or DEFAULT_FIELD.get(it.type)
        if not f:
            raise Missing(f"say which field of {name} to read, like {reference('vaultwarden', name, 'email')}")
        return it.value(f)


class Vaultwarden(Provider):
    id = "vaultwarden"
    label = "Vaultwarden"
    scheme = "vaultwarden"

    def __init__(self):
        self._lock = threading.Lock()
        self._client = None  # (sign-in settings' hash, client): one signed-in client, kept while they stay
        self._vault = None  # (fingerprint, _Vault): the last sync, for listing

    def configured(self, cfg):
        s = settings(cfg)
        return all(s[k] for k in ("url", "email", "client_id", "client_secret", "password"))

    def fingerprint(self, cfg):
        s = settings(cfg)
        return "vaultwarden:" + _hash(s["url"], s["email"], s["client_id"], s["folder"])

    def describe(self, cfg):
        s = settings(cfg)
        return {
            **super().describe(cfg),
            "url": s["url"] or None,
            "email": s["email"] or None,
            "folder": s["folder"],
            "installed": self.installed(cfg),
            "scope": f"the items in the folder {s['folder']} of the account {s['email']}"
            if s["email"]
            else "the items in one folder of one account",
        }

    def installed(self, cfg):
        components.activate(cfg)
        return components.importable("vaultwarden.clients.bitwarden")

    def reset(self):
        with self._lock:
            self._client = self._vault = None

    # ---------- talking to the vault ----------
    def _sdk(self, cfg):
        components.activate(cfg)  # the package, fetched into the data folder once an address was set
        try:
            from vaultwarden.clients.bitwarden import BitwardenAPIClient
            from vaultwarden.utils import crypto
        except ImportError:
            raise Unavailable(
                "the Vaultwarden client isn't installed yet: it's fetched once an address is set (Settings › Components)"
            ) from None
        return BitwardenAPIClient, crypto

    def _signed_in(self, cfg):
        s = settings(cfg)
        fp = _hash(*(s[k] for k in ("url", "email", "client_id", "client_secret", "password")))  # a new key or password signs in again
        with self._lock:
            if self._client and self._client[0] == fp:
                return self._client[1]
        BitwardenAPIClient, _ = self._sdk(cfg)
        client = BitwardenAPIClient(
            s["url"], s["email"], s["password"], s["client_id"], s["client_secret"], device_id(cfg), timeout=TIMEOUT
        )
        with self._lock:
            self._client = (fp, client)
        return client

    def _sync(self, cfg):
        """Sign in (or use the sign-in held) and sync; what went wrong, in words that name no secret."""
        import httpx

        s = settings(cfg)
        client = self._signed_in(cfg)
        _, crypto = self._sdk(cfg)
        try:
            return client.sync(force_refresh=True), client, crypto
        except httpx.HTTPStatusError as e:
            self.reset()
            code = e.response.status_code
            if code in (400, 401, 403):
                raise Unavailable(
                    f"Vaultwarden at {s['url']} refused Lens's sign-in (HTTP {code}): check the API key and the account"
                ) from None
            raise Unavailable(f"Vaultwarden at {s['url']} answered HTTP {code}") from None
        except httpx.HTTPError as e:
            self.reset()
            raise Unavailable(f"couldn't reach Vaultwarden at {s['url']}: {type(e).__name__}: {e}"[:300]) from None

    def _read_vault(self, cfg):
        sync, client, crypto = self._sync(cfg)
        s = settings(cfg)
        decrypt = crypto.decrypt
        try:
            user_key = client.connect_token.user_key
            folders = {str(_get(f, "id")): decrypt(_get(f, "name"), user_key).decode("utf-8") for f in sync.Folders if _get(f, "name")}
        except Exception as e:  # noqa: BLE001 - the HMAC check fails when the master password is wrong
            self.reset()
            raise Unavailable(f"Vaultwarden's items don't open: check the master password ({type(e).__name__})") from None
        wanted = {fid for fid, name in folders.items() if name.strip().casefold() == s["folder"].casefold()}
        org_keys: dict[str, bytes | None] = {}

        def org_key(oid):
            if oid not in org_keys:
                try:
                    private = client.connect_token.orgs_key
                    org = next(o for o in sync.Profile.Organizations if str(o.Id) == oid)
                    org_keys[oid] = decrypt(org.Key, private)
                except Exception as e:  # noqa: BLE001 - an organization Lens's account can't open: its items are skipped
                    log.warning("secrets: vaultwarden organization %s can't be opened: %s", oid, type(e).__name__)
                    org_keys[oid] = None
            return org_keys[oid]

        items = []
        for raw in sync.Ciphers:
            if _get(raw, "deletedDate") or str(_get(raw, "folderId") or "") not in wanted:
                continue
            key = org_key(str(_get(raw, "organizationId"))) if _get(raw, "organizationId") else user_key
            if key is None:
                continue
            try:
                if _get(raw, "key"):  # an item with a key of its own, kept wrapped by the account's or organization's
                    key = decrypt(_get(raw, "key"), key)
                items.append(_Item(raw, key, decrypt))
            except Exception as e:  # noqa: BLE001 - one item that doesn't open doesn't hide the others
                log.warning("secrets: a vaultwarden item in %s can't be opened: %s", s["folder"], type(e).__name__)
        vault = _Vault(s["folder"], items)
        with self._lock:
            self._vault = (self.fingerprint(cfg), vault)
        return vault

    # ---------- the provider ----------
    def read(self, cfg, refs):
        vault = self._read_vault(cfg)
        out = {}
        for r in refs:
            try:
                out[r.text] = vault.value(r.item, r.field)
            except Missing as e:
                out[r.text] = e
        return out

    def items(self, cfg, search=None, limit=200):
        vault = self._read_vault(cfg)
        q = (search or "").strip().casefold()
        out = []
        for it in sorted(vault.items, key=lambda i: i.name.casefold()):
            if q and q not in it.name.casefold():
                continue
            default = DEFAULT_FIELD.get(it.type)
            out.append(
                {
                    "name": it.name,
                    "type": TYPES.get(it.type, "other"),
                    "fields": it.fields(),
                    "ref": reference(self.id, it.name) if default else None,
                    "refs": {f: reference(self.id, it.name, f) for f in it.fields()},
                }
            )
            if len(out) >= limit:
                break
        return out

    def test(self, cfg):
        s = settings(cfg)
        if not self.configured(cfg):
            return {
                "ok": False,
                "error": "set the address, the account's email, its API key (client id and secret) and its master password",
            }
        try:
            vault = self._read_vault(cfg)
        except Unavailable as e:
            return {"ok": False, "error": str(e)}
        return {"ok": True, "items": len(vault.items), "folder": s["folder"], "account": s["email"]}


PROVIDERS["vaultwarden"] = Vaultwarden()
