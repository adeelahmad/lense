import type { Metadata } from "next";

import { McpServerForm } from "@/components/extensions/mcp-server-form";

export const metadata: Metadata = { title: "Add MCP server" };

/** A new MCP server, by a short form or a pasted config. */
export default function Page() {
  return <McpServerForm />;
}
