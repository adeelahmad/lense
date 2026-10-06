/**
 * Starts Next.js's standalone server (server.js) so that a visitor can't choose the address the API sees.
 *
 * Next fills in X-Forwarded-For with the address it was reached from only when the request has none, so a visitor
 * who sends their own would otherwise pass it on to the API, which trusts the web app's header for IP groups and
 * sign-in throttles. Here a request's X-Forwarded-For is dropped before Next sees it, unless it came from a proxy
 * known to set it: any peer with TRUST_PROXY_HEADERS=true (a reverse proxy in front), or one of the hosts named in
 * LENS_WEB_PROXY_HOSTS (the containers running the Cloudflare tunnel), looked up every 30 seconds.
 */
/* eslint-disable @typescript-eslint/no-require-imports */
const http = require("node:http");
const dns = require("node:dns");

const truthy = (v) => ["1", "true", "yes"].includes((v || "").trim().toLowerCase());
const plain = (address) => (address || "").replace(/^::ffff:/, "");

/** Drop a forwarded address the peer has no standing to give; Next then fills in the peer's own. */
function scrubForwardedFor(headers, peer, trustAll, proxies) {
  if (!trustAll && !proxies.has(plain(peer))) delete headers["x-forwarded-for"];
  return headers;
}

function start() {
  const trustAll = truthy(process.env.TRUST_PROXY_HEADERS);
  const hosts = (process.env.LENS_WEB_PROXY_HOSTS || "")
    .split(",")
    .map((h) => h.trim())
    .filter(Boolean);
  let proxies = new Set();
  const refresh = async () => {
    const found = new Set();
    for (const h of hosts) {
      try {
        for (const a of await dns.promises.lookup(h, { all: true })) found.add(plain(a.address));
      } catch {
        // not up yet; looked up again shortly
      }
    }
    proxies = found;
  };
  if (hosts.length) {
    refresh();
    setInterval(refresh, 30_000).unref();
  }
  const emit = http.Server.prototype.emit;
  http.Server.prototype.emit = function (event, req, ...rest) {
    if (event === "request") scrubForwardedFor(req.headers, req.socket && req.socket.remoteAddress, trustAll, proxies);
    return emit.call(this, event, req, ...rest);
  };
  require("./server.js");
}

if (require.main === module) start();

module.exports = { scrubForwardedFor };
