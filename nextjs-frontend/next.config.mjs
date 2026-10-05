/** @type {import('next').NextConfig} */
const nextConfig = {
  // Separate build folders let several dev servers run side by side (NEXT_DIST_DIR=.next-a next dev -p 3021).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // Dockerfile.prod builds a self-contained server (.next/standalone) with only the packages it uses: a much smaller
  // image for small machines. Other builds (Cloudron, `pnpm build`) keep `next start`.
  ...(process.env.NEXT_OUTPUT === "standalone" ? { output: "standalone" } : {}),
  // The dev server would otherwise write an AGENTS.md and a CLAUDE.md into this folder (since Next 16.3).
  agentRules: false,
  async headers() {
    return [
      // The app's own pages. The API's paths (proxied: /api/v1, /embed, /s, /iiif resources, /reports, ...) send
      // their own headers, so the embed player can still be framed where Settings › Access allows it.
      {
        source: "/((?!api/|embed/|s/|iiif/|reports/|static/|id/|ns$|mcp|\\.well-known/).*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "X-Frame-Options", value: "SAMEORIGIN" },
          { key: "Content-Security-Policy", value: "frame-ancestors 'self'; base-uri 'self'; object-src 'none'" },
          {
            key: "Permissions-Policy",
            value: "camera=(), geolocation=(), payment=(), usb=(), serial=(), microphone=(self)",
          },
        ],
      },
      // Reached through Cloudflare (the tunnel in Settings › Remote access), which always serves https: browsers keep
      // to https for this host from then on. Not sent elsewhere, so a LAN address or a self-signed proxy isn't locked in.
      {
        source: "/:path*",
        has: [{ type: "header", key: "cf-ray" }],
        headers: [{ key: "Strict-Transport-Security", value: "max-age=15552000" }],
      },
      // The OAuth consent page may not be framed: a page that framed it could trick people into clicking Allow.
      {
        source: "/oauth/:path*",
        headers: [
          { key: "Content-Security-Policy", value: "frame-ancestors 'none'" },
          { key: "X-Frame-Options", value: "DENY" },
        ],
      },
    ];
  },
};

export default nextConfig;
