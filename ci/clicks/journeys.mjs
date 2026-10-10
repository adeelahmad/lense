// What the survey does, in order: each journey is one feature, done the way a person would with the defaults, from
// where they'd usually start. They run one after another on the same fresh Lens, so later ones can use what earlier
// ones made (the admin account, an imported transcript). Keep ids stable: the baseline compares by id.

const INSTALLER = "http://localhost:3990";

/** A page in the primary navigation (the sidebar). */
const nav = (page, label) =>
  page
    .getByRole("navigation", { name: "Primary" })
    .getByRole("link", { name: new RegExp(`^${label}( \\d+)?$`) }) // a count can follow the label (Library 1)
    .first();

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
    async run({ page, meter, base }) {
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
    async run({ page, meter, base, code, installer }) {
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
    async run({ page, meter, base }) {
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
    async run({ page, meter }) {
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
    async run({ page, meter }) {
      await meter.click(page.getByRole("banner").getByRole("link", { name: "Import" }), "Import (top bar)");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Library"), "Library");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Search"), "Search");
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
    async run({ page, meter }) {
      await meter.press("Control+k", "Ctrl+K");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Chat"), "Chat");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Notes"), "Notes");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Topics"), "Topics");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Pipelines"), "Pipelines");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Routines"), "Routines");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Extensions"), "Extensions");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Sensors"), "Sensors");
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
    async run({ page, meter }) {
      await meter.click(nav(page, "Settings"), "Settings");
      await meter.click(page.getByRole("link", { name: "LLM provider" }), "LLM provider");
      await meter.replace(page.getByRole("textbox", { name: "Timeout (seconds)" }), "120", "timeout");
      await meter.press("Control+s", "Ctrl+S to save");
      await page.getByText(/Saved|Last saved/).first().waitFor();
    },
  },
  {
    id: "person-add",
    module: "Admin",
    feature: "Add a person as a viewer",
    from: "/",
    async run({ page, meter }) {
      await meter.click(nav(page, "Admin"), "Admin");
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
    async run({ page, meter }) {
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
    async run({ page, meter }) {
      await meter.click(page.getByRole("button", { name: /^Namespace: / }), "the namespace switcher");
      await meter.click(page.getByRole("option", { name: /^podcasts/ }), "podcasts");
      await page.getByRole("button", { name: /^Namespace: podcasts/ }).waitFor();
    },
  },
];

export { TRANSCRIPT, ANSWER };
