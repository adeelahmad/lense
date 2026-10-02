/** @type {import('next').NextConfig} */
const nextConfig = {
  // Separate build folders let several dev servers run side by side (NEXT_DIST_DIR=.next-a next dev -p 3021).
  distDir: process.env.NEXT_DIST_DIR || ".next",
  // The dev server would otherwise write an AGENTS.md and a CLAUDE.md into this folder (since Next 16.3).
  agentRules: false,
};

export default nextConfig;
