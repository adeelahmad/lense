// HTML to Markdown with Turndown, configured from the clipper's settings (the same knobs MarkDownload offers).
import TurndownService from "turndown";
import { gfm } from "turndown-plugin-gfm";

const DROP = "script, style, noscript, template, link, meta, button, input, select, textarea, form, dialog, svg[aria-hidden='true']";
const LAZY_SRC = ["data-src", "data-lazy-src", "data-original", "data-url", "data-hi-res-src"];

const absolute = (href, base) => {
  try {
    return new URL(href, base).href;
  } catch {
    return href;
  }
};

/** Clean a container of clipped nodes in place: absolute links and images, lazy images loaded, page chrome removed.
 * Returns the image addresses it kept, in order. */
export function prepare(root, base) {
  for (const el of root.querySelectorAll(DROP)) el.remove();
  for (const el of root.querySelectorAll("[hidden], [aria-hidden='true']")) {
    if (!el.querySelector("img, picture")) el.remove();
  }
  for (const pic of root.querySelectorAll("picture")) {
    const img = pic.querySelector("img");
    const source = pic.querySelector("source[srcset]");
    if (img && !img.getAttribute("src") && source) img.setAttribute("src", source.getAttribute("srcset").split(/[\s,]+/)[0]);
    for (const s of pic.querySelectorAll("source")) s.remove();
  }
  const images = [];
  for (const img of root.querySelectorAll("img")) {
    let src = img.getAttribute("src") ?? "";
    const lazy = LAZY_SRC.map((a) => img.getAttribute(a)).find(Boolean);
    if (lazy && (!src || src.startsWith("data:image/gif") || src.startsWith("data:image/svg"))) src = lazy;
    if (!src) {
      const set = img.getAttribute("srcset") || img.getAttribute("data-srcset");
      if (set) src = set.trim().split(/[\s,]+/)[0];
    }
    if (!src) {
      img.remove();
      continue;
    }
    src = src.startsWith("data:") ? src : absolute(src, base);
    img.setAttribute("src", src);
    img.removeAttribute("srcset");
    if (!src.startsWith("data:")) images.push(src);
  }
  for (const a of root.querySelectorAll("a[href]")) {
    const href = a.getAttribute("href");
    if (/^\s*javascript:/i.test(href)) a.removeAttribute("href");
    else if (!href.startsWith("#")) a.setAttribute("href", absolute(href, base));
  }
  return [...new Set(images)];
}

export const MARKDOWN_DEFAULTS = {
  headingStyle: "atx", // or setext
  hr: "---",
  bulletListMarker: "-",
  codeBlockStyle: "fenced", // or indented
  fence: "```",
  emDelimiter: "_",
  strongDelimiter: "**",
  linkStyle: "inlined", // or referenced
  linkReferenceStyle: "full",
  images: "keep", // keep | alt (only their alt text) | drop
  links: "keep", // keep | text (only their text)
  tables: true, // GitHub-flavoured tables, strikethrough and task lists
};

/** A Turndown converter for these settings. */
export function converter(options = {}) {
  const o = { ...MARKDOWN_DEFAULTS, ...options };
  const td = new TurndownService({
    headingStyle: o.headingStyle,
    hr: o.hr,
    bulletListMarker: o.bulletListMarker,
    codeBlockStyle: o.codeBlockStyle,
    fence: o.fence,
    emDelimiter: o.emDelimiter,
    strongDelimiter: o.strongDelimiter,
    linkStyle: o.linkStyle,
    linkReferenceStyle: o.linkReferenceStyle,
  });
  if (o.tables) td.use(gfm);
  td.remove(["script", "style", "noscript", "iframe", "object", "embed"]);

  // fenced code keeps its language (class="language-js", "lang-js", "highlight-source-js")
  if (o.codeBlockStyle === "fenced") {
    td.addRule("fencedWithLanguage", {
      filter: (node) => node.nodeName === "PRE" && node.textContent.trim() !== "",
      replacement: (_content, node) => {
        const code = node.querySelector("code") ?? node;
        const cls = `${code.getAttribute("class") ?? ""} ${node.getAttribute("class") ?? ""} ${node.parentNode?.getAttribute?.("class") ?? ""}`;
        const lang = /(?:language|lang|highlight-source)-([\w+#-]+)/.exec(cls)?.[1] ?? code.getAttribute("data-lang") ?? "";
        const text = code.textContent.replace(/\n$/, "");
        let fence = o.fence;
        while (text.includes(fence)) fence += o.fence[0];
        return `\n\n${fence}${lang}\n${text}\n${fence}\n\n`;
      },
    });
  }
  if (o.images === "drop") td.addRule("imageDrop", { filter: "img", replacement: () => "" });
  if (o.images === "alt") td.addRule("imageAlt", { filter: "img", replacement: (_c, node) => node.getAttribute("alt") || "" });
  if (o.images === "keep") {
    td.addRule("image", {
      filter: "img",
      replacement: (_c, node) => {
        const src = node.getAttribute("src");
        if (!src) return "";
        const alt = (node.getAttribute("alt") || "").replace(/[[\]\n]/g, " ").trim();
        const title = node.getAttribute("title");
        return `![${alt}](${src.replace(/ /g, "%20")}${title ? ` "${title.replace(/"/g, '\\"')}"` : ""})`;
      },
    });
  }
  if (o.links === "text") td.addRule("linkText", { filter: "a", replacement: (content) => content });
  return td;
}

/** HTML (a string) to Markdown. */
export function toMarkdown(html, options) {
  return converter(options)
    .turndown(html)
    .replace(/\n{3,}/g, "\n\n")
    .trim();
}
