import { NextResponse } from "next/server";

import { auth } from "@/auth";

/** Pages reachable without a session. */
const PUBLIC_PATHS = ["/login", "/setup", "/password-recovery"];

function isPublic(pathname: string): boolean {
  return PUBLIC_PATHS.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

/*
 * Runs before every page request. Reading the session here also refreshes the
 * access token when it is about to expire and writes the new session cookie on
 * the response (server components can't set cookies themselves).
 */
export default auth((req) => {
  const { pathname, search } = req.nextUrl;
  if (isPublic(pathname)) return;

  if (!req.auth || req.auth.error) {
    const url = new URL("/login", req.nextUrl);
    if (pathname !== "/")
      url.searchParams.set("callbackUrl", pathname + search);
    return NextResponse.redirect(url);
  }
});

export const config = {
  // Skip Auth.js, the backend paths (proxied by route handlers, see lib/api/backend-proxy.ts), Next's files and assets.
  matcher: ["/((?!api/|_next/|embed/|iiif/|reports/|static/|fonts/|favicon\\.ico|icon\\.svg|robots\\.txt).*)"],
};
