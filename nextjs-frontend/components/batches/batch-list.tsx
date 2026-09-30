"use client";

import { Layers } from "lucide-react";
import Link from "next/link";

import { useBatchList } from "@/components/batches/data";
import { batchTone, progressLabel, progressParts } from "@/components/batches/format";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, Progress, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { relative } from "@/lib/format";

/** Your batch runs (everyone's, for admins), newest first. */
export function BatchList() {
  const list = useBatchList();
  return (
    <div className="px-4 py-6 md:px-6">
      <PageHeader title="Batch runs" meta="Templates, pipelines and steps run over many recordings" />
      {list.isLoading && <SkeletonRows rows={4} />}
      {list.isError && (
        <Banner
          tone="error"
          action={
            <Button size="sm" variant="secondary" onClick={() => list.refetch()}>
              Try again
            </Button>
          }
        >
          {list.error.message}
        </Banner>
      )}
      {list.data?.length === 0 && (
        <EmptyState icon={<Layers />} title="No batch runs yet">
          Start one with “Run on…” from a Library selection, a namespace, a speaker, an entity in the graph, search
          results or a saved collection.
        </EmptyState>
      )}
      {list.data && list.data.length > 0 && (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Batch runs">
            <THead className="border-t-0">
              <tr>
                <Th>Run</Th>
                <Th className="w-[140px]">Status</Th>
                <Th className="w-[220px]">Progress</Th>
                <Th className="hidden w-[200px] md:table-cell">Started by</Th>
                <Th className="w-[120px]">Started</Th>
              </tr>
            </THead>
            <tbody>
              {list.data.map((b) => {
                const p = progressParts(b.progress);
                return (
                  <Tr key={b.id}>
                    <Td>
                      <Link
                        href={`/batches/${b.id}`}
                        className="font-semibold text-fg hover:text-fg-accent hover:underline"
                      >
                        {b.label}
                      </Link>
                      <span className="ml-1.5 text-[12px] text-fg-muted">#{b.id}</span>
                    </Td>
                    <Td>
                      <Badge tone={batchTone(b.status)} dot>
                        {b.status}
                      </Badge>
                    </Td>
                    <Td>
                      <span className="flex items-center gap-2">
                        <Progress
                          value={p.total ? (p.done + p.failed) / p.total : 0}
                          tone={p.failed ? "red" : b.status === "finished" ? "green" : "intent"}
                          className="w-24"
                          label={`${b.label} progress`}
                        />
                        <span className="tabular text-[12.5px] text-fg-secondary">{progressLabel(p)}</span>
                      </span>
                    </Td>
                    <Td className="hidden truncate text-fg-secondary md:table-cell">{b.created_by}</Td>
                    <Td className="text-fg-secondary">{relative(b.created_at)}</Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
    </div>
  );
}
