import { NextResponse } from "next/server";

import { auth } from "@/auth";

/** Pages reachable without a session: signing in, and the pages for visitors (docs/access.md). */
const PUBLIC_PATHS = ["/login", "/setup", "/password-recovery", "/signin-link", "/external-signin", "/explore"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some((p) => pathname === p || pathname.startsWith(`${p}/`));
}

/*
 * Runs before every page request. Reading the session here also refreshes the
 * access token when it is about to expire and writes the new session cookie on
 * the response (server components can't set cookies themselves).
 */
export default auth((req) => {
  const { pathname, search } = req.nextUrl;
  // The API's "share a moment" links point at the site root (`/?iiif-content=…`); /iiif opens them. Redirect first,
  // so a signed-out visitor keeps the moment through sign-in.
  if (pathname === "/" && req.nextUrl.searchParams.has("iiif-content")) {
    return NextResponse.redirect(new URL(`/iiif${search}`, req.nextUrl));
  }
  if (isPublic(pathname)) return;

  if (!req.auth || req.auth.error) {
    const url = new URL("/login", req.nextUrl);
    if (pathname !== "/") url.searchParams.set("callbackUrl", pathname + search);
    return NextResponse.redirect(url);
  }
});

export const config = {
  // Skip Auth.js, the backend paths (proxied by route handlers, see lib/api/backend-proxy.ts; /.well-known is OAuth
  // discovery, /mcp the MCP server, /id and /ns the
  // archive's linked-data URIs and vocabulary), Next's files and assets.
  // Under /iiif only the backend's IIIF resources are skipped (collection, discovery, auth, /iiif/<id>/…); the app's own
  // IIIF pages (/iiif, /iiif/collections/…, /iiif/import, /iiif/metadata/…) need the session like any other page.
  matcher: [
    "/((?!api/|\\.well-known/|mcp(?:/|$)|_next/|embed/|s/|id/|ns$|iiif/(?:collection(?!s)|discovery|auth|\\d)|reports/|static/|fonts/|favicon\\.ico|icon\\.svg|robots\\.txt).*)",
  ],
};
