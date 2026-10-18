/**
 * Relays servers Lens runs besides the API at the web app's own address, so their clients reach them wherever the web
 * app is reached, Settings › Remote access and Settings › HTTPS included. All three run in the worker:
 *
 * - /s3 to the S3 server and /dav to the WebDAV server, the file shares (docs/file-tree.md#at-the-web-apps-address,
 *   fastapi_backend/app/domain/shares.py);
 * - /mqtt to the sensor hub's MQTT broker over WebSockets (docs/sensors.md#at-the-web-apps-address,
 *   fastapi_backend/app/domain/mqtt.py).
 *
 * Requests go to these servers before Next sees them: Next answers only the usual methods (WebDAV lists folders with
 * PROPFIND) and redirects paths that end in a slash. Each keeps its path and Host (S3 clients sign both) and carries
 * X-Forwarded-For and X-Forwarded-Prefix. The share servers turn requests that came this way away until an admin turns
 * on "At the web app's address" in Settings › File shares (shares.remote); the broker listens on WebSockets only once
 * an admin turns on "MQTT at the web app's address" in Settings › Sensors (sensors.mqtt_web). The servers are at
 * LENS_S3_URL, LENS_WEBDAV_URL and LENS_MQTT_WS_URL (http://worker:9555, :9556 and :9883 in the Docker Compose files),
 * else this machine's ports.
 *
 * lens-server.js uses it; `node --require ./lens-relay.js` (in NODE_OPTIONS) adds it to `next dev` and `next start`.
 */
/* eslint-disable @typescript-eslint/no-require-imports */
const http = require("node:http");
const net = require("node:net");

const SERVERS = [
  { prefix: "/s3", env: "LENS_S3_URL", fallback: "http://127.0.0.1:9555", name: "S3 share", where: "File shares" },
  {
    prefix: "/dav",
    env: "LENS_WEBDAV_URL",
    fallback: "http://127.0.0.1:9556",
    name: "WebDAV share",
    where: "File shares",
  },
  {
    prefix: "/mqtt",
    env: "LENS_MQTT_WS_URL",
    fallback: "http://127.0.0.1:9883",
    name: "MQTT over WebSockets",
    where: "Sensors",
  },
];
// Hop-by-hop headers stay with each connection; Expect was answered already (Node sends 100 Continue itself).
const HOP = new Set([
  "connection",
  "keep-alive",
  "proxy-connection",
  "proxy-authorization",
  "te",
  "trailer",
  "transfer-encoding",
  "upgrade",
  "expect",
]);

/** The server a request target is for ("/dav/pods/" is WebDAV's), or null. */
function serverOf(url) {
  const path = (url || "").split("?")[0];
  return SERVERS.find((s) => path === s.prefix || path.startsWith(`${s.prefix}/`)) || null;
}

/** Where a server is: its host and port. */
function targetOf(server, env = process.env) {
  const u = new URL(env[server.env] || server.fallback);
  return { host: u.hostname.replace(/^\[(.*)\]$/, "$1"), port: Number(u.port) || 80 };
}

/** The headers sent on: the request's own (Host too), without hop-by-hop ones, with X-Forwarded-For and -Prefix. */
function forwardHeaders(headers, peer, server) {
  const out = {};
  for (const [k, v] of Object.entries(headers)) if (!HOP.has(k)) out[k] = v;
  const from = (peer || "").replace(/^::ffff:/, "");
  out["x-forwarded-for"] = out["x-forwarded-for"] ? `${out["x-forwarded-for"]}, ${from}` : from;
  out["x-forwarded-prefix"] = server.prefix;
  return out;
}

const offText = (server) =>
  `Lens's ${server.name} isn't running here: an admin turns it on in Settings, ${server.where}.\n`;

/** Answers a request for one of the servers by relaying it; false (nothing sent) when the request isn't for one. */
function route(req, res) {
  const server = serverOf(req.url);
  if (!server) return false;
  const { host, port } = targetOf(server);
  const headers = forwardHeaders(req.headers, req.socket && req.socket.remoteAddress, server);
  const up = http.request({ host, port, method: req.method, path: req.url, headers }, (r) => {
    const out = {};
    for (const [k, v] of Object.entries(r.headers)) if (!HOP.has(k)) out[k] = v;
    res.writeHead(r.statusCode || 502, r.statusMessage, out);
    r.pipe(res);
  });
  up.on("error", () => {
    if (res.headersSent) return res.destroy();
    res.writeHead(503, { "content-type": "text/plain; charset=utf-8", "retry-after": "30" });
    res.end(offText(server));
  });
  res.on("close", () => {
    if (!res.writableFinished) up.destroy();
  });
  req.pipe(up);
  return true;
}

/**
 * Answers a WebSocket (an HTTP upgrade) for one of the servers by joining the two connections once the request is
 * passed on; false when it isn't for one (Next's own, such as its development reloads, go on to Next).
 */
function upgrade(req, socket, head) {
  const server = serverOf(req.url);
  if (!server) return false;
  const { host, port } = targetOf(server);
  const headers = forwardHeaders(req.headers, req.socket && req.socket.remoteAddress, server);
  headers.connection = "Upgrade";
  headers.upgrade = req.headers.upgrade || "websocket";
  const lines = [`${req.method} ${req.url} HTTP/1.1`];
  for (const [k, v] of Object.entries(headers)) for (const one of [].concat(v)) lines.push(`${k}: ${one}`);
  let joined = false;
  const up = net.connect({ host, port }, () => {
    joined = true;
    up.write(`${lines.join("\r\n")}\r\n\r\n`);
    if (head && head.length) up.write(head);
    up.pipe(socket);
    socket.pipe(up);
  });
  up.on("error", () => {
    if (joined) return socket.destroy();
    const text = offText(server);
    socket.end(
      "HTTP/1.1 503 Service Unavailable\r\ncontent-type: text/plain; charset=utf-8\r\nretry-after: 30\r\n" +
        `content-length: ${Buffer.byteLength(text)}\r\nconnection: close\r\n\r\n${text}`,
    );
  });
  up.on("close", () => {
    if (joined) socket.destroy();
  });
  socket.on("error", () => up.destroy());
  socket.on("close", () => up.destroy());
  return true;
}

/** Relays these requests on every HTTP server this process starts (for `node --require ./lens-relay.js`). */
function install() {
  const emit = http.Server.prototype.emit;
  http.Server.prototype.emit = function (event, req, res, ...rest) {
    if (event === "request" && route(req, res)) return true;
    if (event === "upgrade" && upgrade(req, res, rest[0])) return true;
    return emit.call(this, event, req, res, ...rest);
  };
}

if (module.parent && module.parent.id === "internal/preload") install();

module.exports = { serverOf, targetOf, forwardHeaders, route, upgrade, install };
