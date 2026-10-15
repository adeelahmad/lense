/**
 * Relays servers Lens runs besides the API at the web app's own address, so their clients reach them wherever the web
 * app is reached, Settings › Remote access and Settings › HTTPS included. Today, the file shares
 * (docs/file-tree.md#at-the-web-apps-address): /s3 to the S3 server and /dav to the WebDAV server, both run by the
 * worker (fastapi_backend/app/domain/shares.py).
 *
 * Requests go to the share servers before Next sees them: Next answers only the usual methods (WebDAV lists folders
 * with PROPFIND) and redirects paths that end in a slash. Each keeps its path and Host (S3 clients sign both) and
 * carries X-Forwarded-Prefix, which tells the share server it came this way: it turns these away until an admin turns
 * on "At the web app's address" in Settings › File shares (shares.remote). The servers are at LENS_S3_URL and
 * LENS_WEBDAV_URL (http://worker:9555 and http://worker:9556 in the Docker Compose files), else this machine's ports.
 *
 * lens-server.js uses it; `node --require ./lens-relay.js` (in NODE_OPTIONS) adds it to `next dev` and `next start`.
 */
/* eslint-disable @typescript-eslint/no-require-imports */
const http = require("node:http");

const SHARES = [
  { prefix: "/s3", env: "LENS_S3_URL", fallback: "http://127.0.0.1:9555", name: "S3" },
  { prefix: "/dav", env: "LENS_WEBDAV_URL", fallback: "http://127.0.0.1:9556", name: "WebDAV" },
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

/** The share a request target is for ("/dav/pods/" is WebDAV's), or null. */
function shareOf(url) {
  const path = (url || "").split("?")[0];
  return SHARES.find((s) => path === s.prefix || path.startsWith(`${s.prefix}/`)) || null;
}

/** Where a share's server is: its host and port. */
function targetOf(share, env = process.env) {
  const u = new URL(env[share.env] || share.fallback);
  return { host: u.hostname.replace(/^\[(.*)\]$/, "$1"), port: Number(u.port) || 80 };
}

/** The headers sent on: the request's own (Host too), without hop-by-hop ones, with X-Forwarded-For and -Prefix. */
function forwardHeaders(headers, peer, share) {
  const out = {};
  for (const [k, v] of Object.entries(headers)) if (!HOP.has(k)) out[k] = v;
  const from = (peer || "").replace(/^::ffff:/, "");
  out["x-forwarded-for"] = out["x-forwarded-for"] ? `${out["x-forwarded-for"]}, ${from}` : from;
  out["x-forwarded-prefix"] = share.prefix;
  return out;
}

/** Answers a request for a share by relaying it; false (nothing sent) when the request isn't for one. */
function route(req, res) {
  const share = shareOf(req.url);
  if (!share) return false;
  const { host, port } = targetOf(share);
  const headers = forwardHeaders(req.headers, req.socket && req.socket.remoteAddress, share);
  const up = http.request({ host, port, method: req.method, path: req.url, headers }, (r) => {
    const out = {};
    for (const [k, v] of Object.entries(r.headers)) if (!HOP.has(k)) out[k] = v;
    res.writeHead(r.statusCode || 502, r.statusMessage, out);
    r.pipe(res);
  });
  up.on("error", () => {
    if (res.headersSent) return res.destroy();
    res.writeHead(503, { "content-type": "text/plain; charset=utf-8", "retry-after": "30" });
    res.end(`Lens's ${share.name} share isn't running here: an admin turns it on in Settings, File shares.\n`);
  });
  res.on("close", () => {
    if (!res.writableFinished) up.destroy();
  });
  req.pipe(up);
  return true;
}

/** Relays these requests on every HTTP server this process starts (for `node --require ./lens-relay.js`). */
function install() {
  const emit = http.Server.prototype.emit;
  http.Server.prototype.emit = function (event, req, res, ...rest) {
    if (event === "request" && route(req, res)) return true;
    return emit.call(this, event, req, res, ...rest);
  };
}

if (module.parent && module.parent.id === "internal/preload") install();

module.exports = { shareOf, targetOf, forwardHeaders, route, install };
