import { handlers } from "@/lib/api/backend-proxy";

// OAuth discovery for API and MCP clients (docs/authentication.md#oauth), answered by the API with this origin's
// addresses; see lib/api/backend-proxy.ts.
export const dynamic = "force-dynamic";
export const { GET, HEAD, OPTIONS } = handlers;
