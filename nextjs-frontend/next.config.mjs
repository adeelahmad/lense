import ForkTsCheckerWebpackPlugin from "fork-ts-checker-webpack-plugin";

const apiBaseUrl = (
  process.env.API_BASE_URL || "http://localhost:8000"
).replace(/\/+$/, "");

/**
 * Paths served by the FastAPI backend on the frontend's origin. The API hands
 * out relative signed media URLs (`/api/v1/recordings/12/audio?exp=..&sig=..`,
 * `/embed/..`, `/iiif/..`, ...), so these make them work in <audio>/<img> tags
 * and let the browser call the API (e.g. SSE streams) without CORS.
 * `/api/auth/*` stays with Auth.js.
 */
const BACKEND_PATHS = ["/api/v1", "/embed", "/iiif", "/reports", "/static"];

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Rewrites are resolved when the server starts (dev) or at build time (next build):
  // set API_BASE_URL in both places.
  async rewrites() {
    return BACKEND_PATHS.flatMap((path) => [
      { source: path, destination: `${apiBaseUrl}${path}` },
      { source: `${path}/:path*`, destination: `${apiBaseUrl}${path}/:path*` },
    ]);
  },
  webpack: (config, { isServer }) => {
    if (!isServer) {
      config.plugins.push(
        new ForkTsCheckerWebpackPlugin({
          async: true,
          typescript: {
            configOverwrite: {
              compilerOptions: {
                skipLibCheck: true,
              },
            },
          },
        }),
      );
    }
    return config;
  },
};

export default nextConfig;
