"""A software passkey authenticator for tests: makes passkeys and signs in with them, as a browser and a phone would."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import struct

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url, encode_cbor

UP, UV, AT = 0x01, 0x04, 0x40


class Authenticator:
    """Keeps its passkeys: {credential id: (key, rp id, user handle, counter)}."""

    def __init__(self, counter: bool = True, prf: bool = True):
        self.keys: dict[str, dict] = {}
        self.counter, self.prf = counter, prf

    def _client_data(self, kind, challenge, origin):
        return json.dumps({"type": kind, "challenge": challenge, "origin": origin, "crossOrigin": False}).encode()

    def create(self, options: dict, origin: str, rp_id: str | None = None, uv: bool = True) -> dict:
        rp_id = rp_id or options["rp"]["id"]
        key = ec.generate_private_key(ec.SECP256R1())
        nums = key.public_key().public_numbers()
        cose = {1: 2, 3: -7, -1: 1, -2: nums.x.to_bytes(32, "big"), -3: nums.y.to_bytes(32, "big")}
        cred_id = os.urandom(16)
        attested = bytes(16) + struct.pack(">H", len(cred_id)) + cred_id + encode_cbor(cose)
        flags = UP | AT | (UV if uv else 0)
        auth_data = hashlib.sha256(rp_id.encode()).digest() + bytes([flags]) + struct.pack(">I", 0) + attested
        cid = bytes_to_base64url(cred_id)
        self.keys[cid] = {"key": key, "rp_id": rp_id, "user": options["user"]["id"], "count": 0, "secret": os.urandom(32)}
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(self._client_data("webauthn.create", options["challenge"], origin)),
                "attestationObject": bytes_to_base64url(encode_cbor({"fmt": "none", "attStmt": {}, "authData": auth_data})),
                "transports": ["internal", "hybrid"],
            },
            "clientExtensionResults": {},
        }

    def get(self, options: dict, origin: str, cred_id: str | None = None, uv: bool = True) -> dict:
        cid = cred_id or next(k for k, v in self.keys.items() if v["rp_id"] == options["rpId"])
        k = self.keys[cid]
        if self.counter:
            k["count"] += 1
        auth_data = hashlib.sha256(k["rp_id"].encode()).digest() + bytes([UP | (UV if uv else 0)]) + struct.pack(">I", k["count"])
        client_data = self._client_data("webauthn.get", options["challenge"], origin)
        sig = k["key"].sign(auth_data + hashlib.sha256(client_data).digest(), ec.ECDSA(hashes.SHA256()))
        return {
            "id": cid,
            "rawId": cid,
            "type": "public-key",
            "response": {
                "clientDataJSON": bytes_to_base64url(client_data),
                "authenticatorData": bytes_to_base64url(auth_data),
                "signature": bytes_to_base64url(sig),
                "userHandle": k["user"],
            },
            "clientExtensionResults": self._prf(k, options),
        }

    def _prf(self, k, options):
        """The PRF extension's result for the salt asked for, as a browser's toJSON() gives it: secret to the passkey,
        the same every time for the same salt."""
        first = ((options.get("extensions") or {}).get("prf") or {}).get("eval", {}).get("first")
        if not (self.prf and first):
            return {}
        out = hmac.new(k["secret"], base64url_to_bytes(first), "sha256").digest()
        return {"prf": {"results": {"first": bytes_to_base64url(out)}}}


def user_handle(credential: dict) -> bytes:
    return base64url_to_bytes(credential["response"]["userHandle"])
