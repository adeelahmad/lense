// The clicks survey: drives a fresh Lens through setup and its main features in Chromium and counts the clicks,
// keystrokes and passkey prompts each takes, per feature and per page. ci/clicks/run.sh starts the fresh Lens; see
// README.md for the rules and how to compare with the baseline.
//
//   node survey.mjs [--base http://localhost:3000] [--out results] [--compare baseline.json] [--only id,id]
//                   [--no-installer] [--fail-on-regression]
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium } from "playwright";

import { startFakeModel } from "./fake-model.mjs";
import { JOURNEYS } from "./journeys.mjs";
import { Meter, pageKey } from "./meter.mjs";
import { compare, markdown } from "./report.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");

function parse(argv) {
  const a = { base: "http://localhost:3000", out: path.join(here, "results"), installer: true, failOnRegression: false };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--base") a.base = argv[++i];
    else if (k === "--out") a.out = argv[++i];
    else if (k === "--compare") a.compare = argv[++i];
    else if (k === "--only") a.only = new Set(argv[++i].split(","));
    else if (k === "--no-installer") a.installer = false;
    else if (k === "--fail-on-regression") a.failOnRegression = true;
    else if (k === "--headed") a.headed = true;
    else if (k === "--state") a.state = argv[++i];
    else throw new Error(`unknown argument ${k}`);
  }
  return a;
}

async function startInstaller(base) {
  const dir = process.env.LENS_SURVEY_DIR || fs.mkdtempSync(path.join(os.tmpdir(), "lens-survey-"));
  const proc = spawn(
    "python3",
    [path.join(repo, "installer/lens_installer.py"), "--no-browser", "--port", "3990", "--dir", dir,
      "--script", path.join(here, "fake-install.sh")],
    {
      env: {
        ...process.env,
        LENS_INSTALLER_TOKEN: "survey-token",
        LENS_SURVEY_SETUP_URL: `${base}/setup?code=${process.env.LENS_SETUP_CODE || "survey-code"}`,
      },
      stdio: "ignore",
    },
  );
  for (let i = 0; i < 60; i++) {
    try {
      await fetch("http://127.0.0.1:3990/");
      return proc;
    } catch {
      await new Promise((r) => setTimeout(r, 500));
    }
  }
  proc.kill();
  throw new Error("the web installer didn't start");
}

/** For each page in the primary navigation (and its tabs and sections): clicks from Home, and its controls. */
async function surveyPages(page, base) {
  const pages = [];
  const controls = () =>
    page.evaluate(() => {
      const main = document.querySelector("main") || document.body;
      const seen = [...main.querySelectorAll("a[href], button, input, select, textarea, [role=tab], [role=switch], [role=radio]")];
      // the page's own controls: not the settings sections or tabs that lead to other pages
      return seen.filter((el) => el.offsetParent !== null && !el.closest("[hidden], nav")).length;
    });
  const settle = () => page.waitForTimeout(1200);
  await page.goto(base + "/", { waitUntil: "domcontentloaded" });
  await settle();
  const nav = await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link")
    .evaluateAll((els) => els.map((e) => ({ label: e.textContent.trim(), href: e.getAttribute("href") })));
  const items = nav.filter((n, i) => n.label && n.label !== "Lens" && !n.label.startsWith("Notes ·") && nav.findIndex((m) => m.href === n.href && m.label === n.label) === i);
  for (const item of items) {
    await page.goto(base + "/", { waitUntil: "domcontentloaded" });
    await settle();
    await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: item.label, exact: true }).first().click();
    await page.waitForLoadState("domcontentloaded");
    await settle();
    const key = pageKey(page.url());
    pages.push({ page: key, label: item.label, reach: 1, controls: await controls() });
    // a second level: settings sections, and tabs that are links (Admin, Sensors, Pipelines)
    const sub = await page
      .locator("main")
      .locator('nav[aria-label="Settings sections"] a, [role=tablist] a[role=tab]')
      .evaluateAll((els) =>
        els.map((e) => ({ label: e.innerText.replace(/\s+/g, " ").replace(/ \d+$/, "").trim(), href: e.getAttribute("href") })),
      );
    for (const s of sub) {
      if (!s.href || pages.some((p) => p.page === pageKey(base + s.href))) continue;
      await page.goto(base + s.href, { waitUntil: "domcontentloaded" });
      await settle();
      pages.push({ page: pageKey(page.url()), label: `${item.label} › ${s.label}`, reach: 2, controls: await controls() });
    }
  }
  return pages;
}

async function main() {
  const a = parse(process.argv.slice(2));
  const model = await startFakeModel(Number(process.env.LENS_SURVEY_MODEL_PORT || 11434));
  const installer = a.installer ? await startInstaller(a.base) : null;
  const browser = await chromium.launch({ headless: !a.headed });
  // --state: keep the sign-in between runs while writing journeys (not for measuring: a run starts signed out)
  const state = a.state && fs.existsSync(a.state) ? a.state : undefined;
  const context = await browser.newContext({ viewport: { width: 1400, height: 900 }, locale: "en-US", storageState: state });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  // A passkey authenticator that says yes, as a person touching their fingerprint reader would
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true,
      isUserVerified: true, automaticPresenceSimulation: true },
  });
  const meter = new Meter(page);
  const ctx = { base: a.base, page, meter, here, code: process.env.LENS_SETUP_CODE || "survey-code", installer: !!installer };
  let failed = 0;
  for (const j of JOURNEYS) {
    if (a.only && !a.only.has(j.id)) continue;
    if (j.needs === "installer" && !installer) continue;
    meter.start(j.module, j.feature, j.note);
    meter.journey.id = j.id;
    try {
      if (j.from) {
        await page.goto(a.base + j.from, { waitUntil: "domcontentloaded" });
        await page.getByRole("navigation", { name: "Primary" }).waitFor();
        await page.waitForTimeout(2000); // hydrated, so the first click lands
      }
      await j.run(ctx);
      const r = meter.end(true);
      console.log(`ok   ${j.id.padEnd(22)} ${String(r.clicks).padStart(3)} clicks ${String(r.keys).padStart(4)} keys ${r.prompts} prompts`);
    } catch (e) {
      failed++;
      fs.mkdirSync(a.out, { recursive: true });
      await page.screenshot({ path: path.join(a.out, `failed-${j.id}.png`) }).catch(() => {});
      meter.end(false, e.message);
      console.log(`FAIL ${j.id}: ${String(e.message).split("\n")[0]}`);
    }
  }
  if (a.state) await context.storageState({ path: a.state });
  const pages = a.only ? [] : await surveyPages(page, a.base);
  await browser.close();
  installer?.kill();
  model.close();

  const result = {
    surveyed: new Date().toISOString(),
    commit: process.env.GITHUB_SHA || "",
    viewport: "1400x900",
    journeys: meter.journeys,
    pages,
  };
  fs.mkdirSync(a.out, { recursive: true });
  fs.writeFileSync(path.join(a.out, "clicks.json"), JSON.stringify(result, null, 2) + "\n");
  const base = a.compare ? JSON.parse(fs.readFileSync(a.compare, "utf8")) : null;
  const md = markdown(result, base);
  fs.writeFileSync(path.join(a.out, "clicks.md"), md);
  console.log(`\nwrote ${path.join(a.out, "clicks.json")} and clicks.md`);
  if (base) {
    const worse = compare(result, base).filter((d) => d.worse);
    for (const d of worse) console.log(`worse: ${d.id} ${d.what}`);
    if (worse.length && a.failOnRegression) process.exitCode = 1;
  }
  if (failed) process.exitCode = 1;
}

await main();
