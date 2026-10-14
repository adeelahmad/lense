import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";
import { click, looksConsequential, press, refuseTyping, submit, type } from "../src/core/act.js";

const page = (body) => new JSDOM(`<!doctype html><body>${body}</body>`, { url: "https://shop.example.com/" }).window.document;

test("typing sets the value the way a person's typing does, so the page's code sees it", () => {
  const doc = page('<input id="q"><select id="s"><option value="fr">France</option><option value="jp">Japan</option></select>');
  const seen = [];
  doc.getElementById("q").addEventListener("input", (e) => seen.push(["input", e.target.value]));
  doc.getElementById("q").addEventListener("change", (e) => seen.push(["change", e.target.value]));
  assert.equal(type(doc.getElementById("q"), "lens"), "lens");
  assert.deepEqual(seen, [
    ["input", "lens"],
    ["change", "lens"],
  ]);
  assert.equal(type(doc.getElementById("s"), "japan"), "Japan");
  assert.equal(doc.getElementById("s").value, "jp");
  assert.throws(() => type(doc.getElementById("s"), "Peru"), /no option "Peru"; it has France \| Japan/);
});

test("passwords, card numbers, checkboxes and file pickers aren't typed into", () => {
  const doc = page(`
    <input id="pw" type="password"><input id="cc" autocomplete="cc-number"><input id="cvc" name="cvc">
    <input id="box" type="checkbox"><input id="file" type="file"><div id="div">text</div><input id="ok">`);
  const why = (id) => refuseTyping(doc.getElementById(id));
  assert.match(why("pw"), /password field: the person types those themselves/);
  assert.match(why("cc"), /card field/);
  assert.match(why("cvc"), /card field/);
  assert.match(why("box"), /click it instead/);
  assert.match(why("file"), /picks a file/);
  assert.match(why("div"), /isn't a field/);
  assert.equal(why("ok"), null);
});

test("sending, deleting, buying and posting look consequential; searching doesn't", () => {
  const doc = page(`
    <button id="send">Send</button><button id="del" aria-label="Delete conversation">🗑</button>
    <a id="buy" href="/cart">Place order</a><button id="next">Next page</button>
    <form method="post"><input id="comment"><button>Save</button></form>
    <form role="search"><input id="search"><button>Search</button></form>`);
  const looks = (id, what) => looksConsequential(doc.getElementById(id), what);
  assert.equal(looks("send"), true);
  assert.equal(looks("del"), true);
  assert.equal(looks("buy"), true);
  assert.equal(looks("next"), false);
  assert.equal(looks("comment", "submit"), true, "submitting a form that posts");
  assert.equal(looks("comment", "type"), false, "typing alone sends nothing");
  assert.equal(looks("search", "submit"), false);
});

test("a click goes as a person's: pointer and mouse down and up, then the click", () => {
  const doc = page('<button id="b">Go</button>');
  const seen = [];
  for (const t of ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]) doc.getElementById("b").addEventListener(t, () => seen.push(t));
  click(doc.getElementById("b"));
  assert.deepEqual(seen, ["pointerdown", "mousedown", "pointerup", "mouseup", "click"]);
  assert.equal(doc.activeElement.id, "b");
});

test("Enter in a form's field submits it unless the page takes the key; other keys go where the focus is", () => {
  const doc = page('<form id="f"><input id="q"></form><input id="mine">');
  let submitted = 0;
  doc.getElementById("f").addEventListener("submit", (e) => {
    e.preventDefault();
    submitted += 1;
  });
  type(doc.getElementById("q"), "lens");
  submit(doc.getElementById("q"));
  assert.equal(submitted, 1);
  doc.getElementById("q").addEventListener("keydown", (e) => e.key === "Enter" && e.preventDefault());
  submit(doc.getElementById("q"));
  assert.equal(submitted, 1, "the page handled Enter itself");

  const keys = [];
  doc.getElementById("mine").addEventListener("keydown", (e) => keys.push(e.key));
  doc.getElementById("mine").focus();
  assert.equal(press(doc, "Escape"), "Pressed Escape");
  assert.deepEqual(keys, ["Escape"]);
  assert.match(press(doc, "PageDown"), /Scrolled down/);
  assert.throws(() => press(doc, "F12"), /no key "F12"/);
});
