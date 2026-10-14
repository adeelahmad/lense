// What the assistant sees of a page (browser_read): its text in reading order, with each link, button and field
// numbered (e12) so it can act on them. Runs in the page, in the extension's own world: the page can't see the numbers
// or change what they point at. Names and roles come from the page's accessibility tree, the way a screen reader
// reads it (dom-accessibility-api). Password and card fields show no value.
import { computeAccessibleName, getRole } from "dom-accessibility-api";

export const READ_CHARS = 10000; // a read's text, unless asked for more or less
export const MOST_CHARS = 40000;

const ROLES = [
  "button", "link", "checkbox", "radio", "tab", "menuitem", "menuitemcheckbox", "menuitemradio", "option", "switch",
  "combobox", "textbox", "searchbox", "treeitem", "slider", "spinbutton",
]; // prettier-ignore
const INTERACTIVE = [
  "a[href]", "button", "input:not([type=hidden])", "select", "textarea", "summary", "[contenteditable='']",
  "[contenteditable='true']", ...ROLES.map((r) => `[role=${r}]`),
].join(","); // prettier-ignore
const SKIP = new Set(["SCRIPT", "STYLE", "NOSCRIPT", "TEMPLATE", "HEAD", "META", "LINK", "IFRAME", "OBJECT", "EMBED", "CANVAS"]);
const BLOCK = new Set([
  "P", "DIV", "SECTION", "ARTICLE", "MAIN", "HEADER", "FOOTER", "NAV", "ASIDE", "LI", "UL", "OL", "TR", "TABLE",
  "BLOCKQUOTE", "PRE", "FORM", "FIELDSET", "DL", "DT", "DD", "FIGURE", "FIGCAPTION", "BR", "HR", "H1", "H2", "H3",
  "H4", "H5", "H6", "DETAILS", "DIALOG", "ADDRESS", "LEGEND", "CAPTION", "TBODY", "THEAD", "TFOOT",
]); // prettier-ignore
const CARD = /\bcc-|credit.?card|card.?num|\bcvc\b|\bcvv\b|\bcsc\b|security.?code/i;

const clean = (s) => String(s ?? "").replace(/\s+/g, " ").trim();
const cut = (s, n) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);
const quote = (s) => JSON.stringify(s);

/** The numbers a document's elements have had, kept for the life of the page. */
export function newRefs() {
  return { next: 1, byRef: new Map(), byEl: new WeakMap() };
}

function refOf(refs, el) {
  let ref = refs.byEl.get(el);
  if (!ref) {
    ref = `e${refs.next++}`;
    refs.byEl.set(el, ref);
    refs.byRef.set(ref, new WeakRef(el));
  }
  return ref;
}

/** The element a ref names, while it's still on the page. */
export function element(refs, ref) {
  const el = refs.byRef.get(String(ref ?? "").trim())?.deref();
  return el?.isConnected ? el : null;
}

function hidden(el) {
  if (el.hidden || el.getAttribute("aria-hidden") === "true" || el.inert) return true;
  if (typeof el.checkVisibility === "function") return !el.checkVisibility({ visibilityProperty: true });
  const style = el.ownerDocument.defaultView?.getComputedStyle?.(el);
  return style ? style.display === "none" || style.visibility === "hidden" : false;
}

/** A password or card field, which the assistant never fills (and never sees the value of): why, or null. */
export function secret(el) {
  if (el.tagName === "INPUT" && el.type === "password") return "a password field";
  const auto = el.getAttribute?.("autocomplete") ?? "";
  if (/current-password|new-password|one-time-code/.test(auto)) return "a password field";
  const hints = [auto, el.getAttribute?.("name"), el.id, el.getAttribute?.("aria-label"), el.getAttribute?.("placeholder")].join(" ");
  if (CARD.test(hints)) return "a card field";
  return null;
}

/** An element as the assistant reads it: role, name and state, e.g. textbox "To" = "ada@example.com". */
export function describe(el) {
  const tag = el.tagName;
  let role = getRole(el) || tag.toLowerCase();
  if (tag === "INPUT" && el.type === "password") role = "password field";
  const name = cut(clean(computeAccessibleName(el)), 150);
  const out = { role, name };
  if (tag === "A" && el.href && /^https?:/i.test(el.href)) out.href = el.href;
  if (tag === "SELECT") {
    out.value = clean(el.selectedOptions?.[0]?.label ?? "");
    out.options = [...el.options].slice(0, 25).map((o) => clean(o.label));
  } else if (tag === "INPUT" && /^(checkbox|radio)$/.test(el.type)) {
    out.checked = el.checked;
  } else if (/^(checkbox|radio|switch|menuitemcheckbox|menuitemradio)$/.test(role)) {
    out.checked = el.getAttribute("aria-checked") === "true";
  } else if ((tag === "INPUT" && !/^(button|submit|reset|image|file|range|color)$/.test(el.type)) || tag === "TEXTAREA") {
    out.value = secret(el) ? null : cut(clean(el.value), 300);
  } else if (el.isContentEditable) {
    out.value = cut(clean(el.innerText ?? el.textContent), 300);
  }
  if (el.disabled || el.getAttribute("aria-disabled") === "true") out.disabled = true;
  if (el.getAttribute("aria-expanded")) out.expanded = el.getAttribute("aria-expanded") === "true";
  if (el.getAttribute("aria-selected") === "true") out.selected = true;
  return out;
}

function line(ref, d) {
  let s = `[${ref}] ${d.role}${d.name ? ` ${quote(d.name)}` : ""}`;
  if (d.href) s += ` → ${cut(d.href, 200)}`;
  if (d.value === null) s += " (the person types this)";
  else if (d.value !== undefined && d.value !== "") s += ` = ${quote(d.value)}`;
  if (d.options) s += ` (options: ${d.options.join(" | ")})`;
  if (d.checked !== undefined) s += d.checked ? " (checked)" : " (not checked)";
  if (d.expanded !== undefined) s += d.expanded ? " (open)" : " (closed)";
  if (d.selected) s += " (selected)";
  if (d.disabled) s += " (disabled)";
  return `${s} `;
}

/** The page as lines of text, interactive elements numbered in place. */
function lines(doc, refs) {
  const out = [];
  let current = "";
  const flush = () => {
    const t = clean(current);
    if (t) out.push(t);
    current = "";
  };
  // what's drawn inside a node: an element's shadow tree in place of its children, a slot's assigned nodes
  const inside = (node) => {
    if (node.shadowRoot) return node.shadowRoot.childNodes;
    if (node.tagName === "SLOT") {
      const assigned = node.assignedNodes?.({ flatten: true }) ?? [];
      if (assigned.length) return assigned;
    }
    return node.childNodes;
  };
  const walk = (node) => {
    for (const n of inside(node)) {
      if (n.nodeType === 3) {
        current += n.nodeValue;
        continue;
      }
      if (n.nodeType !== 1 || SKIP.has(n.tagName) || n.tagName === "svg" || hidden(n)) continue;
      const block = BLOCK.has(n.tagName);
      if (block) flush();
      if (/^H[1-6]$/.test(n.tagName)) current += `${"#".repeat(Number(n.tagName[1]))} `;
      else if (n.tagName === "LI") current += "- ";
      if (n.matches(INTERACTIVE)) {
        current += line(refOf(refs, n), describe(n));
        if (n.tagName === "SELECT" || n.tagName === "TEXTAREA") continue;
        // a link or button speaks for what's inside it, unless it holds more of the page
        if (!n.querySelector(INTERACTIVE) && (n.textContent ?? "").length < 300) continue;
      }
      if (n.tagName === "TD" || n.tagName === "TH") current += " | ";
      walk(n);
      if (n.tagName === "IMG" && n.alt) current += ` [image: ${n.alt}] `;
      if (block) flush();
    }
  };
  walk(doc.body ?? doc.documentElement);
  flush();
  return out;
}

/** Only the lines that mention one of these words, with the line around each. */
function found(all, words) {
  const terms = clean(words).toLowerCase().split(" ").filter(Boolean);
  const keep = new Set();
  all.forEach((l, i) => {
    const low = l.toLowerCase();
    if (terms.some((t) => low.includes(t))) for (const j of [i - 1, i, i + 1]) if (j >= 0 && j < all.length) keep.add(j);
  });
  const out = [];
  let last = -2;
  for (const i of [...keep].sort((a, b) => a - b)) {
    if (i !== last + 1 && out.length) out.push("…");
    out.push(all[i]);
    last = i;
  }
  return out;
}

/** Read the page: {url, title, text, cut_short?}. `find` keeps only what mentions its words. */
export function read(doc, refs, { find = "", max_chars: most = READ_CHARS } = {}) {
  const limit = Math.max(500, Math.min(Number(most) || READ_CHARS, MOST_CHARS));
  let all = lines(doc, refs);
  if (clean(find)) {
    all = found(all, find);
    if (!all.length) all = [`Nothing on this page mentions ${quote(clean(find))}.`];
  }
  let text = all.join("\n");
  const page = { url: doc.URL, title: clean(doc.title) };
  if (text.length > limit) {
    text = `${text.slice(0, limit)}\n…`;
    page.cut_short = "the page goes on: read with find for the part you need, or a larger max_chars";
  }
  return { ...page, text };
}
