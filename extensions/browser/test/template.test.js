import assert from "node:assert/strict";
import { test } from "node:test";
import { formatDate, render } from "../src/core/template.js";

test("variables, filters and literal braces", () => {
  const out = render("{title|kebab} {{x}} {missing} {tags:, } {title|upper}", { title: "Hello World", tags: ["a", "b"] });
  assert.equal(out, "hello-world {x}  a, b HELLO WORLD");
});

test("dates take a format", () => {
  const d = new Date(2026, 0, 5, 7, 8, 9);
  assert.equal(formatDate(d, "YYYY-MM-DD HH:mm:ss"), "2026-01-05 07:08:09");
  assert.equal(render("{date:D/M/YY}", { date: d }), "5/1/26");
  assert.equal(render("{published:YYYY}", { published: "" }), "");
});

test("empty front matter fields are dropped, lists kept", () => {
  const out = render("---\ntitle: {title|yaml}\nauthor: {byline|yaml}\ntags: {tags|list}\nlist:\n  - one\n---\n\nbody", {
    title: "A: B",
    byline: "",
    tags: ["x"],
  });
  assert.equal(out, '---\ntitle: "A: B"\ntags: ["x"]\nlist:\n  - one\n---\n\nbody');
});

test("yaml filter quotes only what needs it", () => {
  assert.equal(render("{v|yaml}", { v: "Plain title" }), "Plain title");
  assert.equal(render("{v|yaml}", { v: "yes" }), '"yes"');
  assert.equal(render("{v|yaml}", { v: '#tag "q"' }), '"#tag \\"q\\""');
});

test("filename filter", () => {
  assert.equal(render("{t|filename}", { t: 'a/b:c*"d"' }), "a b c d");
});
