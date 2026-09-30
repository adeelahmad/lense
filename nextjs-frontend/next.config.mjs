import ForkTsCheckerWebpackPlugin from "fork-ts-checker-webpack-plugin";

/** @type {import('next').NextConfig} */
const nextConfig = {
  // Separate build folders let several dev servers run side by side (NEXT_DIST_DIR=.next-a next dev -p 3021).
  distDir: process.env.NEXT_DIST_DIR || ".next",
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
