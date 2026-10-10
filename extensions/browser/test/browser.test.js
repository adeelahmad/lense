import assert from "node:assert/strict";
import { test } from "node:test";

const area = new Map([["account", { server: "https://lens.test", accessToken: "la_token", apiToken: true }]]);
globalThis.chrome = {
  storage: {
    local: {
      async get(key) {
        return area.has(key) ? { [key]: structuredClone(area.get(key)) } : {};
      },
      async set(items) {
        for (const [k, v] of Object.entries(items)) area.set(k, structuredClone(v));
      },
      async remove(key) {
        area.delete(key);
      },
    },
  },
};
const browser = await import("../src/core/browser.js");

test("a downloaded file keeps its name, and gets an extension that says what it is", () => {
  assert.equal(browser.fileName('attachment; filename="Invoice March.pdf"', "https://x.test/a", "application/pdf"), "Invoice March.pdf");
  assert.equal(browser.fileName("attachment; filename*=UTF-8''R%C3%A9sum%C3%A9.docx", "https://x.test/a", ""), "Résumé.docx");
  assert.equal(browser.fileName(null, "https://x.test/files/report%202026.xlsx?dl=1", ""), "report 2026.xlsx");
  assert.equal(browser.fileName(null, "https://mail.x.test/attachment?id=7", "application/pdf"), "attachment.pdf");
  assert.equal(browser.fileName('attachment; filename="../../etc/passwd"', "https://x.test/", "text/plain"), "..-..-etc-passwd.txt");
  assert.equal(browser.fileName(null, "https://x.test/", ""), "download");
});

test("the browser is named for Lens by its brand and system", () => {
  const chrome = { userAgentData: { brands: [{ brand: "Not)A;Brand" }, { brand: "Chromium" }, { brand: "Google Chrome" }], platform: "macOS" } };
  assert.equal(browser.label(chrome), "Google Chrome on macOS");
  assert.equal(browser.label({ userAgentData: { brands: [{ brand: "Chromium" }, { brand: "Not.A/Brand" }], platform: "Linux" } }), "Chromium on Linux");
  assert.equal(browser.label({ userAgent: "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0" }), "Firefox");
  assert.equal(browser.label({ userAgent: "Mozilla/5.0 (Macintosh) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/18.0 Safari/605.1.15" }), "Safari");
});

test("sites the person allowed are remembered, and can be forgotten", async () => {
  assert.equal(await browser.enabled(), true, "on until the person turns it off");
  await browser.allowSite("https://mail.example.com");
  await browser.allowSite("https://mail.example.com");
  await browser.allowSite("https://bank.example.com");
  assert.deepEqual(await browser.sites(), ["https://bank.example.com", "https://mail.example.com"]);
  await browser.forgetSite("https://bank.example.com");
  assert.deepEqual(await browser.sites(), ["https://mail.example.com"]);
  await browser.setEnabled(false);
  assert.equal(await browser.enabled(), false);
});

test("a file is attached to the conversation as a held upload, sent in chunks", async () => {
  const sent = [];
  const realFetch = globalThis.fetch;
  globalThis.fetch = async (url, init = {}) => {
    const path = url.replace("https://lens.test/api/v1", "");
    if (path === "/uploads/limits") return Response.json({ max_mb: 1, chunk_mb: 100 / 1048576 });
    if (path === "/uploads" && init.method === "POST") {
      const body = JSON.parse(init.body);
      assert.equal(body.hold, true);
      return Response.json({ id: "u1", filename: body.filename, size: body.size, offset: 0, state: "receiving" }, { status: 201 });
    }
    const m = /^\/uploads\/u1\?offset=(\d+)$/.exec(path);
    if (m && init.method === "PUT") {
      const bytes = new Uint8Array(await init.body.arrayBuffer());
      sent.push([Number(m[1]), bytes.length]);
      const offset = Number(m[1]) + bytes.length;
      return Response.json({ id: "u1", filename: "notes.txt", size: 250, offset, state: offset >= 250 ? "held" : "receiving" });
    }
    return Response.json({ detail: "not found" }, { status: 404 });
  };
  try {
    const up = await browser.upload(new File(["x".repeat(250)], "notes.txt", { type: "text/plain" }));
    assert.equal(up.state, "held");
    assert.equal(up.id, "u1");
    assert.deepEqual(sent, [
      [0, 100],
      [100, 100],
      [200, 50],
    ]);
    await assert.rejects(browser.upload(new File(["x".repeat(2 * 1048576)], "big.txt")), /up to 1 MB/);
  } finally {
    globalThis.fetch = realFetch;
  }
});
