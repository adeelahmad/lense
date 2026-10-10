// Edge smoke test: loads dist/edge (npm run build in ..) into Microsoft Edge, opens the popup and options, clips an
// article page and checks a stand-in Lens import webhook got its Markdown. Then it signs the side panel in to the
// stand-in Lens through its OAuth consent page, asks the assistant and approves what it proposes, and has the stand-in
// Lens send the side panel browser actions: reading a page once its site is allowed, typing, a click that waits for
// the person's yes, refusing a password field, and downloading a file into a held upload. Run under a display
// (xvfb-run in CI).
// EDGE_CHANNEL=chromium runs the same test in Playwright's Chromium, for machines without Edge.
import { createHash } from "node:crypto";
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

// the article page and a stand-in for Lens (its import webhook, app sign-in and assistant), on one local port
const pushes = [];
const asked = [];
const decided = [];
const ACCESS = "lens-smoke-access-token";
let challenge = "";
const sse = (events) => events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join("");
const ANSWER = sse([
  ["step", { tool: "search", args: {}, summary: "Searched for “renewal”" }],
  ["approval", { id: 41, tool: "import_files", summary: "Import invoice.pdf into Personal" }],
  ["passages", [{ n: 1, recording_id: 7, title: "Renewal call", time: "12:34", t0: 754000, text: "renewal" }]],
  ["token", { text: "The renewal is **due in March** [1]." }],
  ["done", { message: 12 }],
]);
const server = createServer((req, res) => {
  const cors = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Authorization, Content-Type, Accept",
    "Access-Control-Allow-Methods": "POST, OPTIONS",
  };
  if (req.method === "OPTIONS") return res.writeHead(204, cors).end();
  if (lens(req, res)) return;
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

// the browser, as Lens uses it: a session, its stream of actions, and their answers
const browserSide = { label: null, stream: null, answers: new Map(), waiting: new Map(), ended: false, uploaded: null, next: 1 };
function act(tool, args = {}, chat = null) {
  const id = `a${browserSide.next++}`;
  const done = new Promise((resolve, reject) => {
    browserSide.waiting.set(id, resolve);
    setTimeout(() => reject(new Error(`no answer to ${tool} ${id}`)), 30000);
  });
  browserSide.stream.write(`event: action\ndata: ${JSON.stringify({ id, tool, args, chat })}\n\n`);
  return done;
}
const COMPOSE = `<!doctype html><html lang="en"><head><title>Compose</title></head><body><h1>New message</h1>
<label>To <input id="to"></label> <label>Password <input id="pw" type="password"></label>
<button id="send" onclick="document.getElementById('state').textContent = 'Sent to ' + document.getElementById('to').value">Send</button>
<p id="state">Draft</p><p>Attached: <a href="/files/report.pdf">report.pdf</a></p></body></html>`;
const PDF = "%PDF-1.4\n% a stand-in report\n%%EOF\n";

function lens(req, res) {
  const url = new URL(req.url, origin);
  const reply = (status, body, headers = {}) => res.writeHead(status, { "Content-Type": "application/json", ...headers }).end(JSON.stringify(body));
  const read = (then) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => then(body));
  };
  if (url.pathname === "/.well-known/oauth-authorization-server") {
    reply(200, {
      issuer: origin,
      authorization_endpoint: `${origin}/oauth/authorize`,
      token_endpoint: `${origin}/api/v1/oauth/token`,
      registration_endpoint: `${origin}/api/v1/oauth/register`,
      revocation_endpoint: `${origin}/api/v1/oauth/revoke`,
    });
  } else if (url.pathname === "/api/v1/oauth/register") {
    read(() => reply(201, { client_id: "smoke-client" }));
  } else if (url.pathname === "/oauth/authorize") {
    // the consent page, with the person saying yes at once: back to the extension with a code
    challenge = url.searchParams.get("code_challenge");
    const back = new URL(url.searchParams.get("redirect_uri"));
    back.searchParams.set("code", "smoke-code");
    back.searchParams.set("state", url.searchParams.get("state"));
    res.writeHead(302, { Location: back.toString() }).end();
  } else if (url.pathname === "/api/v1/oauth/token") {
    read((body) => {
      const form = new URLSearchParams(body);
      const ok = form.get("code") === "smoke-code" && createHash("sha256").update(form.get("code_verifier")).digest("base64url") === challenge;
      reply(ok ? 200 : 400, ok ? { access_token: ACCESS, refresh_token: "smoke-refresh", expires_in: 3600 } : { error: "invalid_grant" });
    });
  } else if (url.pathname.startsWith("/api/v1/") && req.headers.authorization !== `Bearer ${ACCESS}` && !url.pathname.startsWith("/api/v1/hooks/")) {
    reply(401, { detail: "sign in" });
  } else if (url.pathname === "/api/v1/auth/me") {
    reply(200, { user: { email: "ada@example.com", name: "Ada" } });
  } else if (url.pathname === "/api/v1/chats" && req.method === "POST") {
    read(() => reply(200, { id: 5 }));
  } else if (url.pathname === "/api/v1/chats/5/messages" && req.method === "POST") {
    read((body) => {
      asked.push(JSON.parse(body));
      res.writeHead(200, { "Content-Type": "text/event-stream" }).end(ANSWER);
    });
  } else if (url.pathname === "/api/v1/approvals/41" && req.method === "POST") {
    read((body) => {
      decided.push(JSON.parse(body));
      reply(200, { status: "done" });
    });
  } else if (url.pathname === "/compose") {
    res.writeHead(200, { "Content-Type": "text/html; charset=utf-8" }).end(COMPOSE);
  } else if (url.pathname === "/files/report.pdf") {
    res.writeHead(200, { "Content-Type": "application/pdf", "Content-Disposition": 'attachment; filename="report.pdf"' }).end(PDF);
  } else if (url.pathname === "/api/v1/browser/sessions" && req.method === "POST") {
    read((body) => {
      browserSide.label = JSON.parse(body || "{}").label;
      reply(201, { id: "s1", label: browserSide.label, live: true });
    });
  } else if (url.pathname === "/api/v1/browser/sessions/s1/actions") {
    res.writeHead(200, { "Content-Type": "text/event-stream", "Cache-Control": "no-store" });
    res.write(": open\n\n");
    browserSide.stream = res;
    req.on("close", () => browserSide.stream === res && (browserSide.stream = null));
  } else if (url.pathname.startsWith("/api/v1/browser/actions/") && req.method === "POST") {
    read((body) => {
      const id = url.pathname.split("/").pop();
      browserSide.answers.set(id, JSON.parse(body));
      browserSide.waiting.get(id)?.(JSON.parse(body));
      reply(200, { ok: true });
    });
  } else if (url.pathname === "/api/v1/browser/sessions/s1" && req.method === "DELETE") {
    browserSide.ended = true;
    reply(200, { ok: true });
  } else if (url.pathname === "/api/v1/uploads/limits") {
    reply(200, { max_mb: 10, chunk_mb: 8, extensions: [".pdf"] });
  } else if (url.pathname === "/api/v1/uploads" && req.method === "POST") {
    read((body) => {
      const b = JSON.parse(body);
      reply(201, { id: "up1", filename: b.filename, size: b.size, offset: 0, state: "receiving", hold: b.hold });
    });
  } else if (url.pathname === "/api/v1/uploads/up1" && req.method === "PUT") {
    read((body) => {
      browserSide.uploaded = body;
      reply(200, { id: "up1", filename: "report.pdf", size: body.length, offset: body.length, state: "held" });
    });
  } else {
    return false;
  }
  return true;
}

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
  for (const page of ["options.html", "popup.html", "sidepanel.html"]) {
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

  // the side panel: sign in through the consent page, ask, see the cited answer, approve what it proposes
  check(manifest.side_panel?.default_path === "sidepanel.html" && manifest.permissions.includes("sidePanel"), "the manifest has the side panel");
  const panel = await context.newPage();
  panel.on("pageerror", (e) => errors.push(`side panel: ${e.message}`));
  await panel.goto(`chrome-extension://${id}/sidepanel.html`);
  check((await panel.inputValue("#server")) === origin, "the side panel offers the clipper's Lens to sign in to");
  await panel.click("#signinButton");
  await panel.waitForSelector("#q:visible", { timeout: 20000 }).catch(() => null);
  check(await panel.isVisible("#q"), `the side panel signs in through Lens's consent page${(await panel.isVisible("#q")) ? "" : `: ${await panel.textContent("#signinStatus")}`}`);
  const kept = await panel.evaluate(async () => (await chrome.storage.local.get("account")).account);
  check(kept?.accessToken === ACCESS && kept.email === "ada@example.com", "and keeps the token and who signed in");
  await panel.fill("#q", "When is the renewal?");
  await panel.press("#q", "Enter");
  await panel.waitForSelector(".msg.assistant a.cite", { timeout: 10000 }).catch(() => null);
  check(asked.at(-1)?.content === "When is the renewal?", "Enter asks the question");
  check((await panel.textContent(".msg.assistant .steps")).includes("Searched for"), "the steps show");
  const cite = await panel.getAttribute(".msg.assistant a.cite", "href").catch(() => null);
  check(cite === `${origin}/resources/7?t=754`, "the answer cites the moment it came from");
  check((await panel.textContent(".msg.assistant strong").catch(() => "")) === "due in March", "the answer is Markdown");
  await panel.click(".approval button.primary");
  await panel.waitForFunction(() => document.querySelector(".approval")?.textContent.includes("Done"), null, { timeout: 5000 }).catch(() => null);
  check(decided.at(-1)?.decision === "approve", "approving sends the person's pick to Lens");

  // the assistant using this browser: Lens sends actions, the side panel carries them out on sites the person allows
  await panel.waitForFunction(() => document.getElementById("browserState").textContent.includes("can use"), null, { timeout: 10000 }).catch(() => null);
  check(Boolean(browserSide.stream) && /\S/.test(browserSide.label ?? ""), `the side panel connects this browser to Lens (${browserSide.label})`);
  const compose = await context.newPage();
  await compose.goto(`${origin}/compose`);
  const tab_id = await panel.evaluate(async (url) => (await chrome.tabs.query({ url })).at(0)?.id, `${origin}/compose`);
  const reading = act("read", { tab_id });
  await panel.locator(".ask.site button.primary").click({ timeout: 10000 });
  let a = await reading;
  check(a.ok && a.result.text.includes("New message"), `Lens reads a page once the person allows its site${a.ok ? "" : `: ${a.error}`}`);
  const refOf = (name) => new RegExp(`\\[(e\\d+)\\] [a-z ]+"${name}"`).exec(a.result?.text ?? "")?.[1];
  const [to, pw, send, file] = ["To", "Password", "Send", "report.pdf"].map(refOf);
  a = await act("type", { tab_id, ref: to, text: "ada@example.com" });
  check(a.ok && (await compose.inputValue("#to")) === "ada@example.com", `it types into a field${a.ok ? "" : `: ${a.error}`}`);
  a = await act("type", { tab_id, ref: pw, text: "hunter2" });
  check(!a.ok && /password/.test(a.error) && (await compose.inputValue("#pw")) === "", "and never into a password field");
  const declined = act("click", { tab_id, ref: send });
  await panel.locator(".ask.action").last().getByRole("button", { name: "No" }).click({ timeout: 10000 });
  a = await declined;
  check(!a.ok && /said no/.test(a.error) && (await compose.textContent("#state")) === "Draft", "a consequential click waits for the person, and No means it doesn't happen");
  const sending = act("click", { tab_id, ref: send });
  await panel.locator(".ask.action").last().getByRole("button", { name: "Yes, do it" }).click({ timeout: 10000 });
  a = await sending;
  check(a.ok && (await compose.textContent("#state")) === "Sent to ada@example.com", `and Yes does it${a.ok ? "" : `: ${a.error}`}`);
  check(a.result?.text?.includes("Sent to ada@example.com"), "the answer has the page as it is after the click");
  a = await act("import", { tab_id, ref: file });
  check(a.ok && a.result.upload_id === "up1" && browserSide.uploaded === PDF, `it downloads a file into a held upload${a.ok ? "" : `: ${a.error}`}`);
  const logged = await panel.locator(".steps li").allTextContents();
  check(logged.some((t) => t.startsWith("Clicked “Send”")) && logged.some((t) => t.startsWith("Downloaded report.pdf")), "each action shows in the side panel");
  await panel.close({ runBeforeUnload: true });
  for (let i = 0; i < 20 && !browserSide.ended; i++) await new Promise((ok) => setTimeout(ok, 100));
  check(browserSide.ended, "closing the side panel ends the session");
  check(errors.length === 0, `no page errors${errors.length ? `: ${errors.join("; ")}` : ""}`);
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
