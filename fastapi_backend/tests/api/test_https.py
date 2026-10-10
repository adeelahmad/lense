"""HTTPS with certificates Lens gets and renews itself: set up in Settings › HTTPS, Caddy runs in the one process that
has the HTTPS ports, the certificates show in the app, and passkeys and email links use the https:// address. Caddy
is faked: it reads the configuration Lens writes, says it got a certificate and keeps one where Caddy would."""

from __future__ import annotations

import datetime as dt
import json
import pathlib
import sys
import textwrap
import time

import pytest

from app import email
from app.domain import https, settings
from tests.helpers import login, make_user

FAKE = textwrap.dedent(
    """\
    #!{python}
    import datetime as dt, json, os, pathlib, sys, time
    args = sys.argv[1:]
    if args[0] == "list-modules":
        print("http.handlers.reverse_proxy")
        print("tls.issuance.acme")
        for m in os.environ.get("FAKE_MODULES", "").split(","):
            if m:
                print(m)
        sys.exit(0)
    conf = json.loads(pathlib.Path(args[args.index("--config") + 1]).read_text())
    pathlib.Path(os.environ["FAKE_SEEN"]).write_text(json.dumps(conf))
    policy = conf["apps"]["tls"]["automation"]["policies"][0]
    for name in policy["subjects"]:
        if "broken" in name:
            print(json.dumps({{"level": "error", "msg": "could not get certificate from issuer", "identifier": name,
                              "error": "HTTP 400 urn:ietf:params:acme:error:dns - no valid A records found"}}), flush=True)
            continue
        from cryptography import x509
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import ec
        key = ec.generate_private_key(ec.SECP256R1())
        subject = x509.Name([x509.NameAttribute(x509.NameOID.COMMON_NAME, name)])
        issuer = x509.Name([x509.NameAttribute(x509.NameOID.ORGANIZATION_NAME, "Let's Encrypt")])
        now = dt.datetime.now(dt.timezone.utc)
        cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(issuer).public_key(key.public_key())
                .serial_number(1).not_valid_before(now).not_valid_after(now + dt.timedelta(days=90))
                .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), False).sign(key, hashes.SHA256()))
        d = pathlib.Path(conf["storage"]["root"]) / "certificates" / "acme-v02.api.letsencrypt.org-directory" / name.replace("*", "wildcard_")
        d.mkdir(parents=True, exist_ok=True)
        (d / (name.replace("*", "wildcard_") + ".crt")).write_bytes(cert.public_bytes(serialization.Encoding.PEM))
        print(json.dumps({{"level": "info", "msg": "certificate obtained successfully", "identifier": name}}), flush=True)
    while True:
        time.sleep(0.2)
    """
)


@pytest.fixture
def caddy(tmp_path, monkeypatch):
    """A fake caddy on PATH, in a process that has the HTTPS ports."""
    d = tmp_path / "bin"
    d.mkdir()
    exe = d / "caddy"
    exe.write_text(FAKE.format(python=sys.executable))
    exe.chmod(0o755)
    seen = tmp_path / "seen.json"
    monkeypatch.setenv("PATH", f"{d}:{__import__('os').environ['PATH']}")
    monkeypatch.setenv("FAKE_SEEN", str(seen))
    monkeypatch.setenv("LENS_HTTPS_LISTEN", "1")
    monkeypatch.delenv("LENS_CADDY", raising=False)
    https._MODULES.clear()
    return seen


def wait(fn, timeout=15):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.05)
    raise AssertionError("timed out")


def runner(db, cfg, name="worker-1"):
    return https.Runner(db, lambda: settings.Settings(db, cfg).current(), name)


def test_https_is_set_up_in_the_app(client, db, cfg):
    make_user(db, "root@x.io", "root password 1", admin=True)
    a = login(client, "root@x.io", "root password 1")
    s = client.get("/api/v1/settings/https/status", headers=a).json()
    assert not s["enabled"] and not s["running"] and not s["ports_open"] and s["url"] is None
    providers = client.get("/api/v1/settings/https/providers", headers=a).json()
    cloudflare = next(p for p in providers if p["name"] == "cloudflare")
    assert cloudflare["fields"] == [{"name": "api_token", "label": "API token (Zone.Zone read, Zone.DNS edit)", "secret": True}]
    for bad in (
        {"enabled": True},  # no domain
        {"domains": ["not a domain"]},
        {"domains": ["*.example.com"]},  # a wildcard needs the DNS challenge
        {"ca": "someone"},
        {"ca": "custom", "ca_url": "http://ca.example.com/dir"},
        {"challenge": "dns", "dns_provider": "Cloud Flare"},
        {"challenge": "dns", "dns_provider": "cloudflare", "dns_package": "example.com/x"},
        {"dns_credentials": "api_token=x"},
        {"dns_credentials": '{"api token": "x"}'},
        {"eab_key_id": "kid"},  # without its HMAC key
        {"https_port": 8080},  # the same as the HTTP port
        {"origin": "frontend:3000"},
        {"email": "nobody"},
    ):
        r = client.put("/api/v1/settings/https", headers=a, json=bad)
        assert r.status_code == 400, bad
    r = client.put(
        "/api/v1/settings/https",
        headers=a,
        json={
            "enabled": True,
            "domains": ["Lens.Example.com", "*.example.com"],
            "email": "admin@example.com",
            "challenge": "dns",
            "dns_provider": "cloudflare",
            "dns_credentials": json.dumps({"api_token": "cf-secret"}),
        },
    )
    assert r.status_code == 200, r.text
    assert "cf-secret" not in client.get("/api/v1/settings", headers=a).text  # the credentials are a secret
    current = settings.Settings(db, cfg).current()
    assert https.credentials(current) == {"api_token": "cf-secret"} and https.hosts(current) == ["lens.example.com"]
    s = client.get("/api/v1/settings/https/status", headers=a).json()
    assert s["enabled"] and s["url"] == "https://lens.example.com" and not s["ports_open"]  # nothing has the ports yet
    # a change that keeps the saved credentials (the mask echoed back)
    r = client.put("/api/v1/settings/https", headers=a, json={"email": "", "dns_credentials": {"secret": True}})
    assert r.status_code == 200, r.text
    assert https.credentials(settings.Settings(db, cfg).current()) == {"api_token": "cf-secret"}
    assert client.put("/api/v1/settings/https", headers=a, json={"enabled": False}).status_code == 200
    viewer = make_user(db, "v@x.io", "viewer password 1")
    assert viewer
    assert client.get("/api/v1/settings/https/status", headers=login(client, "v@x.io", "viewer password 1")).status_code == 403


def test_caddys_configuration_follows_the_settings(cfg, monkeypatch):
    monkeypatch.setenv("LENS_HTTPS_PORT", "8443")
    cfg["https"] = {
        **cfg["https"],
        "enabled": True,
        "domains": ["lens.example.com"],
        "email": "a@example.com",
        "hsts": True,
        "origin": "http://frontend:3000",
    }
    c = https.caddy_config(cfg, "/data/https/caddy")
    assert c["admin"] == {"disabled": True} and c["storage"]["root"] == "/data/https/caddy"
    lens = c["apps"]["http"]["servers"]["lens"]
    assert lens["listen"] == [":8443"] and lens["routes"][0]["match"] == [{"host": ["lens.example.com"]}]
    assert lens["routes"][0]["handle"][-1] == {"handler": "reverse_proxy", "upstreams": [{"dial": "frontend:3000"}]}
    assert lens["routes"][0]["handle"][0]["response"]["set"]["Strict-Transport-Security"]
    # http:// goes to https:// at the port people use (published by install.sh), not the one in the container
    redirect = c["apps"]["http"]["servers"]["redirect"]
    assert redirect["listen"] == [":8080"]
    assert redirect["routes"][0]["handle"][0]["headers"]["Location"] == ["https://{http.request.host}:8443{http.request.uri}"]
    issuers = c["apps"]["tls"]["automation"]["policies"][0]["issuers"]
    assert [i["ca"] for i in issuers] == [https.LETSENCRYPT, https.ZEROSSL]  # both: ZeroSSL when Let's Encrypt can't
    assert all(i["email"] == "a@example.com" and "challenges" not in i for i in issuers)

    cfg["https"].update(ca="letsencrypt", staging=True, challenge="tls-alpn", redirect_http=False)
    c = https.caddy_config(cfg, "/s")
    assert "redirect" not in c["apps"]["http"]["servers"]
    [le] = c["apps"]["tls"]["automation"]["policies"][0]["issuers"]
    assert le["ca"] == https.LETSENCRYPT_STAGING and le["challenges"] == {"http": {"disabled": True}}

    cfg["https"].update(
        ca="zerossl",
        challenge="dns",
        domains=["*.example.com"],
        dns_provider="route53",
        dns_credentials=json.dumps({"access_key_id": "AK", "secret_access_key": "SK"}),
        dns_resolvers=["1.1.1.1"],
        propagation="2m",
        eab_key_id="kid",
        eab_hmac_key="mac",
    )
    https.check(cfg)
    [z] = https.caddy_config(cfg, "/s")["apps"]["tls"]["automation"]["policies"][0]["issuers"]
    assert z["ca"] == https.ZEROSSL and z["external_account"] == {"key_id": "kid", "mac_key": "mac"}
    assert z["challenges"] == {
        "http": {"disabled": True},
        "tls-alpn": {"disabled": True},
        "dns": {
            "provider": {"name": "route53", "access_key_id": "AK", "secret_access_key": "SK"},
            "resolvers": ["1.1.1.1"],
            "propagation_timeout": "2m",
        },
    }
    assert https.package(cfg) == "github.com/caddy-dns/route53"


def test_caddy_runs_in_one_process_with_the_ports_and_shows_its_certificates(db, cfg, caddy):
    cfg["https"] = {**cfg["https"], "enabled": True, "domains": ["lens.example.com"], "origin": "http://frontend:3000"}
    one, two = runner(db, cfg, "worker-1"), runner(db, cfg, "worker-2")
    try:
        one.tick()
        two.tick()  # the lease is taken: it doesn't run a second Caddy
        assert one.proc and not two.proc
        wait(lambda: any("certificate obtained" in line for line in https.status(db, cfg)["log"]))
        one.certs_at = 0.0
        one.tick()
        s = https.status(db, cfg)
        assert s["running"] and s["ports_open"] and s["process"] == "worker-1" and s["error"] is None
        [cert] = s["certificates"]
        assert cert["names"] == ["lens.example.com"] and cert["issuer"] == "Let's Encrypt"
        assert cert["expires"] > (dt.datetime.now(dt.UTC) + dt.timedelta(days=80)).isoformat()
        written = pathlib.Path(cfg["data_dir"]) / "https" / "caddy.json"
        assert oct(written.stat().st_mode & 0o777) == "0o600"  # it may hold the DNS provider's credentials
        assert json.loads(caddy.read_text())["apps"]["tls"]["automation"]["policies"][0]["subjects"] == ["lens.example.com"]
        # turned off: Caddy stops, and the process still says it has the ports
        cfg["https"]["enabled"] = False
        one.tick()
        assert one.proc is None
        s = https.status(db, cfg)
        assert not s["running"] and s["ports_open"]
    finally:
        one.stop_process()
        two.stop_process()


def test_a_domain_that_cant_be_verified_says_why(db, cfg, caddy):
    cfg["https"] = {**cfg["https"], "enabled": True, "domains": ["broken.example.com"], "origin": "http://frontend:3000"}
    r = runner(db, cfg)
    try:
        r.tick()
        s = wait(lambda: (st := https.status(db, cfg))["error"] and st)
        assert "no valid A records" in s["error"] and "broken.example.com" in s["error"] and not s["certificates"]
        cfg["https"]["domains"] = ["lens.example.com"]
        r.tick()  # a changed setting starts Caddy again straight away, and the error goes once it has the certificate
        wait(lambda: https.status(db, cfg)["error"] is None)
    finally:
        r.stop_process()


def test_a_dns_provider_needs_a_caddy_with_its_plugin(db, cfg, caddy, monkeypatch):
    cfg["https"] = {
        **cfg["https"],
        "enabled": True,
        "domains": ["*.example.com"],
        "challenge": "dns",
        "dns_provider": "cloudflare",
        "origin": "http://frontend:3000",
    }
    fetched = []

    def fetch(c, say=None):
        fetched.append(https.package(c))
        raise https.HttpsError("couldn't download Caddy from caddyserver.com: offline")

    monkeypatch.setattr(https, "fetch_binary", fetch)
    assert https.binary(cfg) is None  # the caddy on PATH has no cloudflare plugin
    r = runner(db, cfg)
    try:
        r.tick()
        assert fetched == ["github.com/caddy-dns/cloudflare"] and r.proc is None
        assert "offline" in https.status(db, cfg)["error"]
        monkeypatch.setenv("FAKE_MODULES", "dns.providers.cloudflare")
        https._MODULES.clear()
        assert https.binary(cfg)  # one that has it is used as it is
    finally:
        r.stop_process()


def test_nothing_runs_where_the_ports_arent(db, cfg, monkeypatch):
    monkeypatch.delenv("LENS_HTTPS_LISTEN", raising=False)
    assert https.start(db, lambda: cfg, None, "api-1") is None


def test_passkeys_and_email_links_use_the_https_address(app, client, db, cfg, monkeypatch):
    cfg["https"] = {**cfg["https"], "enabled": True, "domains": ["lens.example.com"]}
    # Caddy passes the browser's host on; the browser is on https://lens.example.com
    o = client.post("/api/v1/auth/passkey/options", headers={"x-forwarded-host": "lens.example.com", "x-forwarded-proto": "http"})
    assert o.status_code == 200 and o.json()["options"]["rpId"] == "lens.example.com"
    assert email.app_url(cfg) == "https://lens.example.com"
    monkeypatch.setenv("LENS_HTTPS_PORT", "8443")
    o = client.post("/api/v1/auth/passkey/options", headers={"x-forwarded-host": "lens.example.com:8443", "x-forwarded-proto": "https"})
    assert o.json()["options"]["rpId"] == "lens.example.com"
    assert email.app_url(cfg) == "https://lens.example.com:8443"
    cfg["https"] = {**cfg["https"], "enabled": False}
    db.next_id("settings")  # the app's settings are read again
    o = client.post("/api/v1/auth/passkey/options", headers={"x-forwarded-host": "lens.example.com", "x-forwarded-proto": "https"})
    assert o.json()["options"]["rpId"] == "localhost"
