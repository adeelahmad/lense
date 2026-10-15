// The clicks survey: drives a fresh Lens through setup and its main features in Chromium and counts the clicks,
// keystrokes and passkey prompts each takes, per feature and per page. ci/clicks/run.sh starts the fresh Lens; see
// README.md for the rules and how to compare with the baseline.
//
//   node survey.mjs [--base http://localhost:3000] [--out results] [--compare baseline.json] [--only id,id]
//                   [--device desktop|phone] [--no-installer] [--fail-on-regression]
//
// --device phone runs the same journeys on a phone-sized touch screen (taps and swipes instead of clicks, no keyboard
// shortcuts) and writes results/clicks-phone.json and clicks-phone.md.
import { spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { chromium, devices } from "playwright";

import { startFakeModel } from "./fake-model.mjs";
import { JOURNEYS } from "./journeys.mjs";
import { Meter, pageKey } from "./meter.mjs";
import { compare, markdown } from "./report.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const repo = path.resolve(here, "../..");

/** Where the survey runs: a desktop browser, or a phone (Pixel 7: 412x839, touch, no hover or keyboard shortcuts). */
const DEVICES = {
  desktop: { name: "1400x900", context: { viewport: { width: 1400, height: 900 } }, suffix: "" },
  phone: { name: "Pixel 7, 412x839, touch", context: { ...devices["Pixel 7"] }, suffix: "-phone", touch: true },
};

function parse(argv) {
  const a = { base: "http://localhost:3000", out: path.join(here, "results"), installer: true, failOnRegression: false, device: "desktop" };
  for (let i = 0; i < argv.length; i++) {
    const k = argv[i];
    if (k === "--base") a.base = argv[++i];
    else if (k === "--out") a.out = argv[++i];
    else if (k === "--compare") a.compare = argv[++i];
    else if (k === "--only") a.only = new Set(argv[++i].split(","));
    else if (k === "--no-installer") a.installer = false;
    else if (k === "--device") a.device = argv[++i];
    else if (k === "--fail-on-regression") a.failOnRegression = true;
    else if (k === "--headed") a.headed = true;
    else if (k === "--state") a.state = argv[++i];
    else throw new Error(`unknown argument ${k}`);
  }
  if (!DEVICES[a.device]) throw new Error(`--device is one of ${Object.keys(DEVICES).join(", ")}`);
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
async function surveyPages(page, base, phone) {
  const pages = [];
  const controls = () =>
    page.evaluate(() => {
      const main = document.querySelector("main") || document.body;
      const seen = [...main.querySelectorAll("a[href], button, input, select, textarea, [role=tab], [role=switch], [role=radio]")];
      // the page's own controls: not the settings sections or tabs that lead to other pages, nor what a closed fold
      // (details) keeps out of sight, which Chromium still lays out
      return seen.filter((el) => el.offsetParent !== null && !el.closest("[hidden], nav, details:not([open]) > :not(summary)")).length;
    });
  const settle = () => page.waitForTimeout(1200);
  // The controls the floating Ask button covers with the page (and anything scrolling in it) at its end, where
  // scrolling can't carry them clear: it should rise above them or step aside, so any is a failure.
  const askCovers = async () => {
    await page.evaluate(() => {
      window.scrollTo(0, document.documentElement.scrollHeight);
      for (const el of document.querySelectorAll("main *"))
        if (el.scrollHeight > el.clientHeight && /(auto|scroll)/.test(getComputedStyle(el).overflowY)) el.scrollTop = el.scrollHeight;
    });
    await page.waitForTimeout(600); // the button moves on the next frame, then slides
    return page.evaluate(() => {
      const ask = document.querySelector("[data-ask-button]");
      if (!ask || getComputedStyle(ask).visibility === "hidden") return [];
      const a = ask.getBoundingClientRect();
      const out = [];
      const all = "a[href], button, input:not([type=hidden]), select, textarea, [role=tab], [role=switch], [role=checkbox], [role=radio]";
      for (const el of document.querySelectorAll(all)) {
        if (el.closest("[data-ask-button], [data-page-chat]")) continue;
        const r = el.getBoundingClientRect();
        if (r.height > innerHeight / 3) continue; // a big field: the button covers a corner of it (ask-place.ts)
        const [x0, x1, y0, y1] = [Math.max(r.left, a.left), Math.min(r.right, a.right), Math.max(r.top, a.top), Math.min(r.bottom, a.bottom)];
        if (x1 - x0 < 4 || y1 - y0 < 4) continue;
        if (document.elementFromPoint((x0 + x1) / 2, (y0 + y1) / 2)?.closest("[data-ask-button]"))
          out.push((el.getAttribute("aria-label") || el.textContent || el.tagName).replace(/\s+/g, " ").trim().slice(0, 60));
      }
      return out;
    });
  };
  const visit = async (entry) => {
    entry.askCovers = await askCovers();
    if (entry.askCovers.length) console.log(`FAIL ${entry.page}: the Ask button covers ${entry.askCovers.join(", ")}`);
    pages.push(entry);
  };
  await page.goto(base + "/", { waitUntil: "domcontentloaded" });
  await settle();
  if (phone) await page.getByRole("button", { name: "Open navigation" }).tap();
  const nav = await page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link")
    // the label without the count that can follow it (Library 1)
    .evaluateAll((els) => els.map((e) => ({ label: (e.querySelector("span")?.textContent ?? e.textContent).trim(), href: e.getAttribute("href") })));
  const items = nav.filter((n, i) => n.label && n.label !== "Lens" && !n.label.startsWith("Notes ·") && nav.findIndex((m) => m.href === n.href && m.label === n.label) === i);
  // On a phone the navigation sits behind the menu button: one more tap to reach every page
  const menu = phone ? 1 : 0;
  for (const item of items) {
    await page.goto(base + "/", { waitUntil: "domcontentloaded" });
    await settle();
    if (phone) await page.getByRole("button", { name: "Open navigation" }).tap();
    const name = new RegExp(`^${item.label.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")}( \\d+)?$`);
    const link = page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name }).first();
    await (phone ? link.tap() : link.click());
    await page.waitForLoadState("domcontentloaded");
    await settle();
    const key = pageKey(page.url());
    await visit({ page: key, label: item.label, reach: 1 + menu, controls: await controls() });
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
      await visit({ page: pageKey(page.url()), label: `${item.label} › ${s.label}`, reach: 2 + menu, controls: await controls() });
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
  const device = DEVICES[a.device];
  const context = await browser.newContext({ ...device.context, locale: "en-US", storageState: state });
  const page = await context.newPage();
  page.setDefaultTimeout(20000);
  // A passkey authenticator that says yes, as a person touching their fingerprint reader would
  const cdp = await context.newCDPSession(page);
  await cdp.send("WebAuthn.enable");
  await cdp.send("WebAuthn.addVirtualAuthenticator", {
    options: { protocol: "ctap2", transport: "internal", hasResidentKey: true, hasUserVerification: true,
      isUserVerified: true, automaticPresenceSimulation: true },
  });
  const meter = new Meter(page, { touch: !!device.touch });
  const ctx = {
    base: a.base, page, meter, here, code: process.env.LENS_SETUP_CODE || "survey-code", installer: !!installer,
    phone: !!device.touch,
  };
  const base = a.compare ? JSON.parse(fs.readFileSync(a.compare, "utf8")) : null;
  // A journey that failed in the baseline too (one that can't be done on a phone yet) is reported, not a new failure
  const failedBefore = new Set((base?.journeys || []).filter((j) => !j.ok).map((j) => j.id));
  let failed = 0;
  for (const j of JOURNEYS) {
    if (a.only && !a.only.has(j.id)) continue;
    if (j.needs === "installer" && !installer) continue;
    meter.start(j.module, j.feature, j.note);
    meter.journey.id = j.id;
    try {
      if ("ns" in j) {
        // Starts with this namespace picked ("" for all namespaces), whatever the journey before left: not counted
        await page.goto(a.base + "/", { waitUntil: "domcontentloaded" });
        await page.evaluate((ns) => (ns ? localStorage.setItem("lens.namespace", ns) : localStorage.removeItem("lens.namespace")), j.ns);
      }
      if (j.from) {
        await page.goto(a.base + j.from, { waitUntil: "domcontentloaded" });
        await page.getByRole("banner").waitFor();
        await page.waitForTimeout(2000); // hydrated, so the first click lands
      }
      await j.run(ctx);
      const r = meter.end(true);
      console.log(
        `ok   ${j.id.padEnd(22)} ${String(r.clicks).padStart(3)} ${ctx.phone ? "taps  " : "clicks"} ${String(r.keys).padStart(4)} keys ${r.prompts} prompts` +
          (ctx.phone ? ` ${r.swipes} swipes` : ""),
      );
    } catch (e) {
      if (!failedBefore.has(j.id)) failed++;
      fs.mkdirSync(a.out, { recursive: true });
      await page.screenshot({ path: path.join(a.out, `failed-${j.id}${device.suffix}.png`) }).catch(() => {});
      meter.end(false, e.message);
      console.log(`${failedBefore.has(j.id) ? "fail (as in the baseline)" : "FAIL"} ${j.id}: ${String(e.message).split("\n")[0]}`);
    }
  }
  if (a.state) await context.storageState({ path: a.state });
  const pages = a.only ? [] : await surveyPages(page, a.base, ctx.phone);
  await browser.close();
  if (installer) {
    // and wait for it to go, so a survey straight after this one gets its port
    const gone = new Promise((r) => (installer.exitCode != null || installer.signalCode != null ? r() : installer.once("exit", r)));
    installer.kill();
    await gone;
  }
  model.close();

  const result = {
    surveyed: new Date().toISOString(),
    commit: process.env.GITHUB_SHA || "",
    viewport: device.name,
    device: a.device,
    journeys: meter.journeys,
    pages,
  };
  fs.mkdirSync(a.out, { recursive: true });
  const name = `clicks${device.suffix}`;
  fs.writeFileSync(path.join(a.out, `${name}.json`), JSON.stringify(result, null, 2) + "\n");
  fs.writeFileSync(path.join(a.out, `${name}.md`), markdown(result, base));
  console.log(`\nwrote ${path.join(a.out, `${name}.json`)} and ${name}.md`);
  // The API's log beside the results, so a failure in CI can be read from the artifact
  if (process.env.LENS_SURVEY_BACKEND_LOG) fs.copyFileSync(process.env.LENS_SURVEY_BACKEND_LOG, path.join(a.out, `backend${device.suffix}.log`));
  if (base) {
    const worse = compare(result, base).filter((d) => d.worse);
    for (const d of worse) console.log(`worse: ${d.id} ${d.what}`);
    if (worse.length && a.failOnRegression) process.exitCode = 1;
  }
  if (failed || pages.some((p) => p.askCovers.length)) process.exitCode = 1;
}

await main();
