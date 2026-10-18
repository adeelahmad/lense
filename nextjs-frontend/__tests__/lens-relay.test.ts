/** @jest-environment node */
import http from "node:http";
import net, { type AddressInfo } from "node:net";

// eslint-disable-next-line @typescript-eslint/no-require-imports
const { forwardHeaders, route, serverOf, targetOf, upgrade } = require("../lens-relay.js");

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

describe("serverOf", () => {
  it("finds the server a path is for", () => {
    expect(serverOf("/s3")?.name).toBe("S3 share");
    expect(serverOf("/s3/pods/a%20b.txt?X-Amz-Signature=1")?.name).toBe("S3 share");
    expect(serverOf("/s3?list-type=2")?.name).toBe("S3 share");
    expect(serverOf("/dav/")?.name).toBe("WebDAV share");
    expect(serverOf("/dav/pods/Collections/")?.name).toBe("WebDAV share");
    expect(serverOf("/mqtt")?.name).toBe("MQTT over WebSockets");
  });

  it("leaves the web app's own paths to Next", () => {
    for (const p of [
      "/",
      "/s3x",
      "/s/abc",
      "/davis",
      "/mqttx",
      "/api/v1/shares/s3",
      "/settings?section=dav",
      "http://x/s3/",
      "/_next/webpack-hmr",
    ])
      expect(serverOf(p)).toBeNull();
  });
});

describe("targetOf", () => {
  it("reads where the server is from the environment, else this machine", () => {
    const s3 = serverOf("/s3");
    expect(targetOf(s3, {})).toEqual({ host: "127.0.0.1", port: 9555 });
    expect(targetOf(s3, { LENS_S3_URL: "http://worker:9555" })).toEqual({ host: "worker", port: 9555 });
    expect(targetOf(serverOf("/dav"), { LENS_WEBDAV_URL: "http://[::1]:7000" })).toEqual({ host: "::1", port: 7000 });
    expect(targetOf(serverOf("/mqtt"), {})).toEqual({ host: "127.0.0.1", port: 9883 });
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
      serverOf("/s3"),
    );
    expect(out).toEqual({
      host: "lens.example.com",
      authorization: "AWS4-HMAC-SHA256 x",
      "x-forwarded-for": "203.0.113.5",
      "x-forwarded-prefix": "/s3",
    });
    expect(
      forwardHeaders({ "x-forwarded-for": "198.51.100.1" }, "172.18.0.4", serverOf("/dav"))["x-forwarded-for"],
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
      expect(r.body).toContain("S3 share isn't running here");
      expect(r.body).toContain("Settings, File shares");
    } finally {
      delete process.env.LENS_S3_URL;
    }
  });
});

/** Sends a WebSocket handshake for `path` and gives back what came first, and the socket to go on with. */
function handshake(port: number, path: string): Promise<{ first: string; socket: net.Socket }> {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: "127.0.0.1", port }, () =>
      socket.write(
        `GET ${path} HTTP/1.1\r\nHost: lens.example.com\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n` +
          "Sec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\nSec-WebSocket-Version: 13\r\nSec-WebSocket-Protocol: mqtt\r\n\r\n",
      ),
    );
    socket.once("data", (d) => resolve({ first: d.toString("latin1"), socket }));
    socket.on("error", reject);
  });
}

describe("upgrade", () => {
  const seen: { head?: string } = {};
  // stands in for the broker's WebSocket server: answers the handshake, then echoes what it's sent
  const broker = net.createServer((s) => {
    s.once("data", (d) => {
      seen.head = d.toString("latin1");
      s.write("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n\r\n");
      s.pipe(s);
    });
  });
  const front = http.createServer();
  front.on("upgrade", (req, socket, head) => {
    if (!upgrade(req, socket, head)) socket.end("HTTP/1.1 404 Not Found\r\ncontent-length: 4\r\n\r\nnext");
  });
  let frontPort = 0;

  beforeAll(async () => {
    const port = await new Promise<number>((resolve) =>
      broker.listen(0, "127.0.0.1", () => resolve((broker.address() as AddressInfo).port)),
    );
    process.env.LENS_MQTT_WS_URL = `http://127.0.0.1:${port}`;
    frontPort = await listen(front);
  });
  afterAll(() => {
    delete process.env.LENS_MQTT_WS_URL;
    broker.close();
    front.close();
  });

  it("passes the handshake on with the visitor and the prefix, then joins the two connections", async () => {
    const { first, socket } = await handshake(frontPort, "/mqtt");
    expect(first).toMatch(/^HTTP\/1\.1 101/);
    const head = seen.head || "";
    expect(head).toMatch(/^GET \/mqtt HTTP\/1\.1\r\n/);
    expect(head).toContain("host: lens.example.com\r\n");
    expect(head).toContain("connection: Upgrade\r\n");
    expect(head).toContain("upgrade: websocket\r\n");
    expect(head).toContain("sec-websocket-protocol: mqtt\r\n");
    expect(head).toContain("x-forwarded-for: 127.0.0.1\r\n");
    expect(head).toContain("x-forwarded-prefix: /mqtt\r\n");
    const frame = Buffer.from([0x82, 0x02, 0x10, 0x00]); // a binary frame, as MQTT rides in
    const echoed = new Promise<Buffer>((resolve) => socket.once("data", resolve));
    socket.write(frame);
    try {
      expect(await echoed).toEqual(frame);
    } finally {
      socket.destroy();
    }
  });

  it("leaves other upgrades to Next", async () => {
    const { first, socket } = await handshake(frontPort, "/_next/webpack-hmr");
    expect(first).toContain("next");
    socket.destroy();
  });

  it("says so when the broker isn't listening", async () => {
    const closed = net.createServer();
    const port = await new Promise<number>((resolve) =>
      closed.listen(0, "127.0.0.1", () => resolve((closed.address() as AddressInfo).port)),
    );
    await new Promise((resolve) => closed.close(resolve));
    const was = process.env.LENS_MQTT_WS_URL;
    process.env.LENS_MQTT_WS_URL = `http://127.0.0.1:${port}`;
    try {
      const { first, socket } = await handshake(frontPort, "/mqtt");
      expect(first).toMatch(/^HTTP\/1\.1 503/);
      expect(first).toContain("Settings, Sensors");
      socket.destroy();
    } finally {
      process.env.LENS_MQTT_WS_URL = was;
    }
  });
});
