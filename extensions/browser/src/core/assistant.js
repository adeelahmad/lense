// Talking to the Lens assistant from the extension, through the same API as the web app's chat (docs/api.md, chats):
// conversations, questions that stream their answer as server-sent events, stopping, and approvals.
import createDOMPurify from "dompurify";
import { createParser } from "eventsource-parser";
import { marked } from "marked";
import { api, apiJson } from "./account.js";

export const newChat = async (opts) => (await apiJson("/chats", { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}" }, opts)).id;

export const getChat = (cid, opts) => apiJson(`/chats/${cid}`, {}, opts);

export const pendingApprovals = async (cid, opts) =>
  (await apiJson(`/approvals?chat_id=${cid}`, {}, opts)).filter((a) => a.status === "pending");

export const stop = (cid, opts) => apiJson(`/chats/${cid}/stop`, { method: "POST" }, opts);

export const decide = (aid, decision, opts) =>
  apiJson(`/approvals/${aid}`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ decision }) }, opts);

/**
 * Ask in conversation `cid`: `onEvent(name, data)` gets each event as it streams (step, approval, notice, passages,
 * token, error, stopped, done, ...). `browser` is this browser's session, so the assistant can use it. Resolves when
 * the answer is done.
 */
export async function ask(cid, content, { browser, onEvent, signal, ...opts } = {}) {
  const res = await api(
    `/chats/${cid}/messages`,
    {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
      body: JSON.stringify(browser ? { content, browser } : { content }),
      signal,
    },
    opts,
  );
  if (!res.ok) {
    let why = `Lens answered ${res.status}`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") why = body.detail;
    } catch {
      // not JSON
    }
    throw new Error(why);
  }
  await readEvents(res.body, onEvent);
}

/** Feed a server-sent event stream (a fetch body) to `onEvent(name, data)`, data parsed as JSON. */
export async function readEvents(body, onEvent) {
  const parser = createParser({
    onEvent(e) {
      let data = e.data;
      try {
        data = JSON.parse(e.data);
      } catch {
        // plain text
      }
      onEvent(e.event ?? "message", data);
    },
  });
  const reader = body.getReader();
  const text = new TextDecoder();
  for (;;) {
    const { done, value } = await reader.read();
    if (done) break;
    parser.feed(text.decode(value, { stream: true }));
  }
  parser.feed(text.decode());
}

/** Where a cited moment opens in Lens. */
export function passageLink(server, p) {
  const t = typeof p.t0 === "number" && p.t0 > 0 ? `?t=${Math.floor(p.t0 / 1000)}` : "";
  return `${server}/resources/${p.recording_id}${t}`;
}

const escapeAttr = (s) => String(s ?? "").replace(/["\\]/g, "");

/**
 * An answer as HTML, safe to put in the page: Markdown, with each [n] a link to the moment it cites, cleaned of
 * anything that could run or fetch on its own (DOMPurify). Links open in a new tab.
 */
export function answerHtml(text, passages = [], server = "", win = globalThis.window) {
  const byN = new Map(passages.map((p) => [p.n, p]));
  const cite = (n) => {
    const p = byN.get(n);
    if (!p || !server) return `[${n}]`;
    const title = escapeAttr([p.title, p.time].filter(Boolean).join(" · "));
    return `[[${n}]](${passageLink(server, p)} "${title}")`;
  };
  // [1], [1, 2] and [1][2] all cite, as in the web app
  const linked = String(text ?? "").replace(/\[(\d{1,3}(?:\s*,\s*\d{1,3})*)\](?!\()/g, (_all, ns) =>
    ns.split(",").map((n) => cite(Number(n.trim()))).join(""),
  );
  const html = marked.parse(linked, { async: false, gfm: true, breaks: true });
  const purify = createDOMPurify(win);
  const clean = purify.sanitize(html, { USE_PROFILES: { html: true }, FORBID_TAGS: ["img", "style", "form", "input", "button"] });
  const box = win.document.createElement("div");
  box.innerHTML = clean;
  for (const a of box.querySelectorAll("a")) {
    if (!/^https?:/i.test(a.getAttribute("href") ?? "")) {
      a.replaceWith(...a.childNodes);
      continue;
    }
    a.target = "_blank";
    a.rel = "noopener noreferrer";
    if (/^\[\d+\]$/.test(a.textContent)) a.className = "cite";
  }
  return box.innerHTML;
}
