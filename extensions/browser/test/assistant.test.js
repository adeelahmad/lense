import assert from "node:assert/strict";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const area = new Map([["account", { server: "https://lens.test", accessToken: "la_token", apiToken: true }]]);
globalThis.chrome = {
  storage: {
    local: {
      async get(key) {
        return area.has(key) ? { [key]: structuredClone(area.get(key)) } : {};
      },
      async set(items) {
        for (const [k, v] of Object.entries(items)) area.set(k, v);
      },
      async remove(key) {
        area.delete(key);
      },
    },
  },
};
const assistant = await import("../src/core/assistant.js");

/** A streamed answer, cut into uneven chunks the way a network delivers it. */
function stream(text, size = 7) {
  const bytes = new TextEncoder().encode(text);
  return new ReadableStream({
    start(c) {
      for (let i = 0; i < bytes.length; i += size) c.enqueue(bytes.slice(i, i + size));
      c.close();
    },
  });
}

test("asking posts the question and hands over each streamed event", async () => {
  let seen;
  const fetchImpl = async (url, init) => {
    seen = { url, init };
    const sse = [
      'event: step\ndata: {"tool":"search","args":{},"summary":"Searched for “invoice”"}\n\n',
      'event: passages\ndata: [{"n":1,"recording_id":7,"text":"x"}]\n\n',
      'event: token\ndata: {"text":"Your invoice [1]."}\n\n',
      'event: done\ndata: {"message":12}\n\n',
    ].join("");
    return new Response(stream(sse), { status: 200, headers: { "content-type": "text/event-stream" } });
  };
  const events = [];
  await assistant.ask(5, "Where's my invoice?", { fetchImpl, onEvent: (name, data) => events.push([name, data]) });
  assert.equal(seen.url, "https://lens.test/api/v1/chats/5/messages");
  assert.equal(seen.init.headers.Authorization, "Bearer la_token");
  assert.deepEqual(JSON.parse(seen.init.body), { content: "Where's my invoice?" });
  assert.deepEqual(
    events.map(([n]) => n),
    ["step", "passages", "token", "done"],
  );
  assert.equal(events[2][1].text, "Your invoice [1].");

  await assistant.ask(5, "and now?", { browser: "browser_session:abc", fetchImpl, onEvent() {} });
  assert.equal(JSON.parse(seen.init.body).browser, "browser_session:abc");
});

test("a refused question says Lens's reason", async () => {
  const fetchImpl = async () => new Response(JSON.stringify({ detail: "ask something" }), { status: 400 });
  await assert.rejects(assistant.ask(5, " ", { fetchImpl, onEvent() {} }), /ask something/);
});

test("approvals are decided by their id, and only pending ones wait", async () => {
  const calls = [];
  const fetchImpl = async (url, init = {}) => {
    calls.push([url, init.method ?? "GET", init.body]);
    if (url.includes("/approvals?")) return Response.json([{ id: 1, status: "pending" }, { id: 2, status: "done" }]);
    return Response.json({ status: "done" });
  };
  assert.deepEqual(await assistant.pendingApprovals(5, { fetchImpl }), [{ id: 1, status: "pending" }]);
  assert.deepEqual(await assistant.decide(1, "decline", { fetchImpl }), { status: "done" });
  assert.deepEqual(calls[1], ["https://lens.test/api/v1/approvals/1", "POST", '{"decision":"decline"}']);
});

test("answers render as Markdown with their citations linked, and nothing that runs", () => {
  const { window } = new JSDOM("<!doctype html><body></body>");
  const passages = [
    { n: 1, recording_id: 7, title: "Renewal call", time: "12:34", t0: 754000, text: "x" },
    { n: 2, recording_id: 9, title: 'Odd "title"', text: "y" },
  ];
  const text = [
    "**Paid** on time [1, 2] and see [3].",
    "",
    "<script>alert(1)</script><img src=x onerror=alert(1)>",
    "[click](javascript:alert(1)) [site](https://example.com)",
  ].join("\n");
  const box = window.document.createElement("div");
  box.innerHTML = assistant.answerHtml(text, passages, "https://lens.test", window);
  assert.equal(box.querySelector("strong").textContent, "Paid");
  const cites = [...box.querySelectorAll("a.cite")];
  assert.deepEqual(
    cites.map((a) => [a.textContent, a.href]),
    [
      ["[1]", "https://lens.test/resources/7?t=754"],
      ["[2]", "https://lens.test/resources/9"],
    ],
  );
  assert.equal(cites[0].title, "Renewal call · 12:34");
  assert.match(box.textContent, /see \[3\]/, "a citation with no passage stays as text");
  assert.equal(box.querySelector("script"), null);
  assert.equal(box.querySelector("img"), null);
  const links = [...box.querySelectorAll("a")];
  assert.ok(links.every((a) => a.href.startsWith("https://") && a.target === "_blank" && a.rel === "noopener noreferrer"));
  assert.match(box.textContent, /click/, "an unsafe link keeps its text");
});
