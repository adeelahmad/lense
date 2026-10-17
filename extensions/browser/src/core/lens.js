// Sending clips to a Lens server's push address (docs/api.md, Import webhooks), with any of the tokens it takes: the
// person's own sign-in (account.js), an API token they made (la_...), or an import webhook's token (lih_...). With the
// person's own, the clip goes into the namespace they picked; a hook's goes into the hook's namespace.

export const PUSH_PATH = "/api/v1/hooks/import";
const MAX_FILES = 20; // the server takes at most 20 files a push
const OLD_HOOKS_ONLY = "that token isn't an import hook's"; // what a Lens from before personal tokens says

/** What kind of token this is, by its prefix: "hook", "key" (an API token) or "" (a sign-in, or not one of Lens's). */
export const tokenKind = (token) => (/^lih_/.test(token ?? "") ? "hook" : /^la_/.test(token ?? "") ? "key" : "");

const UNKNOWN = {
  hook: "Lens doesn't know this import webhook token: it was replaced or deleted. Sign in instead, or paste a new one",
  key: "Lens doesn't know this API token: it was revoked, ran out, or is from another Lens. Sign in instead, or make a new one",
  "": "Lens didn't recognise this sign-in: sign in again from the clipper's options",
};

async function explain(res, token) {
  let detail = "";
  try {
    const body = await res.json();
    detail = body.detail == null ? "" : typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
  } catch {
    // not JSON
  }
  if (res.status === 401) {
    if (detail === OLD_HOOKS_ONLY && tokenKind(token) !== "hook") {
      return "this Lens takes only import webhook tokens: update Lens to clip with your API token or by signing in";
    }
    return detail && detail !== OLD_HOOKS_ONLY ? detail : UNKNOWN[tokenKind(token)];
  }
  if (res.status === 403) return detail || "this import webhook is paused in Lens";
  if (res.status === 413) return detail || "that's more than this Lens takes at once";
  return detail || `Lens answered ${res.status} ${res.statusText}`;
}

const failed = async (res, token) => Object.assign(new Error(await explain(res, token)), { status: res.status });

/** The push address, with the namespace when the token is the person's own. */
export function pushUrl(server, namespace) {
  return `${server}${PUSH_PATH}${namespace ? `?namespace=${encodeURIComponent(namespace)}` : ""}`;
}

const filenameFor = (src, i, type) => {
  let name = "";
  try {
    name = decodeURIComponent(new URL(src).pathname.split("/").pop() ?? "");
  } catch {
    // keep the fallback
  }
  name = name.replace(/[^\w.-]+/g, "-").slice(-80);
  if (!/\.(png|jpe?g|gif|webp|avif|svg|bmp|tiff?)$/i.test(name)) {
    const ext = { "image/png": "png", "image/gif": "gif", "image/webp": "webp", "image/svg+xml": "svg", "image/avif": "avif" }[type] ?? "jpg";
    name = `image-${i + 1}.${ext}`;
  }
  return name;
};

/** Fetch the clip's images, as many as fit, skipping any that fail. */
export async function fetchImages(urls, { max = 10, fetchImpl = fetch } = {}) {
  const out = [];
  for (const [i, src] of urls.slice(0, Math.min(max, MAX_FILES - 1)).entries()) {
    try {
      const res = await fetchImpl(src, { credentials: "omit" });
      if (!res.ok) continue;
      const blob = await res.blob();
      if (!blob.type.startsWith("image/")) continue;
      out.push({ name: filenameFor(src, i, blob.type), blob });
    } catch {
      // an image that can't be fetched is left as a link in the Markdown
    }
  }
  return out;
}

/**
 * Push a clip: its Markdown as text (Lens imports it like pasted text), plus, when asked, its images as files and its
 * address for Lens to capture itself. Resolves to the server's answer: {namespace, items: [{kind, name, recording}]}.
 */
export async function push({ server, token, namespace }, { title, markdown, url }, { images = [], captureUrl = false, fetchImpl = fetch } = {}) {
  if (!token) throw new Error("sign in to this Lens in the clipper's options");
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  let body;
  if (!images.length && !captureUrl) {
    headers["Content-Type"] = "application/json";
    body = JSON.stringify({ text: markdown, title: title.slice(0, 200) });
  } else {
    body = new FormData();
    body.append("text", markdown);
    body.append("title", title.slice(0, 200));
    if (captureUrl && /^https?:/.test(url)) body.append("url", url);
    for (const img of images) body.append("file", img.blob, img.name);
  }
  let res;
  try {
    res = await fetchImpl(pushUrl(server, namespace), { method: "POST", headers, body, credentials: "omit" });
  } catch (e) {
    throw new Error(`couldn't reach ${server}: ${e.message}`);
  }
  if (!res.ok) throw await failed(res, token);
  return res.json();
}

/** Send a web address for Lens to capture itself (a PDF as it is, a page printed by Chromium on the full image). */
export async function pushLink({ server, token, namespace }, url, title, { fetchImpl = fetch } = {}) {
  if (!token) throw new Error("sign in to this Lens in the clipper's options");
  let res;
  try {
    res = await fetchImpl(pushUrl(server, namespace), {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify(title ? { url, title: title.slice(0, 200) } : { url }),
      credentials: "omit",
    });
  } catch (e) {
    throw new Error(`couldn't reach ${server}: ${e.message}`);
  }
  if (!res.ok) throw await failed(res, token);
  return res.json();
}

/**
 * Check an address and token without importing anything. `target` is where clips go: {kind: "hook" | "person",
 * namespace, namespaces, email, hook, write}. A Lens from before personal tokens has no GET there (405): an empty push
 * is refused with 400 once its hook token is known.
 */
export async function check({ server, token }, { fetchImpl = fetch } = {}) {
  const headers = { Authorization: `Bearer ${token}`, Accept: "application/json" };
  let res;
  try {
    res = await fetchImpl(`${server}${PUSH_PATH}`, { headers, credentials: "omit" });
    if (res.status === 405) {
      res = await fetchImpl(`${server}${PUSH_PATH}`, {
        method: "POST",
        headers: { ...headers, "Content-Type": "application/json" },
        body: "{}",
        credentials: "omit",
      });
      if (res.status === 400) return { ok: true, message: "Connected: clips will go into this hook's namespace", target: { kind: "hook" } };
    }
  } catch (e) {
    return { ok: false, message: `couldn't reach ${server}: ${e.message}` };
  }
  if (res.status === 404) return { ok: false, message: "that address answers, but not as a Lens (no import webhooks there)" };
  if (!res.ok) return { ok: false, message: await explain(res, token) };
  const target = await res.json();
  if (target.kind === "hook") return { ok: true, message: `Connected: clips go into ${target.namespace}`, target };
  if (!target.write) return { ok: false, message: "this API token is read-only: make one with write access in Lens, Settings → API keys", target };
  if (!target.namespaces?.length) return { ok: false, message: "you can't add anything to a namespace in this Lens yet: ask an owner for the editor role", target };
  const into = target.namespaces.length > 1 ? `${target.namespace ?? target.namespaces[0]} (or pick another)` : target.namespaces[0];
  return { ok: true, message: `Connected${target.email ? ` as ${target.email}` : ""}: clips go into ${into}`, target };
}

/** Where a pushed item can be opened in Lens. */
export const itemLink = (server, item) => `${server}/recordings/${item.recording}`;
