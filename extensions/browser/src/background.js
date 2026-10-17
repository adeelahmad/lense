// The extension's background: context menus, shortcuts, and every push to Lens (so a send finishes even when the
// popup closes). The assistant lives in the side panel (ui/sidepanel.js); this only opens it.
import * as account from "./core/account.js";
import { fetchImages, push, pushLink } from "./core/lens.js";
import * as settingsStore from "./core/settings.js";

const ext = globalThis.browser ?? globalThis.chrome;

const MENU = [
  { id: "clip-page", title: "Clip this page to Lens", contexts: ["page"] },
  { id: "clip-selection", title: "Clip selection to Lens", contexts: ["selection"] },
  { id: "pick", title: "Pick part of the page to clip…", contexts: ["page", "selection"] },
  { id: "clip-link", title: "Send this link to Lens", contexts: ["link"] },
  { id: "clip-image", title: "Send this image to Lens", contexts: ["image"] },
  { id: "open-assistant", title: "Ask Lens…", contexts: ["page", "selection", "link", "image"] },
];

ext.runtime.onInstalled.addListener(async ({ reason }) => {
  await ext.contextMenus.removeAll();
  for (const m of MENU) ext.contextMenus.create(m);
  if (reason === "install") {
    const s = await settingsStore.load();
    if (!s.destinations.length) ext.runtime.openOptionsPage();
  }
});

// ---------- the assistant ----------
// Called straight from the person's click or shortcut (browsers open a side panel only then): beside the page where
// the browser has a side panel or sidebar, else in a tab of its own.
function openAssistant(tab) {
  if (ext.sidePanel?.open && tab?.windowId != null) return ext.sidePanel.open({ windowId: tab.windowId });
  if (ext.sidebarAction?.open) return ext.sidebarAction.open();
  return ext.tabs.create({ url: ext.runtime.getURL("sidepanel.html") });
}

// ---------- running the clipper in a tab ----------
async function inject(tabId) {
  await ext.scripting.executeScript({ target: { tabId }, files: ["content.js"] });
}

async function clipTab(tabId, mode, tags) {
  const settings = await settingsStore.load();
  await inject(tabId);
  const [{ result }] = await ext.scripting.executeScript({
    target: { tabId },
    func: (args) => globalThis.__lensClipper.run(args),
    args: [{ mode, settings, tags: tags ?? settings.tags }],
  });
  if (!result?.ok) throw new Error(result?.error ?? "couldn't read this page");
  return result.clip;
}

async function startPicker(tabId, intent) {
  const settings = await settingsStore.load();
  await inject(tabId);
  // not awaited: the pick comes back as a "picked" message
  ext.scripting
    .executeScript({
      target: { tabId },
      func: (args) => globalThis.__lensClipper.pick(args),
      args: [{ settings, tags: settings.tags, intent }],
    })
    .catch((e) => flag(tabId, false, e.message));
}

// ---------- sending ----------
const signInAgain = (dest) => new Error(`sign in to ${new URL(dest.server).host} again in the clipper's options`);

// The person's sign-in for a signed-in destination (renewed first when it's about to run out), else the token kept on
// this device for it.
async function credentials(dest, namespace) {
  const where = { server: dest.server, namespace: settingsStore.asPerson(dest) ? (namespace || dest.namespace) || undefined : undefined };
  if (dest.kind !== "signin") return { ...where, token: await settingsStore.token(dest.id) };
  if ((await account.load())?.server !== dest.server) throw signInAgain(dest);
  try {
    return { ...where, token: await account.accessToken() };
  } catch (e) {
    throw e instanceof account.SignedOut ? signInAgain(dest) : e;
  }
}

/** `fn` with a destination's credentials; a signed-in one renews its sign-in once when Lens says it has run out. */
async function withCredentials(dest, namespace, fn) {
  const creds = await credentials(dest, namespace);
  try {
    return await fn(creds);
  } catch (e) {
    if (e.status !== 401 || dest.kind !== "signin") throw e;
    let token;
    try {
      token = await account.accessToken({ force: true });
    } catch (again) {
      throw again instanceof account.SignedOut ? signInAgain(dest) : again;
    }
    return fn({ ...creds, token });
  }
}

function chosen(settings, destinationId) {
  const dest = settingsStore.destination(settings, destinationId);
  if (!dest) throw new Error("Add your Lens in the clipper's options first");
  return dest;
}

async function send(clip, destinationId, namespace) {
  const settings = await settingsStore.load();
  const dest = chosen(settings, destinationId);
  const images = settings.sendImages && clip.images?.length ? await fetchImages(clip.images, { max: settings.maxImages }) : [];
  const answer = await withCredentials(dest, namespace, (creds) => push(creds, clip, { images, captureUrl: settings.captureOnServer }));
  return { ...answer, server: dest.server, destination: dest.name };
}

async function sendLink(url, title) {
  const dest = chosen(await settingsStore.load());
  await withCredentials(dest, undefined, (creds) => pushLink(creds, url, title));
}

// ---------- telling the user ----------
async function flag(tabId, ok, message) {
  const opts = tabId ? { tabId } : {};
  await ext.action.setBadgeBackgroundColor({ ...opts, color: ok ? "#16a34a" : "#dc2626" });
  await ext.action.setBadgeText({ ...opts, text: ok ? "✓" : "!" });
  await ext.action.setTitle({ ...opts, title: ok ? `Lens: ${message}` : `Lens couldn't clip: ${message}` });
  if (ok) setTimeout(() => ext.action.setBadgeText({ ...opts, text: "" }), 4000);
}

// a clip to look over first: kept for the popup, which opens if the browser lets it
async function stage(tabId, clip) {
  await ext.storage.session.set({ pending: { clip, tabId, at: Date.now() } });
  try {
    await ext.action.openPopup();
  } catch {
    await ext.action.setBadgeBackgroundColor({ tabId, color: "#6d5dfc" });
    await ext.action.setBadgeText({ tabId, text: "1" });
    await ext.action.setTitle({ tabId, title: "Lens: your clip is ready, open the clipper to send it" });
  }
}

async function quick(tab, mode) {
  try {
    const clip = await clipTab(tab.id, mode);
    const settings = await settingsStore.load();
    if (settings.reviewQuickClips) return stage(tab.id, clip);
    const answer = await send(clip);
    await flag(tab.id, true, `sent to ${answer.namespace}`);
  } catch (e) {
    await flag(tab.id, false, e.message);
  }
}

ext.contextMenus.onClicked.addListener(async (info, tab) => {
  try {
    if (info.menuItemId === "open-assistant") return await openAssistant(tab);
    if (info.menuItemId === "clip-page") return quick(tab, "page");
    if (info.menuItemId === "clip-selection") return quick(tab, "selection");
    if (info.menuItemId === "pick") return startPicker(tab.id, "quick");
    if (info.menuItemId === "clip-link") {
      await sendLink(info.linkUrl, info.selectionText);
      return flag(tab.id, true, "link sent; Lens captures it");
    }
    if (info.menuItemId === "clip-image") {
      const dest = chosen(await settingsStore.load());
      const images = await fetchImages([info.srcUrl], { max: 1 });
      if (!images.length) throw new Error("couldn't download that image");
      const title = tab.title || info.srcUrl;
      const markdown = `![](${info.srcUrl})\n\nFrom [${title}](${info.pageUrl})\n`;
      await withCredentials(dest, undefined, (creds) => push(creds, { title, markdown, url: info.pageUrl }, { images }));
      return flag(tab.id, true, "image sent");
    }
  } catch (e) {
    await flag(tab?.id, false, e.message);
  }
});

ext.commands.onCommand.addListener(async (command, tab) => {
  if (command === "open-assistant") return openAssistant(tab);
  tab ??= (await ext.tabs.query({ active: true, currentWindow: true }))[0];
  if (!tab) return;
  if (command === "clip-page") return quick(tab, "page");
  if (command === "clip-selection") return quick(tab, "selection");
  if (command === "pick") return startPicker(tab.id, "quick");
});

ext.runtime.onMessage.addListener((msg, sender, reply) => {
  if (msg?.type === "picked") {
    const tabId = sender.tab?.id;
    (async () => {
      if (!msg.ok) return flag(tabId, false, msg.error);
      const settings = await settingsStore.load();
      if (msg.intent === "review" || settings.reviewQuickClips) return stage(tabId, msg.clip);
      const answer = await send(msg.clip);
      await flag(tabId, true, `sent to ${answer.namespace}`);
    })().catch((e) => flag(tabId, false, e.message));
    return false;
  }
  if (msg?.type === "send") {
    send(msg.clip, msg.destination, msg.namespace).then(
      (answer) => reply({ ok: true, answer }),
      (e) => reply({ ok: false, error: e.message }),
    );
    return true; // answers asynchronously
  }
  if (msg?.type === "clip") {
    clipTab(msg.tabId, msg.mode, msg.tags).then(
      (clip) => reply({ ok: true, clip }),
      (e) => reply({ ok: false, error: e.message }),
    );
    return true;
  }
  if (msg?.type === "pick") {
    startPicker(msg.tabId, "review").then(() => reply({ ok: true }));
    return true;
  }
  return false;
});
