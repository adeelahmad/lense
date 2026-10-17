import assert from "node:assert/strict";
import { test } from "node:test";
import { check, fetchImages, push, pushLink, tokenKind } from "../src/core/lens.js";
import { asPerson, choices, normalizeServer } from "../src/core/settings.js";

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

test("with the person's own token, the clip names its namespace", async () => {
  let seen;
  const fetchImpl = async (url, init) => {
    seen = { url, init };
    return reply(202, { namespace: "home life", items: [] });
  };
  await push({ server: "https://lens.test", token: "la_key", namespace: "home life" }, { title: "T", markdown: "m" }, { fetchImpl });
  assert.equal(seen.url, "https://lens.test/api/v1/hooks/import?namespace=home%20life");
  assert.equal(seen.init.headers.Authorization, "Bearer la_key");
  await pushLink({ server: "https://lens.test", token: "la_key", namespace: "pods" }, "https://x.test/a.pdf", "A", { fetchImpl });
  assert.equal(seen.url, "https://lens.test/api/v1/hooks/import?namespace=pods");
  assert.deepEqual(JSON.parse(seen.init.body), { url: "https://x.test/a.pdf", title: "A" });
});

test("errors say what to do, by the kind of token", async () => {
  const says = async (token, status, detail) => {
    const fetchImpl = async () => reply(status, detail === undefined ? {} : { detail });
    const err = await push({ server: "https://l", token }, { title: "T", markdown: "m" }, { fetchImpl }).catch((e) => e);
    return err;
  };
  // Lens says why, and that's shown
  assert.match((await says("la_x", 401, "Lens doesn't know this API token: it was revoked")).message, /revoked/);
  // without a reason, the clipper says it for the kind of token
  assert.match((await says("la_x", 401)).message, /doesn't know this API token.*Sign in instead/);
  assert.match((await says("lih_x", 401)).message, /import webhook token.*Sign in instead/);
  assert.match((await says("access", 401)).message, /sign in again/);
  assert.equal((await says("access", 401)).status, 401);
  // a Lens from before personal tokens refuses an API token: update it
  assert.match((await says("la_x", 401, "that token isn't an import hook's")).message, /update Lens/);
  assert.match((await says("lih_x", 401, "that token isn't an import hook's")).message, /replaced or deleted/);
  assert.match((await says("la_x", 403, "this API token is read-only")).message, /read-only/);
  await assert.rejects(push({ server: "https://l", token: "" }, { title: "T", markdown: "m" }, { fetchImpl: async () => reply(202) }), /sign in/);
  assert.equal(tokenKind("lih_a"), "hook");
  assert.equal(tokenKind("la_a"), "key");
  assert.equal(tokenKind("eyJ"), "");
});

test("check asks Lens where the token's clips go", async () => {
  const at = (status, body) => ({ fetchImpl: async (_url, init) => (init?.method === "POST" ? reply(500) : reply(status, body)) });
  const person = { kind: "person", email: "ed@x.io", namespace: null, namespaces: ["calls", "pods"], write: true };
  let r = await check({ server: "https://l", token: "la_x" }, at(200, person));
  assert.equal(r.ok, true);
  assert.match(r.message, /as ed@x\.io: clips go into calls \(or pick another\)/);
  assert.deepEqual(r.target.namespaces, ["calls", "pods"]);
  r = await check({ server: "https://l", token: "la_x" }, at(200, { ...person, namespace: "pods", namespaces: ["pods"] }));
  assert.match(r.message, /clips go into pods$/);
  r = await check({ server: "https://l", token: "la_x" }, at(200, { ...person, write: false }));
  assert.equal(r.ok, false);
  assert.match(r.message, /read-only/);
  r = await check({ server: "https://l", token: "la_x" }, at(200, { ...person, namespaces: [] }));
  assert.equal(r.ok, false);
  assert.match(r.message, /editor role/);
  r = await check({ server: "https://l", token: "lih_x" }, at(200, { kind: "hook", hook: "Clipper", namespace: "pods", namespaces: ["pods"], write: true }));
  assert.equal(r.ok, true);
  assert.match(r.message, /into pods/);
  r = await check({ server: "https://l", token: "la_x" }, at(401, { detail: "Lens doesn't know this API token" }));
  assert.equal(r.ok, false);
  assert.match(r.message, /doesn't know this API token/);
  assert.match((await check({ server: "https://l", token: "t" }, at(404))).message, /not as a Lens/);
});

test("check on a Lens from before personal tokens: an empty push, refused once the hook token is known", async () => {
  const old = (push) => ({ fetchImpl: async (_url, init) => (init?.method === "POST" ? push : reply(405, { detail: "Method Not Allowed" })) });
  assert.equal((await check({ server: "https://l", token: "lih_x" }, old(reply(400, { detail: "send a url" })))).ok, true);
  const r = await check({ server: "https://l", token: "la_x" }, old(reply(401, { detail: "that token isn't an import hook's" })));
  assert.equal(r.ok, false);
  assert.match(r.message, /update Lens/);
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

test("the popup offers each namespace a signed-in Lens can take, the picked one chosen", () => {
  const settings = {
    defaultDestination: "a",
    destinations: [
      { id: "a", name: "lens.test", server: "https://lens.test", kind: "signin", namespace: "pods", namespaces: ["calls", "pods"] },
      { id: "b", name: "Scanner", server: "https://lens.test", kind: "hook", namespace: "inbox" },
      { id: "c", name: "Old one", server: "https://old.test" }, // from before kinds: a hook
    ],
  };
  assert.deepEqual(
    choices(settings).map((c) => [JSON.parse(c.value), c.label, c.selected]),
    [
      [["a", "calls"], "lens.test · calls", false],
      [["a", "pods"], "lens.test · pods", true],
      [["b", "inbox"], "Scanner · inbox", false],
      [["c", ""], "Old one", false],
    ],
  );
  assert.deepEqual(settings.destinations.map(asPerson), [true, false, false]);
});
