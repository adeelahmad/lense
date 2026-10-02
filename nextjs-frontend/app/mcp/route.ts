import { handlers } from "@/lib/api/backend-proxy";

// The MCP endpoint for assistants (docs/mcp.md), answered by the API; see lib/api/backend-proxy.ts.
export const dynamic = "force-dynamic";
export const { GET, POST, DELETE, OPTIONS } = handlers;
