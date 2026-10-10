// The side panel's half of the assistant using this browser (docs/browser-assistant.md): a session with Lens, the
// actions Lens sends it, carried out in this window's tabs, and what happened, sent back. What it may do is the
// person's to say: only sites they allowed, their yes before anything consequential, never a password or card field.
// Each action shows in the side panel as it happens.
import { createParser } from "eventsource-parser";
import { api, apiJson, SignedOut } from "./account.js";
import * as settingsStore from "./settings.js";

const ext = globalThis.browser ?? globalThis.chrome;
export const SITES = "agentSites"; // origins the person let the assistant read and act on
export const ENABLED = "agentEnabled"; // false: the side panel doesn't offer this browser to the assistant
const LOAD_MS = 20000; // the longest a page is waited for
const SETTLE_MS = 700; // after a click or a key, for the page to react
const AFTER_CHARS = 6000; // the page as it is after an action
const EXT_TYPES = {
  "application/pdf": "pdf", "text/plain": "txt", "text/markdown": "md", "text/html": "html", "message/rfc822": "eml",
  "application/json": "json", "application/zip": "zip", "image/png": "png", "image/jpeg": "jpg", "image/gif": "gif",
  "image/webp": "webp", "image/tiff": "tiff", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/wav": "wav",
  "video/mp4": "mp4", "video/webm": "webm", "application/msword": "doc", "application/rtf": "rtf",
  "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
  "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
  "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
  "application/vnd.ms-excel": "xls", "application/vnd.ms-powerpoint": "ppt",
  "application/vnd.oasis.opendocument.text": "odt",
}; // prettier-ignore

const sleep = (ms, signal) =>
  new Promise((ok) => {
    const t = setTimeout(ok, ms);
    signal?.addEventListener("abort", () => (clearTimeout(t), ok()), { once: true });
  });
const host = (url) => {
  try {
    return new URL(url).host;
  } catch {
    return "";
  }
};
const shorten = (s, n = 60) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

// ---------- what the person allowed ----------
export async function enabled() {
  return (await ext.storage.local.get(ENABLED))[ENABLED] !== false;
}

export async function setEnabled(on) {
  await ext.storage.local.set({ [ENABLED]: Boolean(on) });
}

export async function sites() {
  return (await ext.storage.local.get(SITES))[SITES] ?? [];
}

export async function allowSite(origin) {
  const all = await sites();
  if (!all.includes(origin)) await ext.storage.local.set({ [SITES]: [...all, origin].sort() });
}

export async function forgetSite(origin) {
  await ext.storage.local.set({ [SITES]: (await sites()).filter((s) => s !== origin) });
}

// ---------- the session ----------
/** What to call this browser in Lens, e.g. "Google Chrome on macOS". */
export function label(nav = globalThis.navigator) {
  const brands = (nav?.userAgentData?.brands ?? []).map((b) => b.brand).filter((b) => !/not.?a.?brand/i.test(b));
  const ua = nav?.userAgent ?? "";
  const name =
    brands.find((b) => b !== "Chromium") ??
    brands[0] ??
    [
      [/Edg\//, "Microsoft Edge"],
      [/Firefox\//, "Firefox"],
      [/Chrome\//, "Chrome"],
      [/Safari\//, "Safari"],
    ].find(([re]) => re.test(ua))?.[1] ??
    "Browser";
  const os = nav?.userAgentData?.platform || "";
  return os ? `${name} on ${os}` : name;
}

export const connect = () =>
  apiJson("/browser/sessions", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ label: label() }) });

/** The side panel is closing: Lens stops offering this browser. Sent with the token in hand, so it goes out even as
 * the page does. */
export function end(sid, account, fetchImpl = (...a) => fetch(...a)) {
  return fetchImpl(`${account.server}/api/v1/browser/sessions/${sid}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${account.accessToken}` },
    credentials: "omit",
    keepalive: true,
  }).catch(() => {});
}

export const answer = (aid, out) =>
  apiJson(`/browser/actions/${aid}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(out) });

/**
 * Listen for the actions Lens sends this session, one `onAction(action)` at a time, until `signal` aborts. A dropped
 * stream is opened again; `onGone(status)` when Lens no longer has the session (404) or turned this off (403).
 */
export async function listen(sid, onAction, { signal, onGone = () => {}, onLive = () => {} } = {}) {
  let wait = 1000;
  let chain = Promise.resolve();
  while (!signal?.aborted) {
    try {
      const res = await api(`/browser/sessions/${sid}/actions`, { headers: { Accept: "text/event-stream" }, signal });
      if (res.status === 404 || res.status === 403) return onGone(res.status);
      if (!res.ok) throw new Error(`Lens answered ${res.status}`);
      onLive(true);
      wait = 1000;
      const parser = createParser({
        onEvent(e) {
          if (e.event !== "action") return;
          const action = JSON.parse(e.data);
          chain = chain.then(() => onAction(action)).catch(() => {});
        },
      });
      const reader = res.body.getReader();
      const text = new TextDecoder();
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        parser.feed(text.decode(value, { stream: true }));
      }
    } catch (e) {
      if (signal?.aborted) return;
      if (e instanceof SignedOut) throw e;
    }
    onLive(false);
    await sleep(wait, signal);
    wait = Math.min(wait * 2, 15000);
  }
}

// ---------- carrying actions out ----------
/**
 * Carries out Lens's actions in one browser window. `ui.ask({kind, text, detail, origins}, action)` asks the person
 * and resolves to their yes or no (for a site, it asks the browser for access to `origins` in the person's own click);
 * `ui.log(action, text)` shows an action starting and returns `done(text, ok)`.
 */
export class Hands {
  constructor({ windowId, ui, fetchImpl = (...a) => fetch(...a) }) {
    this.windowId = windowId;
    this.ui = ui;
    this.fetch = fetchImpl;
  }

  /** Carry out `action` and answer Lens. */
  async run(action) {
    this.action = action;
    const step = this.ui.log(action, this.starting(action));
    let out;
    try {
      const tool = this[`t_${action.tool}`];
      if (typeof tool !== "function") throw new Error(`this browser can't ${action.tool} yet: update the Lens extension`);
      const result = await tool.call(this, { ...(action.args ?? {}) });
      out = { ok: true, result };
      step.done(result?.said ?? this.starting(action), true);
    } catch (e) {
      out = { ok: false, error: e.message };
      step.done(e.message, false);
    }
    this.action = null;
    try {
      await answer(action.id, out);
    } catch {
      // given up on in Lens meanwhile (stopped, or too long): nothing to answer
    }
    return out;
  }

  starting({ tool, args = {} }) {
    return (
      {
        tabs: "Looking at the tabs",
        open: `Opening ${host(args.url) || "a page"}`,
        read: args.find ? `Reading the page for “${shorten(args.find, 40)}”` : "Reading the page",
        click: `Clicking ${args.ref}`,
        type: `Typing into ${args.ref}`,
        press: `Pressing ${args.key}`,
        import: args.page ? "Saving the page" : "Downloading a file",
      }[tool] ?? tool
    );
  }

  // ---- tabs and sites ----
  async tab(tabId) {
    const tab = tabId != null ? await ext.tabs.get(Number(tabId)).catch(() => null) : (await ext.tabs.query({ active: true, windowId: this.windowId }))[0];
    if (!tab) throw new Error(tabId != null ? `there's no tab ${tabId}: see browser_tabs` : "there's no tab open");
    if (tab.url?.startsWith(ext.runtime.getURL(""))) throw new Error("the person is looking at Lens's own panel: say which tab (browser_tabs)");
    return tab;
  }

  origin(tab) {
    if (!tab.url) {
      throw new Error(
        "Lens can't see which site that tab is on: open the address with browser_open, or the person opens the side panel from that tab (Ask Lens)",
      );
    }
    const u = new URL(tab.url);
    if (!/^https?:$/.test(u.protocol)) throw new Error("the browser doesn't let extensions use that page");
    return u.origin;
  }

  async allowed(origin) {
    const granted = ext.permissions?.contains ? await ext.permissions.contains({ origins: [`${origin}/*`] }) : true;
    return granted && (await sites()).includes(origin);
  }

  /** Make sure the person lets the assistant use `origin`: asked once per site, then remembered. */
  async site(origin) {
    if (await this.allowed(origin)) return;
    const yes = await this.ui.ask(
      {
        kind: "site",
        text: `Let Lens read and act on ${new URL(origin).host}?`,
        detail: "Only when you ask it to, while this panel is open. Anything that sends, deletes, buys or posts still asks you first.",
        origins: [`${origin}/*`],
      },
      this.action,
    );
    if (!yes) throw new Error(`the person didn't let Lens use ${new URL(origin).host}`);
    await allowSite(origin);
  }

  async call(tabId, name, ...args) {
    await ext.scripting.executeScript({ target: { tabId }, files: ["agent.js"] });
    const [first] = await ext.scripting.executeScript({
      target: { tabId },
      func: (n, a) => globalThis.__lensAgent[n](...a),
      args: [name, args],
    });
    const out = first?.result;
    if (!out?.ok) throw new Error(out?.error ?? "the page didn't answer");
    return out.result;
  }

  async loaded(tabId) {
    const until = Date.now() + LOAD_MS;
    for (;;) {
      const tab = await ext.tabs.get(tabId);
      if (tab.status === "complete" || Date.now() > until) return tab;
      await sleep(200);
    }
  }

  /** The page as it is now, after an action: read when its site is allowed. */
  async after(tabId, said, chars = AFTER_CHARS) {
    await sleep(SETTLE_MS);
    const tab = await this.loaded(tabId);
    const origin = tab.url ? new URL(tab.url).origin : null;
    if (!origin || !(await this.allowed(origin))) {
      return { said, tab_id: tabId, url: tab.url ?? null, note: "the tab is now on a site Lens hasn't been allowed: browser_read asks the person" };
    }
    return { said, tab_id: tabId, ...(await this.call(tabId, "read", { max_chars: chars })) };
  }

  // ---- the tools (browser_<name>) ----
  async t_tabs() {
    const tabs = await ext.tabs.query({ windowId: this.windowId });
    const allowed = new Set(await sites());
    const out = tabs
      .filter((t) => !t.url?.startsWith(ext.runtime.getURL("")))
      .map((t) => {
        const seen = t.url && /^https?:/.test(t.url) ? new URL(t.url).origin : null;
        if (seen && allowed.has(seen)) return { id: t.id, title: t.title, url: t.url, active: t.active };
        return { id: t.id, site: seen ? host(seen) : null, active: t.active, note: "a site Lens hasn't been allowed to read" };
      });
    return { said: `Looked at ${out.length} tab${out.length === 1 ? "" : "s"}`, tabs: out };
  }

  async t_open({ url, tab_id: tabId, new_tab: newTab }) {
    let u;
    try {
      u = new URL(String(url ?? ""));
    } catch {
      throw new Error("that isn't a web address");
    }
    if (!/^https?:$/.test(u.protocol)) throw new Error("only http and https addresses open");
    // asked before going there: an address can carry what was read elsewhere
    await this.site(u.origin);
    const tab = newTab
      ? await ext.tabs.create({ url: u.href, windowId: this.windowId, active: true })
      : await ext.tabs.update((await this.tab(tabId)).id, { url: u.href, active: true });
    return this.after(tab.id, `Opened ${u.host}`);
  }

  async t_read({ tab_id: tabId, find, max_chars: most }) {
    const tab = await this.tab(tabId);
    await this.site(this.origin(tab));
    const page = await this.call(tab.id, "read", { find, max_chars: most });
    return { said: `Read ${shorten(page.title || host(page.url), 70)}${find ? ` for “${shorten(find, 40)}”` : ""}`, tab_id: tab.id, ...page };
  }

  async confirm(text, detail) {
    if (!(await this.ui.ask({ kind: "action", text, detail }, this.action))) throw new Error("the person said no");
  }

  async t_click({ ref, tab_id: tabId, consequential }) {
    const tab = await this.tab(tabId);
    const origin = this.origin(tab);
    await this.site(origin);
    const what = await this.call(tab.id, "inspect", ref, "click");
    if (what.disabled) throw new Error(`${ref} is disabled`);
    const name = what.name ? `“${shorten(what.name)}”` : `the ${what.role}`;
    if (consequential || what.consequential) await this.confirm(`Click ${name} on ${new URL(origin).host}?`);
    await this.call(tab.id, "click", ref);
    return this.after(tab.id, `Clicked ${name}`);
  }

  async t_type({ ref, text, submit, tab_id: tabId, consequential }) {
    const tab = await this.tab(tabId);
    const origin = this.origin(tab);
    await this.site(origin);
    const what = await this.call(tab.id, "inspect", ref, submit ? "submit" : "type");
    if (what.refused) throw new Error(what.refused);
    const name = what.name ? `“${shorten(what.name)}”` : `the ${what.role}`;
    if (submit && (consequential || what.consequential)) {
      await this.confirm(`Type into ${name} and submit it on ${new URL(origin).host}?`, String(text));
    }
    await this.call(tab.id, "type", ref, String(text), Boolean(submit));
    return this.after(tab.id, `Typed “${shorten(String(text), 40)}” into ${name}${submit ? " and pressed Enter" : ""}`);
  }

  async t_press({ key, tab_id: tabId }) {
    const tab = await this.tab(tabId);
    const origin = this.origin(tab);
    await this.site(origin);
    if (key === "Enter") {
      const focus = await this.call(tab.id, "focused");
      if (focus?.consequential) await this.confirm(`Press Enter in “${shorten(focus.name || focus.role)}” on ${new URL(origin).host}?`);
    }
    const said = await this.call(tab.id, "press", key);
    return this.after(tab.id, said);
  }

  async t_import({ ref, url, tab_id: tabId, page }) {
    let file;
    if (page) {
      const tab = await this.tab(tabId);
      await this.site(this.origin(tab));
      file = await this.savePage(tab);
    } else {
      let address = url;
      if (ref) {
        const tab = await this.tab(tabId);
        await this.site(this.origin(tab));
        address = (await this.call(tab.id, "inspect", ref, "click")).href;
        if (!address) throw new Error(`${ref} isn't a link to a file: give the file's address, or click it and read the page it opens`);
      }
      let u;
      try {
        u = new URL(String(address ?? ""));
      } catch {
        throw new Error("give a link's ref, a file's url, or page: true");
      }
      if (!/^https?:$/.test(u.protocol)) throw new Error("only http and https files download");
      await this.site(u.origin);
      file = await this.download(u.href);
    }
    const up = await upload(file);
    return {
      said: `${page ? "Saved the page as" : "Downloaded"} ${up.filename} (${size(up.size)})`,
      upload_id: up.id,
      filename: up.filename,
      size: up.size,
    };
  }

  async savePage(tab) {
    await ext.scripting.executeScript({ target: { tabId: tab.id }, files: ["content.js"] });
    const settings = await settingsStore.load();
    const [first] = await ext.scripting.executeScript({
      target: { tabId: tab.id },
      func: (args) => globalThis.__lensClipper.run(args),
      args: [{ mode: "page", settings, tags: settings.tags }],
    });
    const out = first?.result;
    if (!out?.ok) throw new Error(out?.error ?? "couldn't read the page");
    const name = `${(out.clip.title || host(tab.url) || "page").replace(/[\\/:*?"<>|\s]+/g, " ").trim().slice(0, 100) || "page"}.md`;
    return new File([out.clip.markdown], name, { type: "text/markdown" });
  }

  /** A file, downloaded as the person (their cookies on that site). */
  async download(href) {
    let res;
    try {
      res = await this.fetch(href, { credentials: "include" });
    } catch (e) {
      throw new Error(`couldn't download it (${e.message}): the file may come from another site, which needs allowing too`);
    }
    if (!res.ok) throw new Error(`the site answered ${res.status} for that file`);
    const blob = await res.blob();
    return new File([blob], fileName(res.headers.get("content-disposition"), res.url || href, blob.type), { type: blob.type });
  }
}

const size = (n) => (n >= 1048576 ? `${(n / 1048576).toFixed(1)} MB` : `${Math.max(1, Math.round(n / 1024))} KB`);

/** A downloaded file's name: from Content-Disposition, else the address, with an extension that says its type. */
export function fileName(disposition, url, type) {
  let name = "";
  const star = /filename\*\s*=\s*(?:UTF-8|utf-8)''([^;]+)/.exec(disposition ?? "");
  const plain = /filename\s*=\s*"?([^";]+)"?/i.exec(disposition ?? "");
  try {
    if (star) name = decodeURIComponent(star[1].trim());
    else if (plain) name = plain[1].trim();
    else name = decodeURIComponent(new URL(url).pathname.split("/").pop() ?? "");
  } catch {
    name = "";
  }
  name = name.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, "-").trim() || "download";
  const want = EXT_TYPES[(type ?? "").split(";")[0].trim().toLowerCase()];
  if (want && !/\.[a-z0-9]{1,5}$/i.test(name)) name = `${name}.${want}`;
  return name.slice(-150);
}

/** Attach a file to the person's conversation: a held upload (POST /uploads with hold), sent in chunks. */
export async function upload(file) {
  const limits = await apiJson("/uploads/limits");
  if (file.size > limits.max_mb * 1048576) throw new Error(`that's ${size(file.size)}; this Lens takes files up to ${limits.max_mb} MB`);
  let up = await apiJson("/uploads", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename: file.name, size: file.size, hold: true }),
  });
  const chunk = Math.max(1, Math.floor((limits.chunk_mb || 8) * 1048576));
  while (up.state === "receiving") {
    up = await apiJson(`/uploads/${up.id}?offset=${up.offset}`, {
      method: "PUT",
      headers: { "Content-Type": "application/octet-stream" },
      body: file.slice(up.offset, up.offset + chunk),
    });
  }
  return up;
}
