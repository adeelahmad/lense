// Acting on a page for the assistant (browser_click, browser_type, browser_press), in the page, as a person would:
// the pointer and key events a click or a key sends, and a field's value set the way frameworks notice. It never
// fills a password or card field, and says which actions look consequential, for the side panel to ask about first.
import { describe, secret } from "./snapshot.js";

// what sends, deletes, buys, books, posts, shares or changes an account, by the words on the control
const CONSEQUENTIAL = new RegExp(
  "\\b(send|delete|remove|trash|discard|archive|erase|pay|buy|purchase|place (?:the |my |your )?order|order now|checkout|" +
    "check out|book|reserve|subscribe|unsubscribe|post|publish|share|tweet|submit|confirm|transfer|donate|sign up|" +
    "register|accept|approve|decline|deactivate|uninstall|cancel (?:my |the |your )?(?:subscription|order|account|booking|plan)|" +
    "close (?:my |the |your )?account)\\b",
  "i",
);
const SCROLL_KEYS = new Set(["PageDown", "PageUp", "Home", "End"]);
const KEYS = {
  Enter: 13, Escape: 27, Tab: 9, Backspace: 8, Delete: 46, Space: 32,
  ArrowDown: 40, ArrowUp: 38, ArrowLeft: 37, ArrowRight: 39,
}; // prettier-ignore

/** Whether doing `what` to `el` looks consequential from its words (or its form's), whatever the model said. */
export function looksConsequential(el, what = "click") {
  const d = describe(el);
  const words = [d.name, el.getAttribute?.("title"), el.value && /^(submit|button)$/.test(el.type) ? el.value : ""].join(" ");
  if (CONSEQUENTIAL.test(words)) return true;
  if (what === "submit" || (what === "click" && el.type === "submit")) {
    const form = el.form ?? el.closest?.("form");
    if (!form || form.getAttribute("role") === "search") return false;
    const buttons = [...form.querySelectorAll("button, input[type=submit]")].map((b) => describe(b).name).join(" ");
    return CONSEQUENTIAL.test(buttons) || (form.method ?? "").toLowerCase() === "post";
  }
  return false;
}

/** Why the assistant may not type into `el`, or null. */
export function refuseTyping(el) {
  const why = secret(el);
  if (why) return `that's ${why}: the person types those themselves`;
  if (el.disabled || el.readOnly) return "that field can't be changed";
  if (el.tagName === "INPUT" && /^(checkbox|radio)$/.test(el.type)) return "that's a checkbox: click it instead";
  if (el.tagName === "INPUT" && el.type === "file") return "that picks a file from the computer, which the person does";
  const typable = el.tagName === "TEXTAREA" || el.tagName === "SELECT" || el.tagName === "INPUT" || el.isContentEditable;
  if (!typable) return "that isn't a field: read the page again and use a field's ref";
  return null;
}

function point(el) {
  el.scrollIntoView?.({ block: "center", inline: "center" });
  const r = el.getBoundingClientRect?.() ?? { left: 0, top: 0, width: 0, height: 0 };
  return { clientX: r.left + r.width / 2, clientY: r.top + r.height / 2 };
}

/** Click as a person does: pointer and mouse down and up, focus, then the click. */
export function click(el) {
  const win = el.ownerDocument.defaultView;
  const at = { bubbles: true, cancelable: true, composed: true, button: 0, view: win, ...point(el) };
  const Pointer = win.PointerEvent ?? win.MouseEvent;
  el.dispatchEvent(new Pointer("pointerdown", { ...at, pointerType: "mouse", isPrimary: true }));
  el.dispatchEvent(new win.MouseEvent("mousedown", at));
  el.focus?.({ preventScroll: true });
  el.dispatchEvent(new Pointer("pointerup", { ...at, pointerType: "mouse", isPrimary: true }));
  el.dispatchEvent(new win.MouseEvent("mouseup", at));
  el.click();
}

function keyEvent(win, type, key) {
  const code = key === "Space" ? "Space" : key.length === 1 ? `Key${key.toUpperCase()}` : key;
  const keyCode = KEYS[key] ?? 0;
  return new win.KeyboardEvent(type, { key: key === "Space" ? " " : key, code, keyCode, which: keyCode, bubbles: true, cancelable: true, composed: true });
}

/** Press a key where the focus is (or on `el`); Enter in a form's field submits it when the page doesn't. */
function sendKey(doc, key, el = doc.activeElement ?? doc.body) {
  const win = doc.defaultView;
  const down = keyEvent(win, "keydown", key);
  const go = el.dispatchEvent(down);
  if (key === "Enter") el.dispatchEvent(keyEvent(win, "keypress", key));
  el.dispatchEvent(keyEvent(win, "keyup", key));
  if (!go) return;
  if (key === "Enter" && el.tagName === "INPUT" && el.form) el.form.requestSubmit();
  else if (key === "Tab") {
    const all = [...doc.querySelectorAll("a[href], button, input, select, textarea, [tabindex]:not([tabindex='-1'])")].filter((e) => !e.disabled);
    all[(all.indexOf(el) + 1) % all.length]?.focus();
  }
}

/** Set a field's value the way a person typing does, so the page's own code sees it: the text, or the option of a
 * list whose label (or value) matches. Returns what it now holds. */
export function type(el, text) {
  const win = el.ownerDocument.defaultView;
  point(el);
  el.focus?.({ preventScroll: true });
  if (el.tagName === "SELECT") {
    const want = String(text).trim().toLowerCase();
    const options = [...el.options];
    const option =
      options.find((o) => o.label.trim().toLowerCase() === want || o.value.toLowerCase() === want) ??
      options.find((o) => o.label.toLowerCase().includes(want));
    if (!option) throw new Error(`no option ${JSON.stringify(text)}; it has ${options.map((o) => o.label.trim()).slice(0, 25).join(" | ")}`);
    el.value = option.value;
    el.dispatchEvent(new win.Event("input", { bubbles: true }));
    el.dispatchEvent(new win.Event("change", { bubbles: true }));
    return option.label.trim();
  }
  if (el.isContentEditable) {
    const doc = el.ownerDocument;
    doc.getSelection()?.selectAllChildren(el);
    // insertText keeps the page's undo and its editors in step; where it isn't there, the text is set
    if (!doc.execCommand?.("insertText", false, text)) {
      el.textContent = text;
      el.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
    }
    return el.textContent;
  }
  // the element's own value setter, not the page's: frameworks that watch the property then see a change
  const proto = el.tagName === "TEXTAREA" ? win.HTMLTextAreaElement.prototype : win.HTMLInputElement.prototype;
  Object.getOwnPropertyDescriptor(proto, "value").set.call(el, text);
  el.dispatchEvent(new win.InputEvent("input", { bubbles: true, inputType: "insertText", data: text }));
  el.dispatchEvent(new win.Event("change", { bubbles: true }));
  return el.value;
}

/** Press Enter in `el`, after typing: submits its form unless the page handles the key itself. */
export function submit(el) {
  sendKey(el.ownerDocument, "Enter", el);
}

function scroller(doc) {
  // the focused thing's nearest scrolling box (a mail list, say), else the page
  for (let n = doc.activeElement; n && n !== doc.body; n = n.parentElement) {
    const s = doc.defaultView.getComputedStyle(n);
    if (/(auto|scroll)/.test(s.overflowY) && n.scrollHeight > n.clientHeight) return n;
  }
  return doc.scrollingElement ?? doc.documentElement;
}

/** Press a key (Enter, Escape, Tab, arrows...) or scroll (PageDown, PageUp, Home, End). */
export function press(doc, key) {
  key = String(key ?? "").trim();
  if (SCROLL_KEYS.has(key)) {
    const box = scroller(doc);
    const page = Math.max(200, (box.clientHeight || doc.defaultView.innerHeight) * 0.85);
    const top = { PageDown: box.scrollTop + page, PageUp: box.scrollTop - page, Home: 0, End: box.scrollHeight }[key];
    if (typeof box.scrollTo === "function") box.scrollTo({ top });
    else box.scrollTop = top;
    return `Scrolled ${{ PageDown: "down", PageUp: "up", Home: "to the top", End: "to the end" }[key]}`;
  }
  if (!(key in KEYS) && !/^[a-z0-9]$/i.test(key)) {
    throw new Error(`no key ${JSON.stringify(key)}: press Enter, Escape, Tab, Backspace, Delete, Space, an arrow, or scroll with PageDown, PageUp, Home or End`);
  }
  sendKey(doc, key);
  return `Pressed ${key}`;
}
