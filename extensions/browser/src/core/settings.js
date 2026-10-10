// The clipper's settings. Everything but the tokens syncs across the user's browsers; tokens stay on this device.
import { MARKDOWN_DEFAULTS } from "./markdown.js";

export const DEFAULT_TEMPLATE = `---
title: {title|yaml}
source: {url}
author: {byline|yaml}
site: {siteName|yaml}
published: {published:YYYY-MM-DD}
clipped: {date:YYYY-MM-DDTHH:mm:ssZ}
image: {image}
tags: {tags|list}
---

# {title}

{content}
`;

export const DEFAULTS = {
  destinations: [], // [{id, name, server}]; each one's token is in local storage under token:<id>
  defaultDestination: "",
  template: DEFAULT_TEMPLATE,
  titleTemplate: "{title}",
  selectionTemplate: "", // blank: the same template as pages
  markdown: { ...MARKDOWN_DEFAULTS },
  readability: true, // full pages: keep the article, not the menus and footers
  sendImages: false, // also push the content's images as files (they become resources in Lens)
  maxImages: 10,
  captureOnServer: false, // also ask Lens to capture the page itself (needs Chromium on the server)
  reviewQuickClips: false, // context menu and shortcut clips open the popup instead of sending straight away
  tags: [],
};

const ext = globalThis.browser ?? globalThis.chrome;

export async function load() {
  const stored = await ext.storage.sync.get(Object.keys(DEFAULTS));
  const s = { ...structuredClone(DEFAULTS), ...stored };
  s.markdown = { ...MARKDOWN_DEFAULTS, ...(stored.markdown ?? {}) };
  return s;
}

export async function save(patch) {
  await ext.storage.sync.set(patch);
}

export async function token(id) {
  const key = `token:${id}`;
  return (await ext.storage.local.get(key))[key] ?? "";
}

export async function setToken(id, value) {
  if (value) await ext.storage.local.set({ [`token:${id}`]: value });
  else await ext.storage.local.remove(`token:${id}`);
}

export function destination(settings, id) {
  const all = settings.destinations ?? [];
  return all.find((d) => d.id === (id || settings.defaultDestination)) ?? all[0] ?? null;
}

// plain http is fine on this computer or the local network, nowhere else
export const isLocal = (host) =>
  host === "localhost" ||
  host === "[::1]" ||
  host.endsWith(".local") ||
  host.endsWith(".lan") ||
  /^(127|10)\.\d+\.\d+\.\d+$/.test(host) ||
  /^192\.168\.\d+\.\d+$/.test(host) ||
  /^172\.(1[6-9]|2\d|3[01])\.\d+\.\d+$/.test(host);

/** A server's address as its origin plus any base path, without a trailing slash. */
export function normalizeServer(input) {
  let s = String(input ?? "").trim();
  if (!s) throw new Error("enter your Lens address");
  if (!/^https?:\/\//i.test(s)) s = `https://${s}`;
  const u = new URL(s);
  if (u.protocol === "http:" && !isLocal(u.hostname)) {
    throw new Error("use https:// for a Lens that isn't on this computer or network");
  }
  return `${u.origin}${u.pathname.replace(/\/+$/, "")}`;
}
