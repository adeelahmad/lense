#!/usr/bin/env python3
"""A stand-in for `nebula-cert` (tests/api/test_mesh.py): the subcommands and flags domain/mesh.py uses, writing
certificates and keys in Nebula's PEM framing with JSON inside, so `print -json` gives back what `ca` and `sign` put
in. Public keys are in Nebula's own format (a base64 line between X25519 PUBLIC KEY lines), as mesh.PUBLIC_KEY_RX
expects. It checks what the real tool checks for these flows: a CA key that isn't the CA certificate's, a public key
that isn't one, a duration that isn't hours. Not a cryptographic implementation.
"""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import re
import secrets
import sys

CERT = "NEBULA CERTIFICATE V2"
CA_KEY = "NEBULA ED25519 PRIVATE KEY"
KEY = "NEBULA X25519 PRIVATE KEY"
PUB = "NEBULA X25519 PUBLIC KEY"


def wrap(kind, text):
    b64 = base64.b64encode(text.encode()).decode()
    body = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
    return f"-----BEGIN {kind}-----\n{body}\n-----END {kind}-----\n"


def unwrap(pem, kind):
    m = re.search(rf"-----BEGIN {kind}-----\n(.*?)-----END {kind}-----", pem, re.S)
    if not m:
        raise SystemExit(f"error while parsing: no {kind} block")
    return base64.b64decode(m.group(1).replace("\n", "")).decode()


def read(path):
    return sys.stdin.read() if path == "-" else open(path).read()


def write(path, text):
    if path == "-":
        sys.stdout.write(text)
    else:
        open(path, "w").write(text)


def flags(args):
    out, i = {}, 0
    while i < len(args):
        a = args[i]
        if not a.startswith("-"):
            raise SystemExit(f"flag provided but not defined: {a}")
        out[a.lstrip("-")] = args[i + 1] if i + 1 < len(args) else ""
        i += 2
    return out


def hours(value):
    m = re.fullmatch(r"(\d+)h(0m0s)?", value or "")
    if not m:
        raise SystemExit(f'invalid value "{value}" for flag -duration: time: unknown unit')
    return int(m.group(1))


def now():
    return dt.datetime.now(dt.UTC).replace(microsecond=0)


def stamp(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def cert(name, networks, groups, is_ca, issuer, duration, public_key):
    start = now()
    details = {
        "groups": groups or None,
        "isCa": is_ca,
        "issuer": issuer,
        "name": name,
        "networks": networks,
        "notAfter": stamp(start + dt.timedelta(hours=duration)),
        "notBefore": stamp(start),
        "unsafeNetworks": None,
    }
    body = json.dumps(details, sort_keys=True) + public_key + secrets.token_hex(8)
    return {
        "curve": "CURVE25519",
        "details": details,
        "fingerprint": hashlib.sha256(body.encode()).hexdigest(),
        "publicKey": public_key,
        "signature": secrets.token_hex(64),
        "version": 2,
    }


def main(argv):
    if not argv:
        raise SystemExit("Usage of nebula-cert <global flags> <mode>")
    mode, f = argv[0], flags(argv[1:])
    if mode == "ca":
        if not f.get("name"):
            raise SystemExit("-name is required")
        key_id = secrets.token_hex(16)
        c = cert(
            f["name"],
            [n for n in (f.get("networks") or "").split(",") if n],
            [],
            True,
            "",
            hours(f.get("duration") or "8760h"),
            secrets.token_hex(32),
        )
        c["x_key"] = key_id
        write(f.get("out-crt") or "ca.crt", wrap(CERT, json.dumps(c)))
        write(f.get("out-key") or "ca.key", wrap(CA_KEY, json.dumps({"id": key_id})))
        return
    if mode == "keygen":
        write(f["out-key"], wrap(KEY, json.dumps({"id": secrets.token_hex(16)})))
        write(f["out-pub"], f"-----BEGIN {PUB}-----\n{base64.b64encode(secrets.token_bytes(32)).decode()}\n-----END {PUB}-----\n")
        return
    if mode == "sign":
        ca = json.loads(unwrap(read(f.get("ca-crt") or "ca.crt"), CERT))
        key = json.loads(unwrap(read(f.get("ca-key") or "ca.key"), CA_KEY))
        if key["id"] != ca.get("x_key"):
            raise SystemExit("error while signing: refusing to sign, root certificate does not match private key")
        try:
            pub = re.search(rf"-----BEGIN {PUB}-----\n([A-Za-z0-9+/=]+)\n-----END {PUB}-----", read(f["in-pub"])).group(1)
            raw = base64.b64decode(pub)
            if len(raw) != 32:
                raise ValueError
        except (AttributeError, ValueError, KeyError):
            raise SystemExit("error while reading in-pub: bytes not the correct length for a public key") from None
        if not f.get("name"):
            raise SystemExit("-name is required")
        if not f.get("networks"):
            raise SystemExit("-networks is required")
        c = cert(
            f["name"],
            f["networks"].split(","),
            [g for g in (f.get("groups") or "").split(",") if g],
            False,
            ca["fingerprint"],
            hours(f.get("duration") or "8759h"),
            raw.hex(),
        )
        write(f.get("out-crt") or f"{f['name']}.crt", wrap(CERT, json.dumps(c)))
        return
    if mode == "print":
        c = json.loads(unwrap(read(f.get("path") or "-"), CERT))
        c.pop("x_key", None)
        print(json.dumps([c]) if "json" in f else json.dumps(c, indent=2))
        return
    raise SystemExit(f"unknown mode: {mode}")


if __name__ == "__main__":
    main(sys.argv[1:])
