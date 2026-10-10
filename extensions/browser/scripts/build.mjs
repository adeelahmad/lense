// Builds the extension for each browser in targets/: dist/<target>/ is what the browser loads (or the store gets).
// A target is targets/<name>.json, merged over src/manifest.json (null removes a key). `--target chrome` builds one;
// `--check` builds and then checks every file the manifests name exists.
import { build } from "esbuild";
import { cpSync, existsSync, mkdirSync, readdirSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const args = process.argv.slice(2);
const only = args.includes("--target") ? args[args.indexOf("--target") + 1] : null;
const pkg = JSON.parse(readFileSync(join(root, "package.json"), "utf8"));
// a release builds with its own version (EXT_VERSION=v0.7.0); browsers take only dotted numbers
const version = (process.env.EXT_VERSION || pkg.version).replace(/^v/, "").replace(/[^0-9.].*$/, "");

function merge(base, over) {
  const out = { ...base };
  for (const [k, v] of Object.entries(over)) {
    if (v === null) delete out[k];
    else if (v && typeof v === "object" && !Array.isArray(v) && typeof out[k] === "object" && !Array.isArray(out[k])) out[k] = merge(out[k], v);
    else out[k] = v;
  }
  return out;
}

function referenced(m) {
  const files = new Set([...Object.values(m.icons ?? {}), ...Object.values(m.action?.default_icon ?? {})]);
  for (const f of [m.action?.default_popup, m.options_ui?.page, m.background?.service_worker, ...(m.background?.scripts ?? [])]) if (f) files.add(f);
  return [...files];
}

const targets = readdirSync(join(root, "targets"))
  .filter((f) => f.endsWith(".json"))
  .map((f) => f.slice(0, -5))
  .filter((t) => !only || t === only);
if (!targets.length) throw new Error(`no target ${only} in targets/`);

const base = JSON.parse(readFileSync(join(root, "src/manifest.json"), "utf8"));
for (const target of targets) {
  const out = join(root, "dist", target);
  rmSync(out, { recursive: true, force: true });
  mkdirSync(out, { recursive: true });
  await build({
    entryPoints: {
      background: join(root, "src/background.js"),
      content: join(root, "src/content.js"),
      popup: join(root, "src/ui/popup.js"),
      options: join(root, "src/ui/options.js"),
    },
    outdir: out,
    bundle: true,
    format: "iife",
    platform: "browser",
    target: ["chrome116", "firefox115", "safari16"],
    legalComments: "linked",
    define: { "process.env.NODE_ENV": '"production"' },
    logLevel: "warning",
  });
  for (const f of ["popup.html", "options.html", "style.css"]) cpSync(join(root, "src/ui", f), join(out, f));
  cpSync(join(root, "src/icons"), join(out, "icons"), { recursive: true });
  const manifest = merge({ ...base, version }, JSON.parse(readFileSync(join(root, "targets", `${target}.json`), "utf8")));
  writeFileSync(join(out, "manifest.json"), `${JSON.stringify(manifest, null, 2)}\n`);
  if (args.includes("--check")) {
    const missing = referenced(manifest).filter((f) => !existsSync(join(out, f)));
    if (missing.length) throw new Error(`${target}: the manifest names files the build lacks: ${missing.join(", ")}`);
  }
  console.log(`built dist/${target} (${manifest.name} ${manifest.version})`);
}
