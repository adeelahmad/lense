"""HTTPS with certificates Lens gets and renews itself (Settings › HTTPS), from Let's Encrypt or ZeroSSL over ACME.

Lens runs Caddy (https://caddyserver.com), a single program that answers on the HTTPS port, gets a certificate for
each domain, renews it before it runs out and passes visitors on to the web app. Off by default. Two ways to prove
the domain is yours:

* **http**: the certificate authority visits http://<domain>/.well-known/acme-challenge/ (port 80) or the HTTPS
  port itself (TLS-ALPN). The domain's DNS points at this machine and ports 80 and 443 reach it.
* **dns**: Lens adds a TXT record through the DNS provider's API (Cloudflare, Route 53 and the other caddy-dns
  plugins), so nothing has to reach this machine to get the certificate, and wildcard names (*.example.com) work.

Caddy is fetched the first time it's needed (data_dir/bin): Caddy's release from GitHub, checked against its
checksums, or for a DNS provider a build with that provider's plugin from Caddy's download service. A caddy on PATH
(or LENS_CADDY) that has the plugin is used instead.

Only a process that has the HTTPS ports runs it (LENS_HTTPS_LISTEN=1: install.sh's docker-compose.https.yml gives
the worker the ports and sets it). Of those, one holds a lease on `app_service:https`, like the Cloudflare tunnel's,
and keeps what Caddy is doing there for the web app.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import platform
import re
import shutil
import subprocess
import tarfile
import threading
import time
import urllib.parse

import httpx
from cryptography.hazmat.primitives import serialization

from . import store

R = store.R
ROW = R("app_service", "https")
CAS = ("letsencrypt", "zerossl", "both", "custom")
CHALLENGES = ("http", "tls-alpn", "dns")
LEASE_SECONDS = 30
TICK_SECONDS = 5
RETRY = (5, 15, 60, 300)
LOG_LINES = 40
CADDY_VERSION = "2.10.2"
RELEASES = f"https://github.com/caddyserver/caddy/releases/download/v{CADDY_VERSION}/"
BUILDS = "https://caddyserver.com/api/download"
LETSENCRYPT = "https://acme-v02.api.letsencrypt.org/directory"
LETSENCRYPT_STAGING = "https://acme-staging-v02.api.letsencrypt.org/directory"
ZEROSSL = "https://acme.zerossl.com/v2/DV90"
HOST = re.compile(r"(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}")
MODULE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
PACKAGE = re.compile(r"(github\.com|gitlab\.com|codeberg\.org)/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(/v[0-9]+)?")
DURATION = re.compile(r"[0-9]+(ms|s|m|h)")

# The DNS providers the web app lists, with what each needs (the caddy-dns plugin's fields; secret ones are
# passwords). Any other caddy-dns plugin works too: its name, and its fields as JSON.
PROVIDERS: dict[str, dict] = {
    "cloudflare": {"label": "Cloudflare", "fields": [("api_token", "API token (Zone.Zone read, Zone.DNS edit)", True)]},
    "route53": {
        "label": "Amazon Route 53",
        "fields": [
            ("access_key_id", "Access key ID", False),
            ("secret_access_key", "Secret access key", True),
            ("region", "Region (optional)", False),
            ("hosted_zone_id", "Hosted zone ID (optional)", False),
        ],
    },
    "digitalocean": {"label": "DigitalOcean", "fields": [("auth_token", "API token", True)]},
    "duckdns": {"label": "Duck DNS", "fields": [("api_token", "Token", True)]},
    "porkbun": {"label": "Porkbun", "fields": [("api_key", "API key", True), ("api_secret_key", "Secret API key", True)]},
    "namecheap": {
        "label": "Namecheap",
        "fields": [("api_key", "API key", True), ("user", "User name", False), ("client_ip", "This server's public IP (optional)", False)],
    },
    "gandi": {"label": "Gandi", "fields": [("bearer_token", "Personal access token", True)]},
    "ovh": {
        "label": "OVHcloud",
        "fields": [
            ("endpoint", "Endpoint (ovh-eu, ovh-ca, ...)", False),
            ("application_key", "Application key", False),
            ("application_secret", "Application secret", True),
            ("consumer_key", "Consumer key", True),
        ],
    },
    "desec": {"label": "deSEC", "fields": [("token", "Token", True)]},
    "dnsimple": {
        "label": "DNSimple",
        "fields": [("api_access_token", "API access token", True), ("account_id", "Account ID (optional)", False)],
    },
    "netlify": {"label": "Netlify", "fields": [("personal_access_token", "Personal access token", True)]},
    "vultr": {"label": "Vultr", "fields": [("auth_token", "API key", True)]},
    "linode": {"label": "Linode (Akamai)", "fields": [("api_token", "API token", True)]},
    "ionos": {"label": "IONOS", "fields": [("auth_api_token", "API key (prefix.secret)", True)]},
    "bunny": {"label": "Bunny DNS", "fields": [("access_key", "API key", True)]},
    "netcup": {
        "label": "netcup",
        "fields": [("customer_number", "Customer number", False), ("api_key", "API key", True), ("api_password", "API password", True)],
    },
    "azure": {
        "label": "Azure DNS",
        "fields": [
            ("tenant_id", "Tenant ID", False),
            ("client_id", "Client ID", False),
            ("client_secret", "Client secret", True),
            ("subscription_id", "Subscription ID", False),
            ("resource_group_name", "Resource group", False),
        ],
    },
    "googleclouddns": {"label": "Google Cloud DNS", "fields": [("gcp_project", "Project ID", False)]},
    "powerdns": {
        "label": "PowerDNS",
        "fields": [("server_url", "API address", False), ("api_token", "API key", True), ("server_id", "Server ID (optional)", False)],
    },
    "rfc2136": {
        "label": "RFC 2136 (BIND, Knot, ...)",
        "fields": [
            ("server", "Server (host:53)", False),
            ("key_name", "TSIG key name", False),
            ("key_alg", "TSIG algorithm (hmac-sha256)", False),
            ("key", "TSIG secret", True),
        ],
    },
}


class HttpsError(RuntimeError):
    """Setting HTTPS up didn't work; the message says why, for the admin."""


def _now():
    return dt.datetime.now(dt.timezone.utc)


def _ts(t=None):
    return (t or _now()).isoformat(timespec="seconds")


def conf(cfg):
    return cfg.get("https") or {}


def enabled(cfg):
    return bool(conf(cfg).get("enabled"))


def domains(cfg):
    """The names to get certificates for, lower case."""
    return [d.strip().lower().rstrip(".") for d in conf(cfg).get("domains") or [] if isinstance(d, str) and d.strip()]


def hosts(cfg):
    """The host names the web app is served at over HTTPS (not wildcards), while HTTPS is on."""
    return [d for d in domains(cfg) if not d.startswith("*.")] if enabled(cfg) else []


def public_port():
    """The port HTTPS is reached at from outside (LENS_HTTPS_PORT, which install.sh publishes), 443 by default."""
    try:
        return int(os.environ.get("LENS_HTTPS_PORT") or 443)
    except ValueError:
        return 443


def public_url(cfg):
    """https://<first domain>, for links in emails, or "" when HTTPS is off."""
    h = hosts(cfg)
    if not h:
        return ""
    port = public_port()
    return f"https://{h[0]}" + ("" if port == 443 else f":{port}")


def origin(cfg):
    """Where Caddy sends visitors: the web app, as this server reaches it."""
    from app.config import settings as env

    return (
        conf(cfg).get("origin") or os.environ.get("LENS_HTTPS_ORIGIN") or os.environ.get("LENS_TUNNEL_ORIGIN") or env.FRONTEND_URL
    ).rstrip("/")


def credentials(cfg):
    """The DNS provider's fields ({name: value}), from the sealed dns_credentials (JSON)."""
    raw = conf(cfg).get("dns_credentials")
    if not raw or not isinstance(raw, str):
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return {str(k): v for k, v in d.items()} if isinstance(d, dict) else {}


def parse_credentials(value):
    """Settings › HTTPS: dns_credentials is a JSON object of the provider's fields. ValueError when it isn't."""
    try:
        d = json.loads(value)
    except ValueError:
        raise ValueError("https.dns_credentials is a JSON object of the DNS provider's fields") from None
    if not isinstance(d, dict) or not all(
        isinstance(k, str) and re.fullmatch(r"[a-z0-9_]{1,64}", k) and isinstance(v, (str, int, bool)) and len(str(v)) <= 4000
        for k, v in d.items()
    ):
        raise ValueError('https.dns_credentials is a JSON object of the DNS provider\'s fields: {"api_token": "..."}')
    return value


def package(cfg):
    """The Go package of the DNS provider's Caddy plugin: dns_package, else github.com/caddy-dns/<provider>."""
    t = conf(cfg)
    return (t.get("dns_package") or "").strip() or f"github.com/caddy-dns/{(t.get('dns_provider') or '').strip()}"


def check(cfg):
    """Settings › HTTPS before saving: ValueError for what can't work."""
    t = conf(cfg)
    names = domains(cfg)
    challenge = t.get("challenge") or "http"
    for d in names:
        bare = d[2:] if d.startswith("*.") else d
        if not HOST.fullmatch(bare):
            raise ValueError(f"{d} isn't a domain name; enter names like lens.example.com")
        if d.startswith("*.") and challenge != "dns":
            raise ValueError("a wildcard name (*.example.com) needs the DNS challenge")
    if len(names) > 50:
        raise ValueError("up to 50 domain names")
    email = (t.get("email") or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("the email address for the certificate authority isn't an email address")
    ca = t.get("ca") or "both"
    if ca == "custom":
        u = urllib.parse.urlsplit(t.get("ca_url") or "")
        if u.scheme != "https" or not u.hostname:
            raise ValueError("a custom certificate authority is its ACME directory's https:// address")
    if bool(t.get("eab_key_id")) != bool(t.get("eab_hmac_key")):
        raise ValueError("external account binding needs both the key ID and the HMAC key")
    if challenge == "dns":
        prov = (t.get("dns_provider") or "").strip()
        if not MODULE.fullmatch(prov):
            raise ValueError("choose the DNS provider that holds the domain, like cloudflare or route53")
        if not PACKAGE.fullmatch(package(cfg)):
            raise ValueError("the DNS plugin is a Go package, like github.com/caddy-dns/cloudflare")
        for r in t.get("dns_resolvers") or []:
            if not re.fullmatch(r"[A-Za-z0-9.:\[\]-]{1,100}", str(r)):
                raise ValueError("DNS resolvers are addresses, like 1.1.1.1 or 1.1.1.1:53")
        if t.get("propagation") and not DURATION.fullmatch(str(t["propagation"])):
            raise ValueError("how long to wait for the DNS record is a duration, like 2m")
    for k in ("https_port", "http_port"):
        v = t.get(k)
        if v is not None and not (isinstance(v, int) and not isinstance(v, bool) and 1 <= v <= 65535):
            raise ValueError(f"https.{k} is a port number")
    if t.get("https_port") and t.get("https_port") == t.get("http_port"):
        raise ValueError("the HTTPS and HTTP ports are different ports")
    if (t.get("client_auth") or "off") != "off":
        client_cas(t.get("client_ca") or "")
    elif t.get("client_ca"):
        client_cas(t["client_ca"])
    o = urllib.parse.urlsplit(origin(cfg))
    if o.scheme != "http" or not o.hostname:
        raise ValueError("the web app's address for HTTPS is an http:// address, like http://frontend:3000")
    if t.get("enabled") and not names:
        raise ValueError("enter the domain name Lens is reached at, like lens.example.com")


def client_cas(pem):
    """The CA certificates in PEM text, as base64 DER for Caddy. ValueError when there's none or one doesn't parse."""
    import base64

    from cryptography import x509

    blocks = re.findall(r"-----BEGIN CERTIFICATE-----[^-]+-----END CERTIFICATE-----", pem or "")
    if not blocks:
        raise ValueError("paste the PEM of the CA that signs client certificates (-----BEGIN CERTIFICATE-----)")
    out = []
    for b in blocks:
        try:
            c = x509.load_pem_x509_certificate(b.encode())
        except ValueError:
            raise ValueError("a client CA certificate doesn't parse; paste it as PEM") from None
        out.append(base64.b64encode(c.public_bytes(serialization.Encoding.DER)).decode())
    return out


# ---------- Caddy's configuration ----------
def _issuers(cfg):
    t = conf(cfg)
    challenge = t.get("challenge") or "http"
    if challenge == "dns":
        dns: dict = {"provider": {"name": t["dns_provider"].strip(), **credentials(cfg)}}
        if t.get("dns_resolvers"):
            dns["resolvers"] = [str(r) for r in t["dns_resolvers"]]
        if t.get("propagation"):
            dns["propagation_timeout"] = str(t["propagation"])
        challenges: dict = {"http": {"disabled": True}, "tls-alpn": {"disabled": True}, "dns": dns}
    elif challenge == "tls-alpn":
        challenges = {"http": {"disabled": True}}
    else:
        challenges = {}

    def acme(url, eab=False):
        i: dict = {"module": "acme", "ca": url}
        if (t.get("email") or "").strip():
            i["email"] = t["email"].strip()
        if challenges:
            i["challenges"] = challenges
        if eab and t.get("eab_key_id") and t.get("eab_hmac_key"):
            i["external_account"] = {"key_id": t["eab_key_id"], "mac_key": t["eab_hmac_key"]}
        return i

    ca = t.get("ca") or "both"
    le = acme(LETSENCRYPT_STAGING if t.get("staging") else LETSENCRYPT)
    if ca == "letsencrypt":
        return [le]
    if ca == "zerossl":
        return [acme(ZEROSSL, eab=True)]
    if ca == "custom":
        return [acme(t["ca_url"], eab=True)]
    # both: Let's Encrypt, and ZeroSSL when Let's Encrypt can't (ZeroSSL has no staging service)
    return [le] if t.get("staging") else [le, acme(ZEROSSL, eab=True)]


def caddy_config(cfg, storage):
    """Caddy's JSON configuration: HTTPS for the domains, passed on to the web app; HTTP sent to HTTPS."""
    t = conf(cfg)
    https_port, http_port = int(t.get("https_port") or 8443), int(t.get("http_port") or 8080)
    names = domains(cfg)
    o = urllib.parse.urlsplit(origin(cfg))
    upstream = f"{o.hostname}:{o.port or 80}"
    handle: list[dict] = []
    if t.get("hsts"):
        handle.append({"handler": "headers", "response": {"set": {"Strict-Transport-Security": ["max-age=31536000"]}}})
    handle.append({"handler": "reverse_proxy", "upstreams": [{"dial": upstream}]})
    routes: list[dict] = [{"match": [{"host": names}], "handle": handle, "terminal": True}]
    lens: dict = {
        "listen": [f":{https_port}"],
        "routes": routes,
        # its own redirect below: Caddy's would send people to the port inside the container
        "automatic_https": {"disable_redirects": True},
    }
    policy: dict = {}
    if t.get("strict_sni"):
        policy["match"] = {"sni": names}  # no other name, and no SNI at all, gets a handshake
        lens["strict_sni_host"] = True  # the Host a request names is the name its connection was made for
        routes.append({"handle": [{"handler": "static_response", "status_code": 421}]})
    mode = {"optional": "verify_if_given", "required": "require_and_verify"}.get(t.get("client_auth") or "off")
    if mode:
        policy["client_authentication"] = {
            "ca": {"provider": "inline", "trusted_ca_certs": client_cas(t.get("client_ca") or "")},
            "mode": mode,
        }
    if t.get("min_tls") == "1.3":
        policy["protocol_min"] = "tls1.3"
    if policy:
        lens["tls_connection_policies"] = [policy]
    servers: dict = {"lens": lens}
    if t.get("redirect_http", True):
        port = public_port()
        target = "https://{http.request.host}" + ("" if port == 443 else f":{port}") + "{http.request.uri}"
        servers["redirect"] = {
            "listen": [f":{http_port}"],
            "routes": [
                {
                    "match": [{"host": names}],
                    "handle": [{"handler": "static_response", "status_code": 308, "headers": {"Location": [target]}}],
                }
            ],
        }
    return {
        "admin": {"disabled": True},
        "logging": {"logs": {"default": {"level": "INFO", "encoder": {"format": "json"}}}},
        "storage": {"module": "file_system", "root": str(storage)},
        "apps": {
            "http": {"http_port": http_port, "https_port": https_port, "servers": servers},
            "tls": {"automation": {"policies": [{"subjects": names, "issuers": _issuers(cfg)}]}},
        },
    }


def certificates(storage):
    """The certificates Caddy keeps: [{names, expires, issuer}], soonest to expire first."""
    from cryptography import x509

    out = []
    for p in sorted(pathlib.Path(storage).glob("certificates/*/*/*.crt")):
        try:
            c = x509.load_pem_x509_certificate(p.read_bytes())
        except (OSError, ValueError):
            continue
        try:
            names = c.extensions.get_extension_for_class(x509.SubjectAlternativeName).value.get_values_for_type(x509.DNSName)
        except x509.ExtensionNotFound:
            names = []
        org = c.issuer.get_attributes_for_oid(x509.NameOID.ORGANIZATION_NAME)
        out.append(
            {
                "names": list(names),
                "expires": c.not_valid_after_utc.isoformat(timespec="seconds"),
                "issuer": str(org[0].value) if org else c.issuer.rfc4514_string(),
            }
        )
    return sorted(out, key=lambda c: c["expires"])


# ---------- what other processes see ----------
def state(db):
    row = db.one("SELECT * FROM $r", r=ROW) or {}
    row.pop("id", None)
    if row.get("log") and isinstance(row["log"], str):
        row["log"] = json.loads(row["log"])
    if row.get("certificates") and isinstance(row["certificates"], str):
        row["certificates"] = json.loads(row["certificates"])
    return row


def status(db, cfg):
    s = state(db)
    live = bool(s.get("at") and s["at"] >= _ts(_now() - dt.timedelta(seconds=LEASE_SECONDS)))
    on = enabled(cfg)
    names = set(domains(cfg))
    certs = [c for c in s.get("certificates") or [] if names & set(c.get("names") or [])]
    return {
        "enabled": on,
        "running": live and bool(s.get("running")),
        "ports_open": live,
        "url": public_url(cfg) or None,
        "certificates": certs,
        "error": s.get("error") if on else None,
        "log": s.get("log") or [],
        "origin": origin(cfg),
        "process": s.get("owner") if live else None,
    }


def providers():
    """Settings › HTTPS: the DNS providers listed, with their fields."""
    return [
        {"name": n, "label": p["label"], "fields": [{"name": f, "label": lbl, "secret": sec} for f, lbl, sec in p["fields"]]}
        for n, p in PROVIDERS.items()
    ]


# ---------- Caddy ----------
def _platform():
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64", "armv7l": "armv7", "armv6l": "armv6"}.get(machine)
    if system not in ("linux", "darwin") or not arch:
        raise HttpsError(f"install caddy yourself on this system ({system} {machine}) and set LENS_CADDY to it")
    return system, arch


_MODULES: dict[str, tuple[float, set[str]]] = {}


def modules(exe):
    """The modules a caddy has (cached while the file is unchanged)."""
    try:
        mtime = os.path.getmtime(exe)
    except OSError:
        return set()
    hit = _MODULES.get(exe)
    if hit and hit[0] == mtime:
        return hit[1]
    try:
        out = subprocess.run([exe, "list-modules"], capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return set()
    found = {line.strip() for line in out.splitlines() if line.strip() and " " not in line.strip()}
    _MODULES[exe] = (mtime, found)
    return found


def _wanted(cfg):
    t = conf(cfg)
    return f"dns.providers.{t['dns_provider'].strip()}" if (t.get("challenge") == "dns" and t.get("dns_provider")) else None


def binary(cfg):
    """A caddy that has what the settings need: LENS_CADDY, caddy on PATH, or one Lens fetched; None when none does."""
    want = _wanted(cfg)
    bin_dir = pathlib.Path(cfg["data_dir"]) / "bin"
    fetched = bin_dir / (f"caddy-{conf(cfg)['dns_provider'].strip()}" if want else "caddy")
    for exe in (os.environ.get("LENS_CADDY"), shutil.which("caddy"), str(fetched)):
        if exe and os.path.isfile(exe) and os.access(exe, os.X_OK) and (not want or want in modules(exe)):
            return exe
    return None


def _download(url, dest, say=None):
    tmp = dest.with_name(dest.name + ".part")
    if say:
        say(f"downloading {url}")
    try:
        with httpx.stream("GET", url, follow_redirects=True, timeout=300) as r:
            r.raise_for_status()
            with open(tmp, "wb") as f:
                for chunk in r.iter_bytes():
                    f.write(chunk)
    except httpx.HTTPError as e:
        tmp.unlink(missing_ok=True)
        raise HttpsError(f"couldn't download Caddy from {urllib.parse.urlsplit(url).hostname}: {e}") from None
    return tmp


def fetch_binary(cfg, say=None):
    """Download Caddy into data_dir/bin: the release from GitHub (checksum checked), or with the DNS provider's
    plugin, a build from Caddy's download service. Returns its path."""
    system, arch = _platform()
    dest = pathlib.Path(cfg["data_dir"]) / "bin"
    dest.mkdir(parents=True, exist_ok=True)
    want = _wanted(cfg)
    if want:
        prov = conf(cfg)["dns_provider"].strip()
        params = {"os": system, "arch": arch[:3] if arch.startswith("armv") else arch, "p": package(cfg)}
        if arch.startswith("armv"):
            params["arm"] = arch[-1]
        out = dest / f"caddy-{prov}"
        tmp = _download(f"{BUILDS}?{urllib.parse.urlencode(params)}", out, say)
        tmp.chmod(0o755)
        tmp.replace(out)
        _MODULES.pop(str(out), None)
        if want not in modules(str(out)):
            raise HttpsError(f"the Caddy built with {package(cfg)} has no {want} module; check the DNS provider's name")
        return str(out)
    name = f"caddy_{CADDY_VERSION}_{'mac' if system == 'darwin' else system}_{arch}.tar.gz"
    try:
        sums = httpx.get(RELEASES + f"caddy_{CADDY_VERSION}_checksums.txt", follow_redirects=True, timeout=60)
        sums.raise_for_status()
    except httpx.HTTPError as e:
        raise HttpsError(f"couldn't download Caddy's checksums from GitHub: {e}") from None
    expect = next((line.split()[0] for line in sums.text.splitlines() if line.strip().endswith(" " + name)), None)
    if not expect:
        raise HttpsError(f"Caddy {CADDY_VERSION} has no download for {system} {arch}")
    tmp = _download(RELEASES + name, dest / name, say)
    h = hashlib.sha512()
    with open(tmp, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    if h.hexdigest() != expect:
        tmp.unlink(missing_ok=True)
        raise HttpsError("the Caddy download doesn't match its checksum; try again")
    out = dest / "caddy"
    with tarfile.open(tmp) as t:
        member = next((m for m in t.getmembers() if m.name == "caddy" and m.isfile()), None)
        if not member:
            raise HttpsError("the Caddy download has no program in it")
        src = t.extractfile(member)
        assert src is not None
        with src, open(out.with_name("caddy.new"), "wb") as f:
            shutil.copyfileobj(src, f)
    tmp.unlink()
    out.with_name("caddy.new").chmod(0o755)
    out.with_name("caddy.new").replace(out)
    _MODULES.pop(str(out), None)
    return str(out)


# ---------- the runner ----------
def _line(raw):
    """A Caddy log line, shortened for Settings › HTTPS, and what it says: (text, obtained?, problem or None)."""
    try:
        d = json.loads(raw)
    except ValueError:
        return raw[:300], False, None
    if not isinstance(d, dict):
        return raw[:300], False, None
    msg = str(d.get("msg") or "")
    who = ", ".join(d.get("identifiers") or []) or str(d.get("identifier") or d.get("domain") or "")
    err = str(d.get("error") or "")
    text = " ".join(x for x in (str(d.get("level") or "").upper(), msg, who, err) if x)[:300]
    obtained = msg.startswith("certificate obtained successfully") or msg == "certificate renewed successfully"
    problem = f"{msg}{' (' + who + ')' if who else ''}: {err}"[:300] if d.get("level") == "error" and err else None
    return text, obtained, problem


def listens_here():
    """Whether this process has the HTTPS ports (LENS_HTTPS_LISTEN=1)."""
    return (os.environ.get("LENS_HTTPS_LISTEN") or "").strip().lower() in ("1", "true", "yes", "on")


class Runner:
    """Holds the lease and runs Caddy while Settings › HTTPS says to, in a process that has the ports."""

    def __init__(self, db, cfg_fn, name, log=None):
        self.db, self.cfg_fn, self.name, self.log = db, cfg_fn, name, log or (lambda *a: None)
        self.proc: subprocess.Popen | None = None
        self.sig = None
        self.lines: list[str] = []
        self.error = None
        self.fails = 0
        self.next_try = 0.0
        self.certs_at = 0.0
        self.certs: list[dict] = []
        self.lock = threading.Lock()

    def _lease(self):
        now = _now()
        return bool(
            self.db.rows(
                "UPSERT $r MERGE {owner: $me, at: $t} WHERE owner = NONE OR owner = $me OR at < $stale RETURN AFTER",
                r=ROW,
                me=self.name,
                t=_ts(now),
                stale=_ts(now - dt.timedelta(seconds=LEASE_SECONDS)),
            )
        )

    def storage(self, cfg):
        return pathlib.Path(cfg["data_dir"]) / "https"

    def _save(self, cfg=None):
        if cfg is not None and time.monotonic() - self.certs_at > 60:
            self.certs_at = time.monotonic()
            try:
                self.certs = certificates(self.storage(cfg) / "caddy")
            except Exception:  # noqa: BLE001 - a certificate it can't read just isn't listed
                pass
        with self.lock:
            d = {
                "running": self.proc is not None and self.proc.poll() is None,
                "error": self.error,
                "log": json.dumps(self.lines[-LOG_LINES:]),
                "certificates": json.dumps(self.certs),
            }
        self.db.q("UPDATE $r MERGE $d WHERE owner = $me", r=ROW, d=d, me=self.name)

    def stop_process(self):
        p, self.proc = self.proc, None
        if p and p.poll() is None:
            p.terminate()
            try:
                p.wait(10)
            except subprocess.TimeoutExpired:
                p.kill()

    def _read(self, proc):
        assert proc.stdout is not None
        for raw in proc.stdout:
            raw = raw.rstrip()
            if not raw:
                continue
            text, obtained, problem = _line(raw)
            with self.lock:
                self.lines = (self.lines + [text])[-LOG_LINES:]
                if obtained:
                    self.error, self.fails, self.certs_at = None, 0, 0.0
                elif problem:
                    self.error = problem  # Caddy keeps trying; this says why it has no certificate yet
            if obtained or problem:
                self._save()
        code = proc.wait()
        if self.proc is proc:
            with self.lock:
                self.error = f"Caddy stopped (exit {code}); {self.lines[-1] if self.lines else 'no output'}"
            self.fails += 1
            self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]
            self._save()

    def tick(self):
        cfg = self.cfg_fn()
        if not self._lease():
            if self.proc:  # someone else runs it now
                self.stop_process()
            return
        if not enabled(cfg):  # the lease still says a process has the ports, for Settings › HTTPS
            if self.proc:
                self.stop_process()
            self.error, self.sig = None, None
            self._save()
            return
        root = self.storage(cfg)
        try:
            check(cfg)
            conf_json = json.dumps(caddy_config(cfg, root / "caddy"), sort_keys=True)
        except (ValueError, KeyError) as e:
            self.stop_process()
            self.error = str(e)
            self._save()
            return
        sig = json.dumps([conf_json, _wanted(cfg), package(cfg)])
        if sig != self.sig:
            self.stop_process()
            self.sig, self.fails, self.next_try, self.error, self.lines = sig, 0, 0.0, None, []
        if self.proc and self.proc.poll() is None:
            self._save(cfg)
            return
        if time.monotonic() < self.next_try:
            self._save(cfg)
            return
        try:
            exe = binary(cfg) or fetch_binary(cfg, say=lambda m: self.lines.append(m))
            root.mkdir(parents=True, exist_ok=True)
            os.chmod(root, 0o700)  # the configuration holds the DNS provider's credentials
            path = root / "caddy.json"
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                f.write(conf_json)
        except (HttpsError, OSError) as e:
            self.error = str(e)
            self.fails += 1
            self.next_try = time.monotonic() + RETRY[min(self.fails - 1, len(RETRY) - 1)]
            self._save(cfg)
            return
        env = {k: v for k, v in os.environ.items() if not k.startswith("CADDY")}
        env["HOME"] = str(root)  # Caddy keeps its own files under $HOME otherwise
        env["XDG_DATA_HOME"], env["XDG_CONFIG_HOME"] = str(root / "data"), str(root / "config")
        self.proc = subprocess.Popen(
            [exe, "run", "--config", str(path)], env=env, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
        threading.Thread(target=self._read, args=(self.proc,), daemon=True, name="caddy-log").start()
        self.log("https: started Caddy for %s", ", ".join(domains(cfg)))
        self._save(cfg)

    def loop(self, stop):
        while not stop.is_set():
            try:
                self.tick()
            except Exception as e:  # noqa: BLE001 - keep the runner alive; the error shows in Settings
                self.error = f"{type(e).__name__}: {e}"
                try:
                    self._save()
                except Exception:  # noqa: BLE001
                    pass
            stop.wait(TICK_SECONDS)
        self.stop_process()
        self.db.q("UPDATE $r SET owner = NONE, running = false WHERE owner = $me", r=ROW, me=self.name)


def start(db, cfg_fn, stop, name, log=None):
    """Run the HTTPS runner in a thread: idle unless this process has the ports and HTTPS is on."""
    if not listens_here():
        return None
    r = Runner(db, cfg_fn, name, log)
    threading.Thread(target=r.loop, args=(stop,), daemon=True, name="https").start()
    return r
