import { handlers } from "@/lib/api/backend-proxy";

// A namespace's own MCP server (docs/mcp.md, "One namespace's MCP server"), answered by the API like /mcp; see
// lib/api/backend-proxy.ts.
export const dynamic = "force-dynamic";
export const { GET, POST, DELETE, OPTIONS } = handlers;
