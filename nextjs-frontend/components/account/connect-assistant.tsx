"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useState } from "react";

import { CodeBlock } from "@/components/ui/states";

/** Where AI assistants and other MCP clients connect: the web app's own address plus /mcp (docs/mcp.md). */
export function mcpAddress(): string {
  return typeof window === "undefined" ? "/mcp" : `${window.location.origin}/mcp`;
}

/** The MCP address once the page is in the browser (the server can't know the address people use). */
export function useMcpAddress(): string {
  const [mcp, setMcp] = useState("/mcp");
  useEffect(() => setMcp(mcpAddress()), []);
  return mcp;
}

/** Whether apps may sign people in here (tokens.oauth_enabled): OAuth discovery answers only when they may. */
export function useAppSignIn(enabled = true) {
  return useQuery({
    queryKey: ["oauth", "on"],
    queryFn: async () => (await fetch("/.well-known/oauth-authorization-server", { cache: "no-store" })).ok,
    enabled,
    staleTime: 30_000,
  });
}

/** What to do with the address: add it as a connector, or give a token while apps can't sign in. */
export function connectHint(signIn: boolean | undefined): string {
  return signIn === false
    ? "Apps can’t sign in here, so give the assistant an API token as its key (an admin turns sign-in on in Settings → API keys)."
    : "Add it to Claude, ChatGPT, Cursor or another MCP client as a connector; you allow it here once.";
}

/** The MCP address with Copy, so connecting an assistant after setup is a copy and a paste (API tokens page). */
export function ConnectAssistant() {
  const mcp = useMcpAddress();
  const signIn = useAppSignIn();
  return (
    <div id="connect" className="flex flex-col gap-1.5">
      <span className="text-[13px] font-semibold text-fg">Connect an assistant</span>
      <CodeBlock inline text={mcp} label="MCP address" className="self-start" />
      <p className="text-[12.5px] leading-[1.45] text-fg-secondary">{connectHint(signIn.data)}</p>
    </div>
  );
}
