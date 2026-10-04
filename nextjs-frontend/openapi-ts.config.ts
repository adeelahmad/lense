import path from "node:path";

import { defineConfig } from "@hey-api/openapi-ts";
import { config } from "dotenv";

config({ path: ".env.local" });

export default defineConfig({
  // An absolute path: the generator reads a bare "openapi.json" as a server address and bakes it into the client's
  // types, so `make openapi`, the dev watcher and the pre-commit hook would each write a different client.
  input: path.resolve(process.env.OPENAPI_OUTPUT_FILE || "openapi.json"),
  output: {
    // Prettier only: eslint.config.mjs ignores the generated client, and ESLint exits non-zero when every file it is
    // given is ignored.
    postProcess: ["prettier"],
    path: "app/openapi-client",
  },
  plugins: [
    // No baseUrl baked in: lib/api/* configures it (API_BASE_URL on the server).
    { name: "@hey-api/client-fetch", baseUrl: false },
    // One class per OpenAPI tag (Auth.login, Tokens.listTokens, ...) so operation
    // names from different routers never collide.
    { name: "@hey-api/sdk", asClass: true },
  ],
});
