// Release helpers for copies of the clipper installed from a GitHub release instead of a store:
//
//   node scripts/release.mjs id <key.pem>                       the Chromium extension ID a packing key gives
//   node scripts/release.mjs chrome-updates <target> <id> <crx-url> <out.xml>
//   node scripts/release.mjs firefox-updates <target> <xpi-url> <out.json>
//
// The version is read from dist/<target>-selfhosted/manifest.json, so run the build (with EXT_UPDATE_BASE) first.
import { createHash, createPublicKey } from "node:crypto";
import { readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const manifestOf = (target) => JSON.parse(readFileSync(join(root, "dist", `${target}-selfhosted`, "manifest.json"), "utf8"));
const xml = (s) => String(s).replace(/&/g, "&amp;").replace(/'/g, "&apos;").replace(/</g, "&lt;");

/** Chromium's extension ID: the first 128 bits of the SHA-256 of the public key, in the letters a to p. */
export function extensionId(pem) {
  const der = createPublicKey(pem).export({ type: "spki", format: "der" });
  return [...createHash("sha256").update(der).digest("hex").slice(0, 32)].map((c) => String.fromCharCode(97 + parseInt(c, 16))).join("");
}

/** The update manifest Chrome and Edge poll (the gupdate format). */
export function chromeUpdates(id, version, crxUrl) {
  return `<?xml version='1.0' encoding='UTF-8'?>
<gupdate xmlns='http://www.google.com/update2/response' protocol='2.0'>
  <app appid='${xml(id)}'>
    <updatecheck codebase='${xml(crxUrl)}' version='${xml(version)}' />
  </app>
</gupdate>
`;
}

/** The update manifest Firefox polls. */
export function firefoxUpdates(addonId, version, xpiUrl) {
  return `${JSON.stringify({ addons: { [addonId]: { updates: [{ version, update_link: xpiUrl }] } } }, null, 2)}\n`;
}

const [cmd, ...a] = process.argv.slice(2);
if (cmd === "id") console.log(extensionId(readFileSync(a[0], "utf8")));
else if (cmd === "chrome-updates") {
  const [target, id, url, out] = a;
  writeFileSync(out, chromeUpdates(id, manifestOf(target).version, url));
} else if (cmd === "firefox-updates") {
  const [target, url, out] = a;
  const m = manifestOf(target);
  const addonId = m.browser_specific_settings?.gecko?.id;
  if (!addonId) throw new Error(`${target} has no browser_specific_settings.gecko.id`);
  writeFileSync(out, firefoxUpdates(addonId, m.version, url));
} else if (cmd) throw new Error(`unknown command ${cmd}`);
