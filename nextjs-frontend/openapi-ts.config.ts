import { defineConfig } from "@hey-api/openapi-ts";
import { config } from "dotenv";

config({ path: ".env.local" });

export default defineConfig({
  input: process.env.OPENAPI_OUTPUT_FILE || "openapi.json",
  output: {
    format: "prettier",
    lint: "eslint",
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
