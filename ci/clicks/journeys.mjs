// What the survey does, in order: each journey is one feature, done the way a person would with the defaults, from
// where they'd usually start. They run one after another on the same fresh Lens, so later ones can use what earlier
// ones made (the admin account, an imported transcript). Keep ids stable: the baseline compares by id. `ns` starts a
// journey with that namespace picked ("" for all namespaces, as on a fresh install) rather than whatever the journey
// before left picked.

import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const INSTALLER = "http://localhost:3990";

/** A page in the primary navigation (the sidebar). */
const nav = (page, label) =>
  page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: new RegExp(`^${label}( \\d+)?$`) }) // a count can follow the label (Library 1)
    .first();

/** Opens a page from the primary navigation. On a phone the navigation sits behind the menu button. */
async function go({ page, meter, phone }, label) {
  if (phone) await meter.click(page.getByRole("button", { name: "Open navigation" }), "Open navigation (menu)");
  await meter.click(nav(page, label), label);
}

/** The palette that jumps to a recording or a page: Ctrl+K on a computer, the Jump to box in the top bar on a phone. */
async function jumpTo({ page, meter, phone }) {
  if (phone) await meter.click(page.getByRole("banner").getByRole("button", { name: /^Jump to/ }), "Jump to (top bar)");
  else await meter.press("Control+k", "Ctrl+K");
}

/** Import: the top bar's button. A phone's top bar has none, so there it's the Jump to palette's Import action. */
async function openImport(ctx) {
  const { page, meter, phone } = ctx;
  if (!phone) return meter.click(page.getByRole("banner").getByRole("link", { name: "Import" }), "Import (top bar)");
  await jumpTo(ctx);
  await meter.type(page.getByRole("combobox", { name: /Search recordings and pages/ }), "import", "import");
  await meter.click(page.getByRole("option", { name: /^Import/ }).first(), "Import recordings or transcripts");
  await page.waitForURL(/\/import/);
}

/** Fails the journey with the reason when a control isn't on the screen at all (on a phone some are hidden). */
async function needs({ phone }, locator, what) {
  try {
    await locator.waitFor({ state: "visible", timeout: phone ? 5000 : 20000 });
  } catch {
    throw new Error(`${what} isn't on the screen${phone ? " on a phone" : ""}`);
  }
}

/** Saves a settings form: Ctrl+S on a computer, the Save button on a phone (no keyboard shortcuts there). */
async function save({ page, meter, phone }) {
  if (phone) await meter.click(page.getByRole("main").getByRole("button", { name: /^Save/ }).first(), "Save");
  else await meter.press("Control+s", "Ctrl+S to save");
}

/** The model-backed answer the stand-in model gives (fake-model.mjs). */
const ANSWER = /stand-in model answering/;

const TRANSCRIPT = [
  "Host A: Welcome back. Today we talk about local models on small computers.",
  "Host B: A Raspberry Pi can run Lens when the models are in the cloud.",
  "Host A: And everything stays on your own hardware.",
].join("\n");

export const JOURNEYS = [
  // --- Setup -------------------------------------------------------------------------------------------------------
  {
    id: "install",
    module: "Setup",
    feature: "Install with the web installer (defaults, local model found)",
    note: "The install line in a terminal, then the browser installer. The browser opens on its own on a desktop; on a server the link is opened by hand (counted). A computer missing Docker also asks for its password.",
    needs: "installer",
    async run(ctx) {
      const { page, meter, base } = ctx;
      meter.record("terminal", "paste the install line and press Enter", 0, 2);
      meter.openLink("open the installer link");
      await page.goto(`${INSTALLER}/?t=survey-token`);
      await meter.click(page.locator("#next"), "Continue (this computer)");
      await page.getByRole("heading", { name: "How Lens runs" }).waitFor();
      await meter.click(page.locator("#next"), "Continue (how it runs)");
      const found = page.getByRole("button", { name: /Ollama · \d+ models/ });
      await found.waitFor();
      await meter.click(found, "pick the model server found on this computer");
      await meter.click(page.locator("#next"), "Continue (AI model)");
      await page.getByRole("heading", { name: "Extras" }).waitFor();
      await meter.click(page.locator("#next"), "Continue (extras)");
      await page.getByRole("heading", { name: "Ready to install" }).waitFor();
      await meter.click(page.locator("#next"), "Install Lens");
      const open = page.getByRole("button", { name: "Open Lens" });
      await open.waitFor({ timeout: 60000 });
      await meter.click(open, "Open Lens");
      await page.waitForURL(`${base}/setup**`);
    },
  },
  {
    id: "first-admin",
    module: "Setup",
    feature: "Create the first admin (passkey)",
    note: "From the setup link, which fills in the setup code.",
    async run(ctx) {
      const { page, meter, base, code, installer } = ctx;
      if (!installer) {
        meter.openLink("open the setup link the server printed");
        await page.goto(`${base}/setup?code=${code}`);
      }
      await page.getByRole("heading", { name: "Set up this server" }).waitFor();
      await meter.type(page.getByRole("textbox", { name: "Email" }), "admin@example.com", "email");
      await meter.click(page.getByRole("button", { name: /Create admin/ }), "Create admin with a passkey");
      meter.passkey("create the passkey");
      await page.waitForURL(`${base}/welcome`);
    },
  },
  {
    id: "setup-wizard",
    module: "Setup",
    feature: "First-run setup wizard, every step with its defaults",
    note: "The namespaces come from the install's configuration, so the wizard opens on the model provider. Finish with defaults (one click) skips the remaining steps.",
    async run(ctx) {
      const { page, meter, base } = ctx;
      await page.getByRole("heading", { name: "Connect a model provider" }).waitFor();
      await page.getByRole("button", { name: /Ollama 2 models/ }).waitFor();
      for (const [heading, button] of [
        ["Connect a model provider", "Save and continue"],
        ["Storage", "Save and continue"],
        ["Apps and AI assistants", "Save and continue"],
        ["Telemetry", "Save and finish"],
      ]) {
        await page.getByRole("heading", { name: heading, exact: true }).waitFor();
        await meter.click(page.getByRole("button", { name: button }), `${button} (${heading})`);
      }
      await page.waitForURL(`${base}/`);
    },
  },
  {
    id: "first-question",
    module: "Setup",
    feature: "Ask the first question from the welcome tour",
    async run(ctx) {
      const { page, meter } = ctx;
      const dialog = page.getByRole("dialog", { name: "Welcome to Lens" });
      await dialog.waitFor();
      await meter.type(dialog.getByRole("textbox", { name: "Ask the assistant" }), "What did the hosts say about Raspberry Pi?", "question");
      await meter.press("Enter", "Enter to ask");
      // The tour opens Chat with the question in the composer, not sent
      const composer = page.getByRole("textbox", { name: "Your question" });
      await composer.waitFor();
      if (!(await page.getByText(ANSWER).first().isVisible().catch(() => false))) {
        await page.waitForTimeout(1500);
        if ((await composer.inputValue()).trim()) await meter.press("Enter", "Enter again: the question waits in Chat's composer");
      }
      await page.getByText(ANSWER).first().waitFor({ timeout: 60000 });
    },
  },

  // --- Everyday use: each starts on Home, as after signing in -----------------------------------------------------
  {
    id: "import-paste",
    module: "Import",
    feature: "Import a pasted transcript",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await openImport(ctx);
      await meter.click(page.getByRole("tab", { name: "Paste" }), "Paste tab");
      await meter.paste(page.getByRole("textbox", { name: "Paste a transcript" }), TRANSCRIPT, "paste the transcript");
      await page.getByText("3 turns").waitFor();
      await meter.click(page.getByRole("main").getByRole("button", { name: "Import", exact: true }), "Import");
      await page.getByText(/import sent/).waitFor({ timeout: 30000 });
    },
  },
  {
    id: "library-open",
    module: "Library",
    feature: "Open a recording from the Library",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Library");
      const item = page.getByRole("main").getByRole("link", { name: /Welcome back/ }).first();
      await item.waitFor();
      await meter.click(item, "the recording");
      await page.waitForURL(/\/(recordings|resources)\//);
    },
  },
  {
    id: "search-word",
    module: "Search",
    feature: "Search for a word and see where it was said",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Search");
      await meter.type(page.getByRole("searchbox", { name: "Search transcripts" }), "raspberry", "the word");
      await meter.press("Enter", "Enter to search");
      await page.getByRole("main").getByText(/Raspberry Pi can run Lens/).first().waitFor();
    },
  },
  {
    id: "jump-to",
    module: "Navigation",
    feature: "Jump to a recording with Ctrl+K",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await jumpTo(ctx);
      await meter.type(page.getByRole("combobox", { name: /Search recordings and pages/ }), "welcome", "part of its title");
      await page.getByRole("option", { name: /Welcome back/ }).first().waitFor();
      await meter.press("Enter", "Enter to open");
      await page.waitForURL(/\/(recordings|resources)\//);
    },
  },
  {
    id: "chat-ask",
    module: "Chat",
    feature: "Ask the assistant in Chat",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Chat");
      await meter.click(page.getByRole("button", { name: "New conversation" }), "New conversation");
      await meter.type(page.getByRole("textbox", { name: "Your question" }), "Who said what about Raspberry Pi?", "question");
      await meter.press("Enter", "Enter to send");
      await page.getByText(ANSWER).first().waitFor({ timeout: 60000 });
    },
  },
  {
    id: "note-create",
    module: "Notes",
    feature: "Write a note",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Notes");
      await meter.click(page.getByRole("main").getByRole("button", { name: "New note" }).first(), "New note");
      await page.waitForURL(/\/notes\/\d+/);
      const title = page.getByRole("textbox", { name: "Title" });
      await meter.replace(title, "Pi setup", "title");
      const body = page.locator("main affine-page-root [contenteditable=true]").first();
      const editor = (await body.count()) ? body : page.locator("main .inline-editor").last();
      await meter.type(editor, "Use cloud models on the Pi.", "first line");
      await page.getByText("Saved").first().waitFor();
    },
  },
  {
    id: "topic-add",
    module: "Topics",
    feature: "Add a topic",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Topics");
      await meter.click(page.getByRole("main").getByRole("button", { name: "Add topic" }).first(), "Add topic");
      await meter.type(page.getByRole("textbox", { name: "Label", exact: true }), "Hardware", "label");
      await meter.press("Enter", "Enter to add");
      await page.getByRole("main").getByText("Hardware").first().waitFor();
    },
  },
  {
    id: "pipeline-create",
    module: "Pipelines",
    feature: "Make a pipeline from the standard steps",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Pipelines");
      await meter.click(page.getByRole("main").getByRole("link", { name: "New pipeline" }), "New pipeline");
      await meter.type(page.getByRole("textbox", { name: "Pipeline name" }), "Quick pipeline", "name");
      await meter.click(page.getByRole("button", { name: "Create pipeline" }), "Create pipeline");
      await page.waitForURL(/\/pipelines\/\d+/);
    },
  },
  {
    id: "routine-create",
    module: "Routines",
    feature: "Make a nightly routine (sync sources, the default action)",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Routines");
      await meter.click(page.getByRole("main").getByRole("link", { name: "New routine" }), "New routine");
      await meter.type(page.getByRole("textbox", { name: "Name" }), "Nightly sync", "name");
      await meter.click(page.getByRole("button", { name: "Create routine" }), "Create routine");
      await page.waitForURL(/\/routines\/\d+/);
    },
  },
  {
    id: "extension-add",
    module: "Extensions",
    feature: "Add the example skill to the assistant",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Extensions");
      await meter.click(page.getByRole("main").getByRole("link", { name: "New", exact: true }), "New");
      await meter.click(page.getByRole("button", { name: "Add to the assistant" }), "Add to the assistant");
      await page.waitForURL(/\/extensions\/\d+/);
    },
  },
  {
    id: "sensor-webhook",
    module: "Sensors",
    feature: "Make a webhook sensor",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Sensors");
      await meter.click(page.getByRole("main").getByRole("button", { name: "Add sensor" }).first(), "Add sensor");
      await meter.click(page.getByRole("dialog").getByRole("button", { name: "Make webhook" }), "Make webhook");
      await page.getByText(/token/i).first().waitFor();
    },
  },
  {
    id: "settings-change",
    module: "Settings",
    feature: "Change the model's timeout",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Settings");
      await meter.click(page.getByRole("link", { name: "LLM provider" }), "LLM provider");
      await meter.replace(page.getByRole("textbox", { name: "Timeout (seconds)" }), "120", "timeout");
      await save(ctx);
      await page.getByText(/Saved|Last saved/).first().waitFor();
    },
  },
  {
    id: "person-add",
    module: "Admin",
    feature: "Add a person as a viewer",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Admin");
      await meter.click(page.getByRole("button", { name: "Create account" }), "Create account");
      const d = page.getByRole("dialog");
      await meter.type(d.getByRole("textbox", { name: "Email" }), "viewer@example.com", "email");
      await meter.click(d.getByRole("button", { name: "Create account" }), "Create account");
      // The sign-in link is shown once, to send to them
      await meter.click(d.getByRole("button", { name: /Copy/ }), "Copy the sign-in link");
      await meter.click(d.getByRole("button", { name: "Done" }), "Done");
      await page.getByRole("main").getByText("viewer@example.com").first().waitFor();
    },
  },
  {
    id: "api-token",
    module: "Account",
    feature: "Make an API token",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await meter.click(page.getByRole("button", { name: "Account menu" }), "Account menu");
      await meter.click(page.getByRole("menuitem", { name: /API tokens/ }), "API tokens");
      await meter.click(page.getByRole("main").getByRole("button", { name: "New token" }).first(), "New token");
      const d = page.getByRole("dialog");
      await meter.type(d.getByRole("textbox", { name: "Name" }), "script", "name");
      await meter.click(d.getByRole("button", { name: "Create token" }), "Create token");
      await page.getByText(/copy/i).first().waitFor();
    },
  },
  {
    id: "namespace-switch",
    module: "Navigation",
    feature: "Switch to another namespace",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await needs(ctx, page.getByRole("button", { name: /^Namespace: / }).first(), "the namespace switcher");
      await meter.click(page.getByRole("button", { name: /^Namespace: / }), "the namespace switcher");
      await meter.click(page.getByRole("option", { name: /^podcasts/ }), "podcasts");
      await page.getByRole("button", { name: /^Namespace: podcasts/ }).waitFor();
    },
  },
  // --- Modules the first survey left out: one everyday task each ---------------------------------------------------
  {
    id: "agent-connect",
    ns: "",
    module: "Agents",
    feature: "Connect an AI assistant (Claude, ChatGPT, Cursor) over MCP",
    note: "In the assistant: add a connector and give it Lens's MCP address; the assistant opens Lens's consent page, where the person allows it. After setup Lens shows the MCP address nowhere, so it is typed (counted as typed).",
    from: "/",
    async run(ctx) {
      const { page, meter, base } = ctx;
      const mcp = `${base}/mcp`;
      meter.record("outside", "in the assistant: add a connector, type Lens's MCP address, add it", 2, mcp.length);
      // What the assistant does by itself (RFC 7591 registration, PKCE), then it opens the consent page
      const redirect = "http://127.0.0.1:9/callback";
      const r = await fetch(`${base}/api/v1/oauth/register`, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ client_name: "Claude", redirect_uris: [redirect], token_endpoint_auth_method: "none" }),
      });
      if (!r.ok) throw new Error(`registering the assistant: ${r.status} ${await r.text()}`);
      const { client_id } = await r.json();
      const verifier = crypto.randomBytes(32).toString("base64url");
      const challenge = crypto.createHash("sha256").update(verifier).digest("base64url");
      const q = new URLSearchParams({
        client_id, redirect_uri: redirect, response_type: "code", code_challenge: challenge,
        code_challenge_method: "S256", state: "survey", scope: "read write",
      });
      // The assistant's own page that the browser is sent back to
      await page.route(`${redirect}**`, (route) => route.fulfill({ contentType: "text/html", body: "Back in the assistant" }));
      await page.goto(`${base}/oauth/authorize?${q}`, { waitUntil: "domcontentloaded" });
      await meter.click(page.getByRole("button", { name: "Allow" }), "Allow");
      await page.waitForURL(`${redirect}**`);
      const code = new URL(page.url()).searchParams.get("code");
      await page.unroute(`${redirect}**`);
      if (!code) throw new Error("the consent page gave the assistant no code");
    },
  },
  {
    id: "import-file",
    ns: "",
    module: "Import",
    feature: "Upload a transcript file",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      const file = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "lens-clicks-")), "pi-episode.txt");
      fs.writeFileSync(file, TRANSCRIPT.replaceAll("Welcome back", "Hello again") + "\n");
      await openImport(ctx);
      await meter.upload(page.getByRole("main").getByRole("button", { name: "Choose files" }), [file], "Choose files, then the file");
      await meter.click(page.getByRole("main").getByRole("button", { name: /^Import/ }).last(), "Import");
      await page.getByText(/import sent|sent \d|imported/i).first().waitFor({ timeout: 30000 });
    },
  },
  {
    id: "source-folder",
    ns: "",
    module: "Sensors",
    feature: "Watch a folder on this machine for new files",
    note: "Needs rclone, as the Lens image has (CI copies the image's version); the folders offered are the allowed ones set at startup.",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Sensors");
      await meter.click(page.getByRole("tab", { name: "Files, email and calendars" }), "Files, email and calendars tab");
      await meter.click(page.getByRole("main").getByRole("button", { name: "Add connection" }).first(), "Add connection");
      const d = page.getByRole("dialog");
      await meter.click(d.getByRole("radio", { name: "This machine" }), "This machine");
      await meter.click(d.getByRole("button", { name: "Test connection" }), "Test connection");
      await meter.click(d.getByRole("button", { name: "Done" }), "Done (the test passed)");
      // The folder browser opens on the allowed folders, which can't be watched themselves: open one first
      const browse = page.getByRole("dialog", { name: /^Browse/ });
      await meter.click(browse.getByRole("table").getByRole("button").first(), "the allowed folder");
      await meter.click(browse.getByRole("button", { name: "Watch this folder" }), "Watch this folder");
      await meter.click(page.getByRole("dialog", { name: /^Watch/ }).getByRole("button", { name: "Start watching" }), "Start watching");
      await page.getByRole("dialog").waitFor({ state: "hidden" });
      await page.getByRole("main").getByText(/[1-9]\d* watched folders?/).first().waitFor();
    },
  },
  {
    id: "entity-find",
    ns: "",
    module: "Entities",
    feature: "Find an entity and see where it was mentioned",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Entities");
      await meter.type(page.getByRole("searchbox", { name: "Find entities" }), "rasp", "part of its name");
      await meter.click(page.getByRole("main").getByRole("button", { name: "Raspberry Pi", exact: true }), "Raspberry Pi");
      await page.waitForURL(/entity=\d+/);
      await page.getByText(/Raspberry Pi can run Lens/).first().waitFor();
    },
  },
  {
    id: "graph-find",
    ns: "",
    module: "Graph",
    feature: "Find a speaker in the graph of what was imported",
    note: "The first namespace keeps its graph to itself, so the graph opens empty until it is picked as the namespace.",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Graph");
      const find = page.getByRole("combobox", { name: "Find in graph" });
      await page.getByRole("region", { name: "Graph" }).waitFor();
      await page.waitForTimeout(1000);
      if (await page.getByRole("heading", { name: /^No graph/ }).isVisible().catch(() => false)) {
        await needs(ctx, page.getByRole("button", { name: /^Namespace: / }).first(), "the namespace switcher");
        await meter.click(page.getByRole("button", { name: /^Namespace: / }).first(), "the namespace switcher");
        await meter.click(page.getByRole("listbox", { name: "Namespaces" }).getByRole("option", { name: /^interviews/ }), "interviews");
      }
      // On a phone the filters, the find box among them, sit behind a button
      if (!(await find.isVisible())) await meter.click(page.getByRole("button", { name: "Filters" }), "Filters");
      await meter.type(find, "host b", "part of the name");
      await page.getByRole("option", { name: /Host B/ }).first().waitFor();
      await meter.press("Enter", "Enter to pick it");
      // On a phone the filters stay open over the node picked
      const filters = page.getByRole("dialog", { name: "Graph filters" });
      if (await filters.isVisible().catch(() => false))
        await meter.click(filters.getByRole("button", { name: "Close" }), "close the filters (they stay open over the node)");
      await page.getByRole("heading", { name: "Host B" }).first().waitFor();
    },
  },
  {
    id: "podcast-create",
    ns: "",
    module: "Podcasts",
    feature: "Make a podcast from a recording",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Library");
      await meter.click(page.getByRole("main").getByRole("link", { name: /Welcome back/ }).first(), "the recording");
      await page.waitForURL(/\/(recordings|resources)\//);
      await meter.click(page.getByRole("main").getByRole("button", { name: "More actions" }), "More actions");
      await meter.click(page.getByRole("menuitem", { name: /Create podcast/ }), "Create podcast…");
      await meter.click(page.getByRole("dialog").getByRole("button", { name: "Create podcast" }), "Create podcast");
      await page.waitForURL(/\/podcasts\/\d+/, { timeout: 30000 });
    },
  },
  {
    id: "report-pdf",
    ns: "",
    module: "Reports",
    feature: "Save the namespace report as a PDF",
    note: "The browser's print dialog does the saving (one click there, counted).",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Reports");
      await page.evaluate(() => {
        window.__printed = false;
        window.print = () => (window.__printed = true);
      });
      await meter.click(page.getByRole("main").getByRole("button", { name: "PDF / HTML" }), "PDF / HTML");
      await meter.click(page.getByRole("menuitem", { name: "Print or save as PDF" }), "Print or save as PDF");
      await page.waitForFunction(() => window.__printed);
      meter.record("outside", "the print dialog: Save", 1, 0);
    },
  },
  {
    id: "workflow-create",
    ns: "",
    module: "Pipelines",
    feature: "Make a workflow",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Pipelines");
      await meter.click(page.getByRole("tab", { name: /^Workflows/ }), "Workflows tab");
      await meter.click(page.getByRole("main").getByRole("link", { name: "New workflow" }), "New workflow");
      await meter.type(page.getByRole("textbox", { name: "Workflow name" }), "Meeting notes", "name");
      await meter.click(page.getByRole("button", { name: "Create workflow" }), "Create workflow");
      await page.waitForURL(/\/workflows\/\d+/);
    },
  },
  {
    id: "template-create",
    ns: "",
    module: "Pipelines",
    feature: "Make a prompt template",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Pipelines");
      await meter.click(page.getByRole("tab", { name: /^Templates/ }), "Templates tab");
      await meter.click(page.getByRole("main").getByRole("link", { name: "New template" }), "New template");
      await meter.type(page.getByRole("textbox", { name: "Name" }), "Pi summary", "name");
      await meter.click(page.getByRole("button", { name: "Create and edit" }), "Create and edit");
      await page.waitForURL(/\/templates\/\d+/);
    },
  },
  {
    id: "namespace-create",
    ns: "",
    module: "Admin",
    feature: "Make a namespace",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Admin");
      await meter.click(page.getByRole("tab", { name: /^Namespaces/ }), "Namespaces tab");
      await meter.click(page.getByRole("main").getByRole("button", { name: "New namespace" }), "New namespace");
      const d = page.getByRole("dialog");
      await meter.type(d.getByRole("textbox", { name: "Name" }), "customer-calls", "name");
      await meter.press("Enter", "Enter to create");
      await page.getByRole("main").getByRole("link", { name: "customer-calls" }).first().waitFor();
    },
  },
  {
    id: "speaker-rename",
    ns: "interviews",
    module: "Speakers",
    feature: "Name a speaker",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Settings");
      await meter.click(page.getByRole("main").getByRole("link", { name: "Host A", exact: true }), "Host A");
      await meter.click(page.getByRole("main").getByRole("button", { name: "Rename" }), "Rename");
      await meter.replace(page.getByRole("textbox").first(), "Alice", "the name");
      await meter.press("Enter", "Enter to save");
      await page.getByRole("heading", { name: "Alice", level: 1 }).waitFor();
    },
  },
  {
    // Last: it points Lens at a cloud model, which the stand-in model behind the journeys above isn't
    id: "cloud-model",
    ns: "",
    module: "Setup",
    feature: "Switch to a cloud model with an API key",
    note: "What a Raspberry Pi install does after the installer: an OpenAI-compatible cloud provider, its model and a pasted key.",
    from: "/",
    async run(ctx) {
      const { page, meter } = ctx;
      await go(ctx, "Settings");
      await meter.click(page.getByRole("link", { name: "LLM provider" }), "LLM provider");
      await meter.replace(page.getByRole("textbox", { name: "Base URL (OpenAI-compatible)" }), "https://api.openai.com/v1", "the provider's address");
      await meter.replace(page.getByRole("textbox", { name: "Model", exact: true }), "gpt-4o-mini", "the model");
      await meter.paste(page.getByRole("textbox", { name: "API key" }), "sk-survey-0000000000000000", "paste the API key");
      await save(ctx);
      await page.getByText(/Saved|Last saved/).first().waitFor();
    },
  },
];

export { TRANSCRIPT, ANSWER };
