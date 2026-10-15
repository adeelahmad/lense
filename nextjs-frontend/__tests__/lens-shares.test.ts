/** @jest-environment node */
import http from "node:http";
import type { AddressInfo } from "node:net";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { forwardHeaders, route, shareOf, targetOf } = require("../lens-shares.js");

type Seen = { method?: string; url?: string; headers: http.IncomingHttpHeaders; body: string };

function listen(server: http.Server): Promise<number> {
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve((server.address() as AddressInfo).port)));
}

function call(port: number, method: string, path: string, headers: Record<string, string> = {}, body = "") {
  return new Promise<{ status: number; headers: http.IncomingHttpHeaders; body: string }>((resolve, reject) => {
    const req = http.request({ host: "127.0.0.1", port, method, path, headers }, (r) => {
      let data = "";
      r.on("data", (c) => (data += c));
      r.on("end", () => resolve({ status: r.statusCode ?? 0, headers: r.headers, body: data }));
    });
    req.on("error", reject);
    req.end(body);
  });
}

describe("shareOf", () => {
  it("finds the share a path is for", () => {
    expect(shareOf("/s3")?.name).toBe("S3");
    expect(shareOf("/s3/pods/a%20b.txt?X-Amz-Signature=1")?.name).toBe("S3");
    expect(shareOf("/s3?list-type=2")?.name).toBe("S3");
    expect(shareOf("/dav/")?.name).toBe("WebDAV");
    expect(shareOf("/dav/pods/Collections/")?.name).toBe("WebDAV");
  });

  it("leaves the web app's own paths to Next", () => {
    for (const p of ["/", "/s3x", "/s/abc", "/davis", "/api/v1/shares/s3", "/settings?section=dav", "http://x/s3/"])
      expect(shareOf(p)).toBeNull();
  });
});

describe("targetOf", () => {
  it("reads where the server is from the environment, else this machine", () => {
    const s3 = shareOf("/s3");
    expect(targetOf(s3, {})).toEqual({ host: "127.0.0.1", port: 9555 });
    expect(targetOf(s3, { LENS_S3_URL: "http://worker:9555" })).toEqual({ host: "worker", port: 9555 });
    expect(targetOf(shareOf("/dav"), { LENS_WEBDAV_URL: "http://[::1]:7000" })).toEqual({ host: "::1", port: 7000 });
  });
});

describe("forwardHeaders", () => {
  it("keeps the Host and signed headers, adds the visitor and the prefix, drops hop-by-hop ones", () => {
    const out = forwardHeaders(
      {
        host: "lens.example.com",
        authorization: "AWS4-HMAC-SHA256 x",
        connection: "keep-alive",
        expect: "100-continue",
      },
      "::ffff:203.0.113.5",
      shareOf("/s3"),
    );
    expect(out).toEqual({
      host: "lens.example.com",
      authorization: "AWS4-HMAC-SHA256 x",
      "x-forwarded-for": "203.0.113.5",
      "x-forwarded-prefix": "/s3",
    });
    expect(
      forwardHeaders({ "x-forwarded-for": "198.51.100.1" }, "172.18.0.4", shareOf("/dav"))["x-forwarded-for"],
    ).toBe("198.51.100.1, 172.18.0.4");
  });
});

describe("route", () => {
  const seen: Seen = { headers: {}, body: "" };
  const share = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => (body += c));
    req.on("end", () => {
      Object.assign(seen, { method: req.method, url: req.url, headers: req.headers, body });
      res.writeHead(207, { "content-type": "application/xml", dav: "1" });
      res.end("<multistatus/>");
    });
  });
  const front = http.createServer((req, res) => {
    if (!route(req, res)) {
      res.writeHead(404);
      res.end("next");
    }
  });
  let frontPort = 0;

  beforeAll(async () => {
    process.env.LENS_WEBDAV_URL = `http://127.0.0.1:${await listen(share)}`;
    frontPort = await listen(front);
  });
  afterAll(() => {
    delete process.env.LENS_WEBDAV_URL;
    share.close();
    front.close();
  });

  it("relays any method, path and body to the share, and its answer back", async () => {
    const r = await call(
      frontPort,
      "PROPFIND",
      "/dav/pods/Collections/",
      { Depth: "1", Host: "lens.example.com" },
      "<propfind/>",
    );
    expect(r.status).toBe(207);
    expect(r.body).toBe("<multistatus/>");
    expect(r.headers.dav).toBe("1");
    expect(seen.method).toBe("PROPFIND");
    expect(seen.url).toBe("/dav/pods/Collections/");
    expect(seen.body).toBe("<propfind/>");
    expect(seen.headers.host).toBe("lens.example.com");
    expect(seen.headers.depth).toBe("1");
    expect(seen.headers["x-forwarded-prefix"]).toBe("/dav");
    expect(seen.headers["x-forwarded-for"]).toBe("127.0.0.1");
  });

  it("leaves other paths to Next", async () => {
    expect((await call(frontPort, "GET", "/settings")).body).toBe("next");
  });

  it("says so when the share isn't running", async () => {
    const closed = http.createServer();
    const port = await listen(closed);
    await new Promise((resolve) => closed.close(resolve));
    process.env.LENS_S3_URL = `http://127.0.0.1:${port}`;
    try {
      const r = await call(frontPort, "GET", "/s3/");
      expect(r.status).toBe(503);
      expect(r.body).toContain("Settings, File shares");
    } finally {
      delete process.env.LENS_S3_URL;
    }
  });
});
