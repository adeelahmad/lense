"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Blocks, MessagesSquare, Mic, Plus } from "lucide-react";
import Link from "next/link";

import { Extensions } from "@/app/openapi-client";
import type { Extension } from "@/app/openapi-client/types.gen";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { VISIBILITY } from "@/components/workflows/save-custom-dialog";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";

export const KIND_LABEL: Record<string, string> = { tool: "Tool", skill: "Skill", hook: "Hook", plugin: "Plugin" };
export const ORIGIN_LABEL: Record<string, string> = { code: "code", canvas: "the canvas", chat: "chat" };

/** What a tool's body is, in a word. */
export function bodyLabel(e: Pick<Extension, "kind" | "spec">): string | null {
  if (e.kind === "plugin") return `${((e.spec.items as unknown[]) ?? []).length} items`;
  if (e.kind === "hook") return `on ${String(e.spec.event ?? "")}`.replace("_", " ");
  if (e.kind !== "tool") return null;
  const run = (e.spec.run ?? {}) as { type?: string };
  return { prompt: "prompt", http: "web request", graph: "canvas graph" }[run.type ?? ""] ?? null;
}

export function useExtensions() {
  const client = useApiClient();
  return useQuery({ queryKey: ["extensions"], queryFn: () => data(Extensions.listExtensions({ client })) });
}

/** The extensions page: what's been added to the assistant (tools, skills, hooks, plugins), switched on or off here. */
export function ExtensionsPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const list = useExtensions();
  const toggle = useMutation({
    mutationFn: (v: { id: number; enabled: boolean }) =>
      data(Extensions.updateExtension({ client, path: { eid: v.id }, body: { enabled: v.enabled } })),
    onSuccess: () => void qc.invalidateQueries({ queryKey: ["extensions"] }),
    onError: (e) => toast({ tone: "red", title: "Couldn’t switch it", body: (e as Error).message }),
  });
  const ask = encodeURIComponent("Make me a tool that ");
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <PageHeader
        title="Extensions"
        meta="Tools, skills, hooks and plugins for the assistant"
        actions={
          <>
            <Button asChild variant="ghost">
              <Link href="/chat?voice=1">
                <Mic aria-hidden />
                By voice
              </Link>
            </Button>
            <Button asChild variant="secondary">
              <Link href={`/chat?q=${ask}`}>
                <MessagesSquare aria-hidden />
                Ask the assistant
              </Link>
            </Button>
            <Button asChild variant="primary">
              <Link href="/extensions/new">
                <Plus aria-hidden />
                New
              </Link>
            </Button>
          </>
        }
      />
      {list.isLoading ? (
        <SkeletonRows rows={4} />
      ) : list.error ? (
        <EmptyState
          tone="error"
          icon={<Blocks />}
          title="Couldn’t load extensions"
          actions={<Button onClick={() => list.refetch()}>Try again</Button>}
        >
          {(list.error as Error).message}
        </EmptyState>
      ) : list.data?.length ? (
        <div className="overflow-x-auto rounded-md border border-border">
          <Table aria-label="Extensions">
            <THead className="border-t-0">
              <tr>
                <Th>On</Th>
                <Th>Name</Th>
                <Th>Kind</Th>
                <Th>Who can use it</Th>
                <Th>Version</Th>
                <Th>Updated</Th>
              </tr>
            </THead>
            <tbody>
              {list.data.map((e) => (
                <Tr key={e.id} className="h-[54px]">
                  <Td>
                    <Switch
                      aria-label={`${e.name} on`}
                      checked={e.enabled}
                      disabled={!e.editable || toggle.isPending}
                      onCheckedChange={(v) => toggle.mutate({ id: e.id, enabled: v })}
                    />
                  </Td>
                  <Td>
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <Link
                        href={`/extensions/${e.id}`}
                        className="font-mono text-[13px] font-semibold text-fg hover:text-fg-accent hover:underline"
                      >
                        {e.name}
                      </Link>
                      {e.description && <span className="line-clamp-1 text-[12px] text-fg-muted">{e.description}</span>}
                    </div>
                  </Td>
                  <Td>
                    <span className="flex items-center gap-1.5">
                      <Badge tone="intent">{KIND_LABEL[e.kind]}</Badge>
                      {bodyLabel(e) && <span className="text-[12px] text-fg-muted">{bodyLabel(e)}</span>}
                    </span>
                  </Td>
                  <Td className="text-fg-secondary">
                    {e.visibility === "namespace"
                      ? (e.namespaces ?? []).join(", ")
                      : VISIBILITY[e.visibility ?? "private"]}
                    {!e.editable && e.owner_email ? (
                      <span className="block text-[12px] text-fg-muted">by {e.owner_email}</span>
                    ) : null}
                  </Td>
                  <Td>
                    <code className="font-mono text-[12px] font-medium text-fg">v{e.current}</code>
                  </Td>
                  <Td className="tabular whitespace-nowrap text-fg-muted">{relative(e.updated_at)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      ) : (
        <EmptyState
          icon={<Blocks />}
          title="Nothing added to the assistant yet"
          actions={
            <>
              <Button asChild variant="secondary">
                <Link href={`/chat?q=${ask}`}>Ask the assistant to make one</Link>
              </Button>
              <Button asChild variant="primary">
                <Link href="/extensions/new">Write one</Link>
              </Button>
            </>
          }
        >
          Give the assistant new tools (a prompt, a web request or a graph drawn on the canvas), skills it follows when
          they apply, hooks that run when something happens in a conversation, or plugins that bundle them. Write one
          here, or just ask the assistant, by typing or by voice.
        </EmptyState>
      )}
    </div>
  );
}
