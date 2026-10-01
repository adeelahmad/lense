/** @type {import('next').NextConfig} */
const nextConfig = {
  // Separate build folders let several dev servers run side by side (NEXT_DIST_DIR=.next-a next dev -p 3021).
  distDir: process.env.NEXT_DIST_DIR || ".next",
};

export default nextConfig;
