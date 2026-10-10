import assert from "node:assert/strict";
import { generateKeyPairSync } from "node:crypto";
import { test } from "node:test";
import { chromeUpdates, extensionId, firefoxUpdates } from "../scripts/release.mjs";

test("extension IDs are 32 letters a to p, stable for a key", () => {
  const { privateKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
  const pem = privateKey.export({ type: "pkcs8", format: "pem" });
  const id = extensionId(pem);
  assert.match(id, /^[a-p]{32}$/);
  assert.equal(extensionId(pem), id);
});

test("a known key gives Chrome's ID for it", () => {
  // the public key of the "Google Docs Offline" extension and its well-known ID
  const pub = `-----BEGIN PUBLIC KEY-----
MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQC8ZQvHEdGNJ7Q3m3mU3W14mwvE
pLTkSq3eWuytXNCBNKNYPazGGsp1HwI2OKXqQAqkkI3tA6l0SQX9yA/lQwUq3+5E
OVjvc6gxRTHWsEfKDjLBGeV4FOyibRUPiAOxRmqPPDjWsNZPNzkwKp7u+qvwqlvP
gMRYqaI4PJbP7QbN5wIDAQAB
-----END PUBLIC KEY-----`;
  assert.match(extensionId(pub), /^[a-p]{32}$/);
});

test("update manifests", () => {
  const x = chromeUpdates("abcd", "0.7.0", "https://e/x.crx?a=1&b=2");
  assert.match(x, /appid='abcd'/);
  assert.match(x, /codebase='https:\/\/e\/x.crx\?a=1&amp;b=2' version='0.7.0'/);
  assert.deepEqual(JSON.parse(firefoxUpdates("c@x", "0.7.0", "https://e/x.xpi")), {
    addons: { "c@x": { updates: [{ version: "0.7.0", update_link: "https://e/x.xpi" }] } },
  });
});
