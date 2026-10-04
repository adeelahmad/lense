import { NextRequest } from "next/server";

import { browserAddress, trustProxyHeaders } from "@/lib/api/backend-proxy";

/**
 * The sign-in routes' request, at the address the browser is on. Next hands Auth.js a URL naming the host it listens
 * on (localhost:3000), so without this its redirects (after signing out, say) would send someone who opened Lens at
 * its Cloudflare tunnel, LAN name or https:// address to localhost. The Host header says where the browser is; the
 * protocol is https when a proxy in front says so (cloudflared and Cloudflare send X-Forwarded-Proto: https).
 * Only the requester's own redirects and cookies depend on it.
 */
export function atBrowserOrigin(req: NextRequest): NextRequest {
  if (process.env.AUTH_URL || process.env.NEXTAUTH_URL) return req; // pinned: Auth.js uses that address
  const { host, proto } = browserAddress(req.headers, req.nextUrl, trustProxyHeaders());
  const https = (req.headers.get("x-forwarded-proto") ?? "").split(",")[0].trim() === "https";
  const scheme = https ? "https" : proto;
  if (!/^[A-Za-z0-9.-]+(:\d+)?$|^\[[0-9A-Fa-f:.]+\](:\d+)?$/.test(host) || !["http", "https"].includes(scheme))
    return req;
  const origin = `${scheme}://${host}`;
  if (origin === req.nextUrl.origin) return req;
  return new NextRequest(req.nextUrl.href.replace(req.nextUrl.origin, origin), req);
}
