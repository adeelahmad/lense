import assert from "node:assert/strict";
import { test } from "node:test";
import { check, fetchImages, push } from "../src/core/lens.js";
import { normalizeServer } from "../src/core/settings.js";

const reply = (status, body = {}, type = "application/json") => new Response(JSON.stringify(body), { status, headers: { "content-type": type } });

test("a plain clip goes as JSON text with the bearer token", async () => {
  let seen;
  const fetchImpl = async (url, init) => {
    seen = { url, init };
    return reply(202, { namespace: "home", items: [{ kind: "text", name: "T", recording: 7 }] });
  };
  const out = await push({ server: "https://lens.test", token: "lih_abc" }, { title: "T", markdown: "# T", url: "https://x" }, { fetchImpl });
  assert.equal(seen.url, "https://lens.test/api/v1/hooks/import");
  assert.equal(seen.init.headers.Authorization, "Bearer lih_abc");
  assert.deepEqual(JSON.parse(seen.init.body), { text: "# T", title: "T" });
  assert.equal(out.items[0].recording, 7);
});

test("images and server capture go as multipart", async () => {
  let body;
  const fetchImpl = async (_url, init) => {
    body = init.body;
    return reply(202, { namespace: "home", items: [] });
  };
  const images = [{ name: "a.png", blob: new Blob(["x"], { type: "image/png" }) }];
  await push({ server: "https://lens.test", token: "t" }, { title: "T", markdown: "m", url: "https://x.test/p" }, { images, captureUrl: true, fetchImpl });
  assert.ok(body instanceof FormData);
  assert.equal(body.get("text"), "m");
  assert.equal(body.get("url"), "https://x.test/p");
  assert.equal(body.getAll("file").length, 1);
});

test("errors say what to do", async () => {
  const fetchImpl = async () => reply(401, { detail: "unknown" });
  await assert.rejects(push({ server: "https://l", token: "t" }, { title: "T", markdown: "m" }, { fetchImpl }), /didn't recognise this token/);
  await assert.rejects(push({ server: "https://l", token: "" }, { title: "T", markdown: "m" }, { fetchImpl }), /token/);
});

test("check reads 400 as a known token", async () => {
  assert.equal((await check({ server: "https://l", token: "t" }, { fetchImpl: async () => reply(400, { detail: "send a url" }) })).ok, true);
  assert.equal((await check({ server: "https://l", token: "t" }, { fetchImpl: async () => reply(401) })).ok, false);
  assert.match((await check({ server: "https://l", token: "t" }, { fetchImpl: async () => reply(404) })).message, /not as a Lens/);
});

test("images: only real images, named sensibly, failures skipped", async () => {
  const fetchImpl = async (url) => {
    if (url.includes("fail")) throw new Error("network");
    if (url.includes("html")) return new Response("<p>", { headers: { "content-type": "text/html" } });
    return new Response(new Blob(["x"], { type: "image/webp" }));
  };
  const out = await fetchImages(["https://a/x/photo.jpg?w=1", "https://a/fail.png", "https://a/page.html", "https://a/raw"], { fetchImpl });
  assert.deepEqual(out.map((i) => i.name), ["photo.jpg", "image-4.webp"]);
});

test("server addresses", () => {
  assert.equal(normalizeServer("lens.example.com/"), "https://lens.example.com");
  assert.equal(normalizeServer("http://localhost:3000"), "http://localhost:3000");
  assert.equal(normalizeServer("http://192.168.1.5/lens/"), "http://192.168.1.5/lens");
  assert.throws(() => normalizeServer("http://lens.example.com"), /https/);
  assert.throws(() => normalizeServer("http://localhost.evil.com"), /https/);
});
