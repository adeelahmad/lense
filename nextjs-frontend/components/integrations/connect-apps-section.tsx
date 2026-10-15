"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plug } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Namespaces } from "@/app/openapi-client";
import type { NamespaceIntegrations, NamespaceIntegrationsUpdate } from "@/app/openapi-client";
import { connectHint, useAppSignIn, useMcpAddress } from "@/components/account/connect-assistant";
import { isUnreachable } from "@/components/errors/error-states";
import { Button } from "@/components/ui/button";
import { Checkbox, Switch } from "@/components/ui/field";
import { CodeBlock, EmptyState, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { needRole } from "@/lib/hooks/session";

export const integrationsKey = (ns: string) => ["namespace", ns, "integrations"] as const;

/** The base URL OpenAI-compatible apps are given, once the page is in the browser (the server can't know it). */
function useChatAddress(ns: string): string {
  const [url, setUrl] = useState(`/api/v1/namespaces/${ns}`);
  useEffect(() => setUrl(`${window.location.origin}/api/v1/namespaces/${ns}`), [ns]);
  return url;
}

function toastFor(ns: string, body: NamespaceIntegrationsUpdate, got: NamespaceIntegrations, mcp: string) {
  if (body.mcp != null)
    return got.mcp
      ? {
          title: `${ns}’s MCP server is on`,
          body: `Agents connected to ${mcp} see ${ns} alone.`,
          tone: "green" as const,
        }
      : { title: `${ns}’s MCP server is off`, body: "Agents connected to it stop getting answers." };
  if (body.chat != null)
    return got.chat
      ? {
          title: `${ns}’s chat completions are on`,
          body: `Apps answer from ${ns} alone, citing it.`,
          tone: "green" as const,
        }
      : { title: `${ns}’s chat completions are off`, body: "Apps using them stop getting answers." };
  const n = got.tools?.length ?? 0;
  return { title: n ? `The model may use ${n} tool${n === 1 ? "" : "s"}` : "No tools: one search per question" };
}

function Row({
  title,
  hint,
  checked,
  disabled,
  onChange,
  label,
}: {
  title: string;
  hint: string;
  checked: boolean;
  disabled: boolean;
  onChange: (v: boolean) => void;
  label: string;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div className="flex min-w-0 flex-1 basis-[260px] flex-col gap-0.5">
        <span className="text-[14px] font-bold text-fg-strong">{title}</span>
        <span className="text-[12.5px] leading-[1.45] text-fg-secondary">{hint}</span>
      </div>
      <Switch
        checked={checked}
        disabled={disabled}
        onCheckedChange={onChange}
        label={checked ? "On" : "Off"}
        aria-label={label}
      />
    </div>
  );
}

/**
 * A namespace's own ways in for other apps (docs/mcp.md, "One namespace's MCP server"; docs/api.md, "Chat
 * completions for any app"): owners turn on an MCP server and OpenAI-style chat completions that see this namespace
 * alone, pick the read tools the model may use there, and the addresses are a copy away.
 */
export function ConnectAppsSection({ ns, isOwner }: { ns: string; isOwner: boolean }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const mcp = `${useMcpAddress()}/${ns}`;
  const chatUrl = useChatAddress(ns);
  const q = useQuery({
    queryKey: integrationsKey(ns),
    queryFn: () => data(Namespaces.getNamespaceIntegrations({ client, path: { name: ns } })),
  });
  const on = q.data?.mcp ?? false;
  const chatOn = q.data?.chat ?? false;
  const tools = q.data?.tools ?? [];
  const signIn = useAppSignIn(on);
  const save = useMutation({
    mutationFn: (body: NamespaceIntegrationsUpdate) =>
      data(Namespaces.updateNamespaceIntegrations({ client, path: { name: ns }, body })),
    onSuccess: (got, body) => {
      qc.setQueryData(integrationsKey(ns), got);
      toast(toastFor(ns, body, got, mcp));
    },
    onError: (e) => toast({ title: "Couldn’t change it", body: (e as Error).message, tone: "red" }),
  });
  const owners = needRole("owner", ns);
  const pick = (name: string, v: boolean) =>
    save.mutate({ tools: v ? [...tools, name] : tools.filter((t) => t !== name) });

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
        <SkeletonRows rows={2} />
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
        <>
          <div className="flex flex-col gap-2">
            <Row
              title="MCP server"
              hint={
                isOwner
                  ? "For Claude, ChatGPT, Cursor and other MCP clients: the same tools as Lens’s own server, on this namespace."
                  : owners
              }
              checked={on}
              disabled={!isOwner || save.isPending}
              onChange={(v) => save.mutate({ mcp: v })}
              label={`${ns}’s MCP server`}
            />
            {on && (
              <>
                <CodeBlock inline text={mcp} label="MCP address" className="self-start" />
                <p className="text-[12.5px] leading-[1.45] text-fg-secondary">{connectHint(signIn.data)}</p>
              </>
            )}
          </div>
          <div className="flex flex-col gap-2 border-t border-border pt-3.5">
            <Row
              title="Chat completions"
              hint={
                isOwner
                  ? "For any app that speaks OpenAI’s API: answers from the configured model, grounded in this namespace and citing it."
                  : owners
              }
              checked={chatOn}
              disabled={!isOwner || save.isPending}
              onChange={(v) => save.mutate({ chat: v })}
              label={`${ns}’s chat completions`}
            />
            {chatOn && (
              <>
                <CodeBlock inline text={chatUrl} label="Base URL" className="self-start" />
                <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
                  Give the app this base URL, the model <code>lens</code>, and an{" "}
                  <Link href="/account/tokens" className="text-blue underline-offset-2 hover:underline">
                    API key
                  </Link>{" "}
                  as its key. Answers cite their sources as [n], with links after them.
                </p>
                <fieldset className="flex flex-col gap-2" disabled={!isOwner || save.isPending}>
                  <legend className="mb-1 text-[13px] font-bold text-fg-strong">Tools the model may use</legend>
                  <p className="text-[12.5px] leading-[1.45] text-fg-secondary">
                    None: one search, one model call per question. Each tool lets the model look more up in {ns} first,
                    which takes more model calls. They only read.
                  </p>
                  <div className="grid gap-x-4 gap-y-1.5 sm:grid-cols-2">
                    {(q.data?.tool_choices ?? []).map((t) => (
                      <span key={t.name} className="flex flex-col" title={t.description}>
                        <Checkbox
                          checked={tools.includes(t.name)}
                          disabled={!isOwner || save.isPending}
                          onCheckedChange={(v) => pick(t.name, v)}
                          label={t.name.replaceAll("_", " ")}
                        />
                      </span>
                    ))}
                  </div>
                </fieldset>
              </>
            )}
          </div>
        </>
      )}
    </section>
  );
}
