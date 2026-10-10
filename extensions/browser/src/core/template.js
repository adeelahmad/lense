// MarkDownload-style templates: {variable}, {date:YYYY-MM-DD}, {title|kebab|lower}. `{{` and `}}` are literal braces.
// Unknown variables render as nothing, so a template never fails on a page that lacks something.

const pad = (n, w = 2) => String(n).padStart(w, "0");

/** Format a date with YYYY YY MM M DD D HH H mm ss Z (local time; Z is the +hh:mm offset). Other text is kept. */
export function formatDate(value, format) {
  const d = value instanceof Date ? value : new Date(value);
  if (Number.isNaN(d.getTime())) return "";
  if (!format) return d.toISOString();
  const off = -d.getTimezoneOffset();
  const tokens = {
    YYYY: () => String(d.getFullYear()),
    YY: () => String(d.getFullYear()).slice(-2),
    MM: () => pad(d.getMonth() + 1),
    M: () => String(d.getMonth() + 1),
    DD: () => pad(d.getDate()),
    D: () => String(d.getDate()),
    HH: () => pad(d.getHours()),
    H: () => String(d.getHours()),
    mm: () => pad(d.getMinutes()),
    ss: () => pad(d.getSeconds()),
    Z: () => `${off >= 0 ? "+" : "-"}${pad(Math.floor(Math.abs(off) / 60))}:${pad(Math.abs(off) % 60)}`,
  };
  return format.replace(/YYYY|YY|MM|M|DD|D|HH|H|mm|ss|Z/g, (t) => tokens[t]());
}

const words = (s) =>
  String(s)
    .normalize("NFKD")
    .replace(/[̀-ͯ]/g, "")
    .replace(/([a-z0-9])([A-Z])/g, "$1 $2")
    .split(/[^A-Za-z0-9]+/)
    .filter(Boolean);

const asText = (v) => (Array.isArray(v) ? v.join(", ") : v == null ? "" : String(v));

export const FILTERS = {
  lower: (v) => asText(v).toLowerCase(),
  upper: (v) => asText(v).toUpperCase(),
  trim: (v) => asText(v).trim(),
  kebab: (v) => words(asText(v)).join("-").toLowerCase(),
  snake: (v) => words(asText(v)).join("_").toLowerCase(),
  camel: (v) =>
    words(asText(v))
      .map((w, i) => (i ? w[0].toUpperCase() + w.slice(1).toLowerCase() : w.toLowerCase()))
      .join(""),
  pascal: (v) =>
    words(asText(v))
      .map((w) => w[0].toUpperCase() + w.slice(1).toLowerCase())
      .join(""),
  title: (v) => asText(v).replace(/\b\p{L}/gu, (c) => c.toUpperCase()),
  // a file name that every OS accepts
  filename: (v) =>
    asText(v)
      .replace(/[\\/:*?"<>|\u0000-\u001f]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 150),
  // a YAML scalar: quoted only when it has to be
  yaml: (v) => {
    const s = asText(v);
    if (s === "") return "";
    return /^[\w][\w .,/()&'-]*$/u.test(s) && !/^(true|false|null|yes|no|on|off|~|\d[\d.:-]*)$/i.test(s) && !/:\s|\s#/.test(s)
      ? s
      : JSON.stringify(s);
  },
  // a YAML flow list: ["a", "b"]
  list: (v) => JSON.stringify((Array.isArray(v) ? v : asText(v) ? [asText(v)] : []).map(String)),
  // tags as #hashtags
  hashtags: (v) =>
    (Array.isArray(v) ? v : asText(v).split(","))
      .map((t) => words(t).join("-"))
      .filter(Boolean)
      .map((t) => `#${t}`)
      .join(" "),
  json: (v) => JSON.stringify(v ?? ""),
  first: (v) => (Array.isArray(v) ? asText(v[0]) : asText(v)),
};

const DATE_VARS = new Set(["date", "published", "modified"]);

function resolve(vars, expr) {
  const [head, ...rest] = expr.split("|");
  const filters = rest.map((f) => f.trim());
  const colon = head.indexOf(":");
  const name = (colon < 0 ? head : head.slice(0, colon)).trim();
  const arg = colon < 0 ? null : head.slice(colon + 1); // kept as typed: {keywords:, } joins with a comma and a space
  let value = vars[name];
  if (DATE_VARS.has(name) && value) value = formatDate(value, arg);
  else if (arg != null && Array.isArray(value)) value = value.join(arg);
  for (const f of filters) {
    const fn = FILTERS[f];
    if (fn) value = fn(value);
  }
  return asText(value);
}

/** Render a template with the clip's variables. */
export function render(template, vars) {
  const out = String(template ?? "")
    .replace(/\{\{/g, "\u0000")
    .replace(/\}\}/g, "\u0001")
    .replace(/\{([^{}\n]+)\}/g, (_, expr) => resolve(vars, expr))
    .replace(/\u0000/g, "{")
    .replace(/\u0001/g, "}");
  return dropEmptyFrontMatter(out);
}

// A front matter line with nothing after its key (`author:`, `author: ""`) is dropped, so pages without an author don't
// leave empty fields behind. A key followed by an indented block (a YAML list) is kept.
function dropEmptyFrontMatter(text) {
  const m = /^---\r?\n([\s\S]*?)\r?\n---(\r?\n|$)/.exec(text);
  if (!m) return text;
  const lines = m[1].split(/\r?\n/);
  const kept = lines.filter((line, i) => {
    if (!/^[\w-]+:\s*(""|'')?\s*$/.test(line)) return true;
    return /^\s+\S/.test(lines[i + 1] ?? "");
  });
  return `---\n${kept.join("\n")}\n---${m[2]}${text.slice(m[0].length)}`;
}

/** The variables a clip offers to templates, shown in the options page. */
export const VARIABLES = {
  title: "the page's (or your edited) title",
  url: "the page's address (its canonical one when it has one)",
  domain: "the site's host name",
  origin: "scheme and host",
  siteName: "the site's name",
  byline: "the author",
  excerpt: "a summary: the page's description or first paragraph",
  published: "when it was published; {published:YYYY-MM-DD}",
  modified: "when it was last changed",
  date: "now; {date:YYYY-MM-DD HH:mm}",
  keywords: "the page's keywords; {keywords:, } joins them",
  tags: "the tags you add",
  image: "the page's lead image",
  lang: "the page's language",
  clipType: "page, selection or element",
  content: "the clipped content as Markdown",
};
