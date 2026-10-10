// Sending clips to a Lens server through its import webhooks (docs/api.md, Import webhooks): a namespace's owners make
// a hook in Lens, and its token is all the clipper needs.

export const PUSH_PATH = "/api/v1/hooks/import";
const MAX_FILES = 20; // the server takes at most 20 files a push

async function explain(res) {
  let detail = "";
  try {
    const body = await res.json();
    detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
  } catch {
    // not JSON
  }
  if (res.status === 401) return "Lens didn't recognise this token: make a new import webhook and paste its token";
  if (res.status === 403) return detail || "this import webhook is paused in Lens";
  if (res.status === 413) return detail || "that's more than this Lens takes at once";
  return detail || `Lens answered ${res.status} ${res.statusText}`;
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
export async function push({ server, token }, { title, markdown, url }, { images = [], captureUrl = false, fetchImpl = fetch } = {}) {
  if (!token) throw new Error("add this Lens's import webhook token in the clipper's options");
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
    res = await fetchImpl(`${server}${PUSH_PATH}`, { method: "POST", headers, body, credentials: "omit" });
  } catch (e) {
    throw new Error(`couldn't reach ${server}: ${e.message}`);
  }
  if (!res.ok) throw new Error(await explain(res));
  return res.json();
}

/** Check an address and token without importing anything: an empty push is refused with 400 once the token is known. */
export async function check({ server, token }, { fetchImpl = fetch } = {}) {
  let res;
  try {
    res = await fetchImpl(`${server}${PUSH_PATH}`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}`, "Content-Type": "application/json", Accept: "application/json" },
      body: "{}",
      credentials: "omit",
    });
  } catch (e) {
    return { ok: false, message: `couldn't reach ${server}: ${e.message}` };
  }
  if (res.status === 400) return { ok: true, message: "Connected: clips will go into this hook's namespace" };
  if (res.status === 404) return { ok: false, message: "that address answers, but not as a Lens (no import webhooks there)" };
  return { ok: false, message: await explain(res) };
}

/** Where a pushed item can be opened in Lens. */
export const itemLink = (server, item) => `${server}/recordings/${item.recording}`;
