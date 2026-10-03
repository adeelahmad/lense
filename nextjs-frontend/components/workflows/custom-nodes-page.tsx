"use client";

import { Puzzle } from "lucide-react";
import Link from "next/link";

import { CatalogHeader, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { ICONS } from "@/components/workflows/graph-editor";
import { VISIBILITY } from "@/components/workflows/save-custom-dialog";
import type { CustomDef } from "@/components/workflows/workflow-model";
import { relative } from "@/lib/format";

/** The custom nodes tab: the ones you made, and the ones shared with you. */
export function CustomNodesPage() {
  const catalog = useWorkflowCatalog();
  const list = (catalog.data?.custom_nodes ?? []) as unknown as (CustomDef & { updated_at?: string | null })[];
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <CatalogHeader tab="nodes" />
      {catalog.isLoading ? (
        <SkeletonRows rows={4} />
      ) : catalog.error ? (
        <EmptyState
          tone="error"
          icon={<Puzzle />}
          title="Couldn’t load custom nodes"
          actions={<Button onClick={() => catalog.refetch()}>Try again</Button>}
        >
          {(catalog.error as Error).message}
        </EmptyState>
      ) : list.length ? (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Custom nodes">
            <THead className="border-t-0">
              <tr>
                <Th>Name</Th>
                <Th>Ports</Th>
                <Th>Who can use it</Th>
                <Th>Version</Th>
                <Th>Updated</Th>
              </tr>
            </THead>
            <tbody>
              {list.map((d) => {
                const Icon = ICONS[d.icon ?? ""] ?? Puzzle;
                return (
                  <Tr key={d.id} className="h-[54px]">
                    <Td>
                      <div className="flex min-w-0 items-start gap-2">
                        <Icon aria-hidden className="mt-0.5 size-4 shrink-0 text-fg-secondary" />
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <span className="flex items-center gap-2">
                            <Link
                              href={`/workflows/nodes/${d.id}`}
                              className="font-semibold text-fg hover:text-fg-accent hover:underline"
                            >
                              {d.name}
                            </Link>
                            {d.scopes.length === 1 && (
                              <Badge tone="intent">{d.scopes[0] === "graph" ? "Graph" : "Recordings"}</Badge>
                            )}
                          </span>
                          {d.description && (
                            <span className="line-clamp-1 text-[12px] text-fg-muted">{d.description}</span>
                          )}
                        </div>
                      </div>
                    </Td>
                    <Td className="font-mono text-[12px] text-fg-secondary">
                      {d.inputs.join(", ")} → {d.outputs.join(", ") || "keeps"}
                    </Td>
                    <Td className="text-fg-secondary">
                      {d.visibility === "namespace"
                        ? (d.namespaces ?? []).join(", ")
                        : VISIBILITY[d.visibility ?? "private"]}
                      {!d.editable && d.owner_email ? (
                        <span className="block text-[12px] text-fg-muted">by {d.owner_email}</span>
                      ) : null}
                    </Td>
                    <Td>
                      <code className="font-mono text-[12px] font-medium text-fg">v{d.current}</code>
                    </Td>
                    <Td className="tabular whitespace-nowrap text-fg-muted">{relative(d.updated_at)}</Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      ) : (
        <p className="text-[13px] text-fg-secondary">
          No custom nodes yet. A custom node is nodes saved as one: a building block with its settings preset, or
          several joined, with the settings you choose left for people to fill in. Make one here, or select nodes in a
          workflow and choose Save as custom node. Keep it to yourself, or share it with namespaces or everyone.
        </p>
      )}
    </div>
  );
}
