import assert from "node:assert/strict";
import { test } from "node:test";
import { clip } from "../src/core/clip.js";
import { extractMetadata } from "../src/core/metadata.js";
import { DEFAULTS } from "../src/core/settings.js";
import { page } from "./fixtures.js";

const settings = structuredClone(DEFAULTS);
const now = new Date("2026-10-10T12:00:00Z");

test("metadata from meta tags", () => {
  const m = extractMetadata(page());
  assert.equal(m.title, "How Lenses Work");
  assert.equal(m.url, "https://example.com/articles/lenses");
  assert.equal(m.domain, "example.com");
  assert.equal(m.siteName, "Example Science");
  assert.equal(m.byline, "Ada Lovelace");
  assert.equal(m.published, "2026-03-04T10:00:00.000Z");
  assert.equal(m.image, "https://example.com/img/lead.jpg");
  assert.deepEqual(m.keywords, ["optics", "lenses"]);
  assert.equal(m.lang, "en");
});

test("metadata from JSON-LD", () => {
  const doc = page(`<html><head><script type="application/ld+json">{"@graph":[{"@type":"NewsArticle","headline":"LD headline",
    "author":[{"name":"Grace"}],"datePublished":"2025-01-02","image":{"url":"https://cdn.example/i.png"}}]}</script></head><body></body></html>`);
  const m = extractMetadata(doc);
  assert.equal(m.title, "LD headline");
  assert.equal(m.byline, "Grace");
  assert.equal(m.image, "https://cdn.example/i.png");
});

test("a full page becomes its article, with front matter", () => {
  const c = clip(page(), { mode: "page", settings, tags: ["reading"], now });
  assert.equal(c.title, "How Lenses Work");
  assert.equal(c.clipType, "page");
  assert.match(c.markdown, /^---\ntitle: How Lenses Work\nsource: https:\/\/example.com\/articles\/lenses\nauthor: Ada Lovelace\n/);
  assert.match(c.markdown, /published: 2026-03-04\n/);
  assert.match(c.markdown, /tags: \["reading"\]/);
  assert.match(c.markdown, /\[the glossary\]\(https:\/\/example.com\/glossary#focal\)/);
  assert.match(c.markdown, /!\[A ray diagram\]\(https:\/\/example.com\/img\/diagram.png\)/);
  assert.match(c.markdown, /```python\nprint\("focus"\)\n```/);
  assert.match(c.markdown, /\| Lens \| Focal \|/);
  assert.doesNotMatch(c.markdown, /javascript:|window.evil|Copyright footer|About\]/);
  assert.deepEqual(c.images, ["https://example.com/img/diagram.png"]);
});

test("without Readability the whole body is kept", () => {
  const c = clip(page(), { mode: "page", settings: { ...settings, readability: false }, now });
  assert.match(c.markdown, /Copyright footer/);
});

test("a picked element, with the selection template", () => {
  const doc = page();
  const c = clip(doc, {
    mode: "element",
    element: doc.querySelector("table"),
    settings: { ...settings, selectionTemplate: "> from {domain}\n\n{content}" },
    now,
  });
  assert.equal(c.markdown, "> from example.com\n\n| Lens | Focal |\n| --- | --- |\n| A | 50mm |\n");
});

test("the selection", () => {
  const doc = page();
  const range = doc.createRange();
  range.selectNodeContents(doc.querySelector("article p"));
  doc.getSelection().addRange(range);
  const c = clip(doc, { mode: "selection", settings: { ...settings, selectionTemplate: "{content}" }, now });
  assert.match(c.markdown, /^A lens bends light\./);
});

test("no selection is an error the popup can show", () => {
  assert.throws(() => clip(page(), { mode: "selection", settings, now }), /Select some text/);
});

test("markdown options apply", () => {
  const md = { ...settings.markdown, images: "drop", links: "text", bulletListMarker: "*" };
  const doc = page(`<html><body><ul><li><a href="/x">one</a></li></ul><img src="/a.png"></body></html>`);
  const c = clip(doc, { mode: "element", element: doc.body, settings: { ...settings, markdown: md, selectionTemplate: "{content}" }, now });
  assert.equal(c.markdown, "*   one\n");
});
