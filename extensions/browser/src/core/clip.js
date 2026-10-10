// Turning what's on the page into a clip: a full page (through Readability), the selection, or one picked element.
import { Readability } from "@mozilla/readability";
import { toMarkdown, prepare } from "./markdown.js";
import { extractMetadata } from "./metadata.js";
import { render } from "./template.js";

function selectionHtml(doc) {
  const sel = doc.getSelection?.();
  if (!sel || sel.isCollapsed || !sel.rangeCount) return "";
  const box = doc.createElement("div");
  for (let i = 0; i < sel.rangeCount; i++) {
    const range = sel.getRangeAt(i);
    // a selection inside a list, table, quote or code block keeps that structure around it
    const holder = range.commonAncestorContainer.nodeType === 1 ? range.commonAncestorContainer : range.commonAncestorContainer.parentNode;
    const wrap = holder?.matches?.("ul, ol") ? holder : holder?.closest?.("pre, code, li, table, blockquote");
    const piece = range.cloneContents();
    if (wrap) {
      const shell = wrap.cloneNode(false);
      shell.append(piece);
      box.append(shell);
    } else box.append(piece);
  }
  return box.innerHTML;
}

function article(doc) {
  const parsed = new Readability(doc.cloneNode(true), { keepClasses: true }).parse();
  return parsed?.content ? parsed : null;
}

/**
 * Clip a document. `mode` is "page", "selection" or "element" (with `element`). Returns
 * {title, markdown, url, images, clipType}: `markdown` is the whole rendered template.
 */
export function clip(doc, { mode = "page", element = null, settings, tags = [], now = new Date() }) {
  const meta = extractMetadata(doc, doc.URL);
  let html = "";
  let clipType = mode;
  if (mode === "selection") {
    html = selectionHtml(doc);
    if (!html) throw new Error("Select some text on the page first");
  } else if (mode === "element") {
    if (!element) throw new Error("Pick something on the page first");
    html = element.outerHTML;
  } else {
    clipType = "page";
    const a = settings.readability ? article(doc) : null;
    if (a) {
      html = a.content;
      meta.title = meta.title || a.title || "";
      meta.byline = meta.byline || a.byline || "";
      meta.excerpt = meta.excerpt || a.excerpt || "";
      meta.siteName = meta.siteName || a.siteName || "";
      meta.published = meta.published || (a.publishedTime ? new Date(a.publishedTime).toISOString() : "");
      meta.lang = meta.lang || a.lang || "";
    } else html = doc.body?.innerHTML ?? "";
  }

  // an inert document: nothing in the clipped HTML runs or loads while it's converted
  const inert = doc.implementation.createHTMLDocument("");
  const box = inert.createElement("div");
  box.innerHTML = html;
  const images = prepare(box, doc.baseURI || meta.url);
  const content = toMarkdown(box.innerHTML, settings.markdown);
  if (!meta.excerpt) meta.excerpt = box.querySelector("p")?.textContent.trim().slice(0, 300) ?? "";

  const vars = { ...meta, content, tags, date: now, clipType };
  const title = render(settings.titleTemplate || "{title}", vars).trim() || meta.title || meta.url;
  const template = (clipType !== "page" && settings.selectionTemplate) || settings.template;
  const markdown = render(template, { ...vars, title }).trim() + "\n";
  return { title, markdown, url: meta.url, images, clipType, image: meta.image };
}
