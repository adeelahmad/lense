"use client";

import { Workflow } from "lucide-react";
import Link from "next/link";

import { CatalogHeader, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { relative } from "@/lib/format";

/** The workflows tab: saved workflows and the pipelines that run them. */
export function WorkflowsPage() {
  const catalog = useWorkflowCatalog();
  const list = catalog.data?.workflows ?? [];
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <CatalogHeader tab="workflows" />
      {catalog.isLoading ? (
        <SkeletonRows rows={4} />
      ) : catalog.error ? (
        <EmptyState
          tone="error"
          icon={<Workflow />}
          title="Couldn’t load workflows"
          actions={<Button onClick={() => catalog.refetch()}>Try again</Button>}
        >
          {(catalog.error as Error).message}
        </EmptyState>
      ) : list.length ? (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Workflows">
            <THead className="border-t-0">
              <tr>
                <Th>Name</Th>
                <Th>Version</Th>
                <Th>Run by</Th>
                <Th>Updated</Th>
              </tr>
            </THead>
            <tbody>
              {list.map((w) => (
                <Tr key={w.id} className="h-[54px]">
                  <Td>
                    <div className="flex min-w-0 flex-col gap-0.5">
                      <span className="flex items-center gap-2">
                        <Link
                          href={`/workflows/${w.id}`}
                          className="font-semibold text-fg hover:text-fg-accent hover:underline"
                        >
                          {w.name}
                        </Link>
                        {w.scope === "graph" && <Badge tone="intent">Graph</Badge>}
                      </span>
                      {w.description && <span className="line-clamp-1 text-[12px] text-fg-muted">{w.description}</span>}
                    </div>
                  </Td>
                  <Td>
                    <code className="font-mono text-[12px] font-medium text-fg">v{w.current}</code>
                  </Td>
                  <Td className="text-fg-secondary">
                    {w.scope === "graph" ? "Routines" : w.pipelines?.length ? w.pipelines.join(", ") : "—"}
                  </Td>
                  <Td className="tabular whitespace-nowrap text-fg-muted">{relative(w.updated_at)}</Td>
                </Tr>
              ))}
            </tbody>
          </Table>
        </div>
      ) : (
        <p className="text-[13px] text-fg-secondary">
          No workflows yet. A workflow is drawn on a canvas: entity extraction by rules or by the model, prompts,
          conditions, and nodes that keep the results as outputs, custom fields or entities. Add one to a pipeline as a
          Workflow step.
        </p>
      )}
    </div>
  );
}
