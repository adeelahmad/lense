"use client";

import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Workflow } from "lucide-react";
import Link from "next/link";
import { useMemo } from "react";

import { Namespaces, Pipelines } from "@/app/openapi-client";
import type { Pipeline } from "@/app/openapi-client/types.gen";
import { CatalogHeader, usePipelineCatalog } from "@/components/pipelines/catalog-header";
import { toSpec } from "@/components/pipelines/pipeline-model";
import { StepChips } from "@/components/pipelines/step-chips";
import { Button } from "@/components/ui/button";
import { Select } from "@/components/ui/field";
import { SectionTitle } from "@/components/ui/panel";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

/** PL1: the built-in pipeline and the saved ones, what uses them, and each namespace's default. */
export function PipelinesPage() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { namespaces, can } = useArchive();
  const catalog = usePipelineCatalog();
  const list = catalog.data?.pipelines ?? [];
  const details = useQueries({
    queries: list.map((p) => ({
      queryKey: ["pipeline", p.id],
      queryFn: () => data(Pipelines.getPipeline({ client, path: { pid: p.id } })),
      staleTime: 30_000,
    })),
  });
  const mine = new Set(namespaces.map((n) => n.name));
  const usedBy = useMemo(() => {
    const m = new Map<string, number>();
    for (const p of list) for (const ns of p.namespaces ?? []) m.set(ns, p.id);
    return m;
  }, [list]);
  const onStandard = namespaces.filter((n) => !usedBy.has(n.name)).map((n) => n.name);

  const setDefault = useMutation({
    mutationFn: ({ ns, pipeline }: { ns: string; pipeline: number | null }) => data(Namespaces.updateNamespace({ client, path: { name: ns }, body: { pipeline } })),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      toast({ tone: "green", title: `${v.ns} now runs ${v.pipeline == null ? "the standard pipeline" : (list.find((p) => p.id === v.pipeline)?.name ?? "that pipeline")}`, body: "New runs use it; runs in progress keep theirs." });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change the default", body: e.message }),
  });

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <CatalogHeader tab="pipelines" />
      {catalog.isLoading ? (
        <SkeletonRows rows={4} />
      ) : catalog.error ? (
        <EmptyState tone="error" icon={<Workflow />} title="Couldn’t load pipelines" actions={<Button onClick={() => catalog.refetch()}>Try again</Button>}>
          {(catalog.error as Error).message}
        </EmptyState>
      ) : (
        <>
          <div className="overflow-hidden rounded-md border border-border">
            <Table aria-label="Pipelines">
              <THead className="border-t-0">
                <tr>
                  <Th>Name</Th>
                  <Th>Steps</Th>
                  <Th>Version</Th>
                  <Th>Default in</Th>
                  <Th>Updated</Th>
                </tr>
              </THead>
              <tbody>
                <Tr className="h-[54px]">
                  <Td>
                    <div className="flex flex-col gap-0.5">
                      <span className="font-semibold text-fg">Standard</span>
                      <span className="text-[12px] text-fg-muted">Built in; every namespace without a default runs it</span>
                    </div>
                  </Td>
                  <Td>
                    <StepChips steps={catalog.data?.standard ?? []} />
                  </Td>
                  <Td>
                    <code className="whitespace-nowrap font-mono text-[12px] text-fg-muted">built-in</code>
                  </Td>
                  <Td className="text-fg-secondary">{onStandard.length ? onStandard.join(", ") : "—"}</Td>
                  <Td className="text-fg-muted">—</Td>
                </Tr>
                {list.map((p, i) => {
                  const d = details[i]?.data as Pipeline | undefined;
                  const latest = Math.max(p.current, ...(d?.history ?? []).map((h) => h.version));
                  const used = (p.namespaces ?? []).filter((n) => mine.has(n));
                  return (
                    <Tr key={p.id} className="h-[54px]">
                      <Td>
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <Link href={`/pipelines/${p.id}`} className="font-semibold text-fg hover:text-fg-accent hover:underline">
                            {p.name}
                          </Link>
                          {p.description && <span className="line-clamp-1 text-[12px] text-fg-muted">{p.description}</span>}
                        </div>
                      </Td>
                      <Td>{d ? <StepChips steps={d.steps.map((s) => toSpec(s))} /> : <span className="skeleton block h-5 w-40" />}</Td>
                      <Td>
                        <span className="flex items-center gap-1.5">
                          <code className="font-mono text-[12px] font-medium text-fg">v{p.current}</code>
                          {latest > p.current && (
                            <span className="h-[18px] rounded-xs bg-blue-surface px-1.5 text-[10.5px] font-semibold leading-[18px] text-fg-accent">draft v{latest}</span>
                          )}
                        </span>
                      </Td>
                      <Td className="text-fg-secondary">{used.length ? used.join(", ") : "—"}</Td>
                      <Td className="tabular whitespace-nowrap text-fg-muted">{relative(p.updated_at)}</Td>
                    </Tr>
                  );
                })}
              </tbody>
            </Table>
          </div>
          {!list.length && (
            <p className="text-[13px] text-fg-secondary">
              No saved pipelines yet. Every namespace runs the standard steps; a pipeline of your own can skip steps, add a prompt template or export files.
            </p>
          )}

          <section aria-labelledby="ns-defaults" className="mt-2 flex flex-col gap-1">
            <SectionTitle>
              <span id="ns-defaults">Namespace defaults</span>
            </SectionTitle>
            <p className="-mt-2 mb-2 text-[13px] text-fg-secondary">
              What imports, watched folders and Reprocess run in each namespace unless they pick something else. Runs pin the version they started with.
            </p>
            <div className="overflow-hidden rounded-md border border-border">
              <Table aria-label="Namespace defaults">
                <THead className="border-t-0">
                  <tr>
                    <Th>Namespace</Th>
                    <Th>Default pipeline</Th>
                  </tr>
                </THead>
                <tbody>
                  {namespaces.map((n) => {
                    const cur = usedBy.get(n.name);
                    const allowed = can("owner", n.name);
                    const select = (
                      <Select
                        aria-label={`Default pipeline for ${n.name}`}
                        className="h-8 w-[280px] text-[13px]"
                        value={cur == null ? "" : String(cur)}
                        disabled={!allowed || setDefault.isPending}
                        onChange={(e) => setDefault.mutate({ ns: n.name, pipeline: e.target.value ? Number(e.target.value) : null })}
                        options={[{ value: "", label: "Standard (built in)" }, ...list.map((p) => ({ value: String(p.id), label: `${p.name} · v${p.current}` }))]}
                      />
                    );
                    return (
                      <Tr key={n.name} className="h-12">
                        <Td className="font-medium text-fg">{n.name}</Td>
                        <Td>
                          {allowed ? (
                            select
                          ) : (
                            <Tooltip content={needRole("owner", n.name)}>
                              <span tabIndex={0} className="inline-flex">
                                {select}
                              </span>
                            </Tooltip>
                          )}
                        </Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
            </div>
          </section>
        </>
      )}
    </div>
  );
}
