/** @type {import('next').NextConfig} */
const nextConfig = {
  // Separate build folders let several dev servers run side by side (NEXT_DIST_DIR=.next-a next dev -p 3021).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The OAuth consent page may not be framed: a page that framed it could trick people into clicking Allow.
  async headers() {
    return [
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
