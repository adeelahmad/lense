"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug } from "lucide-react";

import { Namespaces } from "@/app/openapi-client";
import { connectHint, useAppSignIn, useMcpAddress } from "@/components/account/connect-assistant";
import { isUnreachable } from "@/components/errors/error-states";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { CodeBlock, EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole } from "@/lib/hooks/session";

export const integrationsKey = (ns: string) => ["namespace", ns, "integrations"] as const;

/**
 * A namespace's own ways in for other apps (docs/mcp.md, "One namespace's MCP server"): owners turn on an MCP server
 * that sees this namespace alone, and its address is a copy away.
 */
export function ConnectAppsSection({ ns, isOwner }: { ns: string; isOwner: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const mcp = `${useMcpAddress()}/${ns}`;
  const q = useQuery({
    queryKey: integrationsKey(ns),
    queryFn: () => data(Namespaces.getNamespaceIntegrations({ client, path: { name: ns } })),
  });
  const on = q.data?.mcp ?? false;
  const signIn = useAppSignIn(on);
  const save = useMutation({
    mutationFn: (mcpOn: boolean) =>
      data(Namespaces.updateNamespaceIntegrations({ client, path: { name: ns }, body: { mcp: mcpOn } })),
    onSuccess: (got) => {
      qc.setQueryData(integrationsKey(ns), got);
      toast(
        got.mcp
          ? { title: `${ns}’s MCP server is on`, body: `Agents connected to ${mcp} see ${ns} alone.`, tone: "green" }
          : { title: `${ns}’s MCP server is off`, body: "Agents connected to it stop getting answers." },
      );
    },
    onError: (e) => toast({ title: "Couldn’t change it", body: (e as Error).message, tone: "red" }),
  });

  return (
    <section
      id="connect-apps"
      aria-labelledby="connect-apps-heading"
      className="flex scroll-mt-4 flex-col gap-3.5 rounded-md border border-border bg-background px-4 py-5 sm:px-6"
    >
      <div className="flex flex-col gap-1">
        <h2 id="connect-apps-heading" className="text-[17px] font-bold leading-tight text-fg">
          Connect apps
        </h2>
        <p className="text-[13px] leading-[1.45] text-fg-secondary">
          Give an agent or app {ns} alone. It sees this namespace and nothing else, with the roles of the person who
          signs it in.
        </p>
      </div>
      {q.isPending ? (
        <SkeletonRows rows={1} />
      ) : q.isError ? (
        <EmptyState
          tone="error"
          icon={<Plug />}
          title={isUnreachable(q.error) ? "Can’t reach the server" : "Couldn’t load this"}
          actions={<Button onClick={() => q.refetch()}>Try again</Button>}
        >
          {q.error.message}
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-0.5">
              <span className="text-[14px] font-bold text-fg-strong">MCP server</span>
              <span className="text-[12.5px] leading-[1.45] text-fg-secondary">
                {isOwner
                  ? "For Claude, ChatGPT, Cursor and other MCP clients: the same tools as Lens’s own server, on this namespace."
                  : needRole("owner", ns)}
              </span>
            </div>
            <Switch
              checked={on}
              disabled={!isOwner || save.isPending}
              onCheckedChange={(v) => save.mutate(v)}
              label={on ? "On" : "Off"}
              aria-label={`${ns}’s MCP server`}
            />
          </div>
          {on && (
            <>
              <CodeBlock inline text={mcp} label="MCP address" className="self-start" />
              <p className="text-[12.5px] leading-[1.45] text-fg-secondary">{connectHint(signIn.data)}</p>
            </>
          )}
        </div>
      )}
    </section>
  );
}
