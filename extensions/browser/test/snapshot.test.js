import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";
import { element, newRefs, read } from "../src/core/snapshot.js";

const page = (body) => new JSDOM(`<!doctype html><html><head><title>Inbox (3)</title></head><body>${body}</body></html>`, { url: "https://mail.example.com/inbox" }).window.document;

const MAIL = `
<h1>Inbox</h1>
<nav><a href="/sent">Sent</a> <a href="javascript:void(0)">Settings</a></nav>
<table><tr><td>Ada</td><td><a href="/m/1">Invoice for March</a></td><td><a href="/att/1/invoice.pdf">invoice.pdf</a></td></tr></table>
<p>Hello <b>there</b>, this is the page.</p>
<div style="display:none">secret hidden text <button>Hidden button</button></div>
<div aria-hidden="true">also hidden</div>
<form method="post">
  <label>To <input name="to" value="ada@example.com"></label>
  <label>Password <input type="password" value="hunter2"></label>
  <label>Card number <input name="cardnumber" value="4111111111111111"></label>
  <label>Country <select><option>France</option><option selected>Japan</option></select></label>
  <label><input type="checkbox" checked> Remember me</label>
  <textarea aria-label="Message">See you</textarea>
  <button>Send</button>
</form>`;

test("a page reads as text in order, with its links, buttons and fields numbered", () => {
  const doc = page(MAIL);
  const refs = newRefs();
  const out = read(doc, refs);
  assert.equal(out.title, "Inbox (3)");
  assert.equal(out.url, "https://mail.example.com/inbox");
  const text = out.text;
  assert.match(text, /^# Inbox$/m);
  assert.match(text, /\[e\d+\] link "Invoice for March" → https:\/\/mail\.example\.com\/m\/1/);
  assert.match(text, /\[e\d+\] link "invoice\.pdf" → https:\/\/mail\.example\.com\/att\/1\/invoice\.pdf/);
  assert.match(text, /\[e\d+\] link "Settings"(?! →)/, "a javascript: link has no address");
  assert.match(text, /Hello there, this is the page\./);
  assert.match(text, /\[e\d+\] textbox "To" = "ada@example\.com"/);
  assert.match(text, /\[e\d+\] password field "Password" \(the person types this\)/);
  assert.match(text, /\[e\d+\] textbox "Card number" \(the person types this\)/);
  assert.match(text, /combobox "Country" = "Japan" \(options: France \| Japan\)/);
  assert.match(text, /checkbox "Remember me" \(checked\)/);
  assert.match(text, /textbox "Message" = "See you"/);
  assert.match(text, /\[e\d+\] button "Send"/);
  assert.doesNotMatch(text, /hunter2|4111|secret hidden|Hidden button|also hidden/);
  assert.ok(text.indexOf("Inbox") < text.indexOf("Invoice") && text.indexOf("Invoice") < text.indexOf("Send"), "reading order");
});

test("refs stay with their elements across reads, and go when the element does", () => {
  const doc = page(MAIL);
  const refs = newRefs();
  const first = read(doc, refs).text;
  const ref = /\[(e\d+)\] button "Send"/.exec(first)[1];
  assert.equal(element(refs, ref).textContent, "Send");
  doc.body.insertAdjacentHTML("afterbegin", '<button>New one</button>');
  const again = read(doc, refs).text;
  assert.match(again, new RegExp(`\\[${ref}\\] button "Send"`), "the same element keeps its ref");
  element(refs, ref).remove();
  assert.equal(element(refs, ref), null);
  assert.equal(element(refs, "e999"), null);
});

test("find keeps what mentions its words, with the line around it", () => {
  const refs = newRefs();
  const out = read(page(MAIL), refs, { find: "invoice" });
  assert.match(out.text, /Invoice for March/);
  assert.doesNotMatch(out.text, /Remember me/);
  assert.match(read(page(MAIL), refs, { find: "zebra" }).text, /Nothing on this page mentions "zebra"/);
});

test("a long page is cut short, saying how to get the rest", () => {
  const doc = page(`<p>${"word ".repeat(5000)}</p>`);
  const out = read(doc, newRefs(), { max_chars: 1000 });
  assert.ok(out.text.length <= 1002);
  assert.match(out.cut_short, /find/);
});

test("what's inside shadow roots and slots is read too", () => {
  const doc = page('<div id="host"><span slot="title">Slotted title</span></div>');
  const root = doc.getElementById("host").attachShadow({ mode: "open" });
  root.innerHTML = '<h2><slot name="title"></slot></h2><button>In the shadow</button>';
  const text = read(doc, newRefs()).text;
  assert.match(text, /## Slotted title/);
  assert.match(text, /button "In the shadow"/);
});
