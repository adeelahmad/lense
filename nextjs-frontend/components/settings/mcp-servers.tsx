"use client";

import Link from "next/link";

import { ServiceSwitch } from "@/components/admin/service-switch";
import { useExtensions } from "@/components/extensions/extensions-page";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";

/** How many of a server's tools the assistant is offered: "12 tools", "10 of 12 tools". */
export function toolCount(tools: number, off: number): string {
  const word = tools === 1 ? "tool" : "tools";
  return off ? `${tools - off} of ${tools} ${word}` : `${tools} ${word}`;
}

/** Settings › AI assistant: the MCP servers the assistant calls, each with its tools, and the containers that run
 * them (the MCP runner, the browser). */
export function McpServers() {
  const list = useExtensions();
  const servers = (list.data ?? []).filter((e) => e.kind === "mcp");
  return (
    <div className="flex flex-col gap-3">
      <span className="text-[13px] font-bold leading-tight text-fg-strong">MCP servers</span>
      <p className="text-[13px] leading-normal text-fg-secondary">
        Other programs and services whose tools the assistant calls. A server’s page switches each of its tools on or
        off and says which ask first.
      </p>
      {list.isError ? (
        <Banner tone="error">{list.error.message}</Banner>
      ) : servers.length ? (
        <ul aria-label="MCP servers" className="flex flex-col divide-y divide-border text-[13px]">
          {servers.map((e) => {
            const tools = ((e.spec.tools as unknown[]) ?? []).length;
            const off = ((e.spec.off as unknown[]) ?? []).length;
            return (
              <li key={e.id} className="flex items-center justify-between gap-3 py-2">
                <Link href={`/extensions/${e.id}`} className="font-semibold text-fg-accent hover:underline">
                  {e.title ?? e.name}
                </Link>
                <span className="text-[12px] text-fg-secondary">
                  {toolCount(tools, off)}
                  {e.enabled ? "" : ", switched off"}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        !list.isPending && <p className="text-[12.5px] text-fg-secondary">None yet.</p>
      )}
      <Button asChild size="xs" variant="secondary" className="self-start">
        <Link href="/extensions/new/mcp">Add MCP server</Link>
      </Button>
      <ServiceSwitch profile="mcp" label="Run the MCP runner on this machine" />
      <ServiceSwitch profile="browser" label="Run the browser on this machine" />
    </div>
  );
}
