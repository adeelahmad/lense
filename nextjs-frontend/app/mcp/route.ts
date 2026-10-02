import { handlers } from "@/lib/api/backend-proxy";

// The MCP server (docs/mcp.md), answered by the API: MCP clients connect to <this origin>/mcp and sign in with OAuth
// on this origin; see lib/api/backend-proxy.ts.
export const dynamic = "force-dynamic";
export const { GET, POST, DELETE, OPTIONS } = handlers;
