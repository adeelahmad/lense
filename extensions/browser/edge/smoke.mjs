// Edge smoke test: loads dist/edge (npm run build in ..) into Microsoft Edge, opens the popup and options, clips an
// article page and checks a stand-in Lens import webhook got its Markdown. Run under a display (xvfb-run in CI).
// EDGE_CHANNEL=chromium runs the same test in Playwright's Chromium, for machines without Edge.
import { cpSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import { createServer } from "node:http";
import { tmpdir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import { chromium } from "playwright-core";

const here = dirname(fileURLToPath(import.meta.url));
const built = join(here, "..", "dist", "edge");
const TOKEN = "lih_edge-smoke-token";

const ARTICLE = `<!doctype html><html lang="en"><head>
<title>Edge smoke article</title>
<meta property="og:title" content="Clipping from Edge">
<meta name="author" content="Ada Lovelace">
<meta property="og:site_name" content="Smoke Times">
</head><body><nav>Home · About</nav><article><h1>Clipping from Edge</h1>
${"<p>The Lens clipper turns what you read into Markdown with its metadata and images, so it can be filed and found again later.</p>".repeat(6)}
<ul><li>first point</li><li>second point</li></ul><pre><code class="language-js">const x = 1;</code></pre>
</article><footer>footer links</footer></body></html>`;

// the article page and a stand-in for Lens's import webhook, on one local port
const pushes = [];
const server = createServer((req, res) => {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
  };
  if (req.method === "OPTIONS") return res.writeHead(204, cors).end();
  if (req.url === "/article") return res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }).end(ARTICLE);
  if (req.url === "/api/v1/hooks/import" && req.method === "POST") {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      pushes.push({ auth: req.headers.authorization, type: req.headers["content-type"], body });
      res.writeHead(200, { ...cors, "Content-Type": "application/json" });
      res.end(JSON.stringify({ namespace: "smoke", items: [{ kind: "text", name: "clip", recording: "r1" }] }));
    });
    return;
  }
  res.writeHead(404, cors).end();
});
await new Promise((ok) => server.listen(0, "127.0.0.1", ok));
const origin = `http://127.0.0.1:${server.address().port}`;

// The real build only reaches a tab through activeTab, which a click grants and a test can't. This copy also holds
// host permission for the test server; everything else is dist/edge as shipped.
const ext = mkdtempSync(join(tmpdir(), "lens-clipper-edge-"));
cpSync(built, ext, { recursive: true });
const manifest = JSON.parse(readFileSync(join(ext, "manifest.json"), "utf8"));
writeFileSync(join(ext, "manifest.json"), JSON.stringify({ ...manifest, host_permissions: ["http://127.0.0.1/*"] }));

const profile = mkdtempSync(join(tmpdir(), "lens-edge-profile-"));
const channel = process.env.EDGE_CHANNEL || "msedge";
const context = await chromium.launchPersistentContext(profile, {
  channel: channel === "chromium" ? undefined : channel,
  executablePath: channel === "chromium" ? process.env.CHROMIUM_PATH : undefined,
  headless: false,
  args: [`--disable-extensions-except=${ext}`, `--load-extension=${ext}`, "--no-first-run"],
});

const failures = [];
const check = (ok, what) => {
  console.log(`${ok ? "ok  " : "FAIL"} ${what}`);
  if (!ok) failures.push(what);
};

try {
  const browserVersion = context.browser()?.version() ?? "";
  const worker = context.serviceWorkers()[0] ?? (await context.waitForEvent("serviceworker", { timeout: 15000 }));
  const id = new URL(worker.url()).host;
  console.log(`${channel} ${browserVersion}: extension ${id} loaded (${manifest.name} ${manifest.version})`);
  check(manifest.manifest_version === 3 && manifest.minimum_chrome_version, "the Edge manifest is MV3 with a minimum version");

  // a first install opens the options page; it and the popup render without errors
  const errors = [];
  for (const page of ["options.html", "popup.html"]) {
    const p = await context.newPage();
    p.on("pageerror", (e) => errors.push(`${page}: ${e.message}`));
    await p.goto(`chrome-extension://${id}/${page}`);
    await p.waitForLoadState("load");
    check((await p.locator("body").innerText()).trim().length > 0, `${page} renders`);
    await p.close();
  }

  // a destination, as the options page saves one
  await worker.evaluate(
    async ({ server, token }) => {
      await chrome.storage.sync.set({ destinations: [{ id: "d1", name: "Smoke", server }], defaultDestination: "d1" });
      await chrome.storage.local.set({ "token:d1": token });
    },
    { server: origin, token: TOKEN },
  );

  // clip the article and send it, the way the popup does: a "clip" message, then a "send" one
  const article = await context.newPage();
  await article.goto(`${origin}/article`);
  const page = await context.newPage();
  await page.goto(`chrome-extension://${id}/options.html`);
  const tabId = await page.evaluate(async (url) => (await chrome.tabs.query({ url })).at(0)?.id, `${origin}/article`);
  const clipped = await page.evaluate((tabId) => chrome.runtime.sendMessage({ type: "clip", tabId, mode: "page" }), tabId);
  check(clipped?.ok, `the page clips${clipped?.ok ? "" : `: ${clipped?.error}`}`);
  const md = clipped?.clip?.markdown ?? "";
  if (process.env.SMOKE_DEBUG) console.log(md);
  check(/^title: "?Clipping from Edge"?$/m.test(md), "the clip's front matter has the title");
  check(/^author: "?Ada Lovelace"?$/m.test(md), "and the author");
  check(/^-\s+second point$/m.test(md) && md.includes("```js"), "the article's list and code block are Markdown");
  check(!md.includes("footer links"), "Readability left the footer out");

  const sent = await page.evaluate((clip) => chrome.runtime.sendMessage({ type: "send", clip }), clipped?.clip);
  check(sent?.ok && sent.answer?.namespace === "smoke", `the clip is sent${sent?.ok ? "" : `: ${sent?.error}`}`);
  const push = pushes.at(-1);
  check(push?.auth === `Bearer ${TOKEN}`, "Lens got it with the hook's token");
  check(push && JSON.parse(push.body).text === md, "and the Markdown as text");
} finally {
  await context.close();
  server.close();
  rmSync(ext, { recursive: true, force: true });
  rmSync(profile, { recursive: true, force: true });
}

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed`);
  process.exit(1);
}
console.log("\nEdge smoke test passed");
