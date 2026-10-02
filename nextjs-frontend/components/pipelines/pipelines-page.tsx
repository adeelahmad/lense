"use client";

import { useMutation, useQueries, useQueryClient } from "@tanstack/react-query";
import { Workflow } from "lucide-react";
import Link from "next/link";
import { useMemo, useState } from "react";

import { Namespaces, Pipelines } from "@/app/openapi-client";
import type { Pipeline } from "@/app/openapi-client/types.gen";
import { CatalogHeader, useContentTypes, usePipelineCatalog } from "@/components/pipelines/catalog-header";
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
  const overrides = useMemo(() => {
    const m = new Map<string, { subtype: string; pipeline: number }[]>();
    for (const p of list)
      for (const u of p.content_types ?? [])
        m.set(u.namespace, [...(m.get(u.namespace) ?? []), { subtype: u.content_type, pipeline: p.id }]);
    return m;
  }, [list]);
  const types = useContentTypes();
  const subtypes = types.data?.types ?? [];
  const typeLabel = (k: string) => subtypes.find((t) => t.key === k)?.label ?? k;
  const [adding, setAdding] = useState<Record<string, { subtype: string; pipeline: string }>>({});

  const setForType = useMutation({
    mutationFn: ({ ns, kind, pipeline }: { ns: string; kind: string; pipeline: number | null }) =>
      data(Namespaces.updateNamespace({ client, path: { name: ns }, body: { pipelines: { [kind]: pipeline } } })),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      setAdding((a) => ({ ...a, [v.ns]: { subtype: "", pipeline: "" } }));
      toast({
        tone: "green",
        title: `${typeLabel(v.kind)} in ${v.ns}: ${v.pipeline == null ? "no override" : (list.find((p) => p.id === v.pipeline)?.name ?? "that pipeline")}`,
        body: "New runs use it; runs in progress keep theirs.",
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change it", body: e.message }),
  });

  const setDefault = useMutation({
    mutationFn: ({ ns, pipeline }: { ns: string; pipeline: number | null }) =>
      data(
        Namespaces.updateNamespace({
          client,
          path: { name: ns },
          body: { pipeline },
        }),
      ),
    onSuccess: (_d, v) => {
      void qc.invalidateQueries({ queryKey: ["pipelines"] });
      toast({
        tone: "green",
        title: `${v.ns} now runs ${v.pipeline == null ? "the standard pipeline" : (list.find((p) => p.id === v.pipeline)?.name ?? "that pipeline")}`,
        body: "New runs use it; runs in progress keep theirs.",
      });
    },
    onError: (e: Error) =>
      toast({
        tone: "red",
        title: "Couldn’t change the default",
        body: e.message,
      }),
  });

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <CatalogHeader tab="pipelines" />
      {catalog.isLoading ? (
        <SkeletonRows rows={4} />
      ) : catalog.error ? (
        <EmptyState
          tone="error"
          icon={<Workflow />}
          title="Couldn’t load pipelines"
          actions={<Button onClick={() => catalog.refetch()}>Try again</Button>}
        >
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
                      <span className="text-[12px] text-fg-muted">
                        Built in; every namespace without a default runs it
                      </span>
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
                          <Link
                            href={`/pipelines/${p.id}`}
                            className="font-semibold text-fg hover:text-fg-accent hover:underline"
                          >
                            {p.name}
                          </Link>
                          {p.description && (
                            <span className="line-clamp-1 text-[12px] text-fg-muted">{p.description}</span>
                          )}
                        </div>
                      </Td>
                      <Td>
                        {d ? (
                          <StepChips steps={d.steps.map((s) => toSpec(s))} />
                        ) : (
                          <span className="skeleton block h-5 w-40" />
                        )}
                      </Td>
                      <Td>
                        <span className="flex items-center gap-1.5">
                          <code className="font-mono text-[12px] font-medium text-fg">v{p.current}</code>
                          {latest > p.current && (
                            <span className="h-[18px] rounded-xs bg-blue-surface px-1.5 text-[10.5px] font-semibold leading-[18px] text-fg-accent">
                              draft v{latest}
                            </span>
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
              No saved pipelines yet. Every namespace runs the standard steps; a pipeline of your own can skip steps,
              add a prompt template or export files.
            </p>
          )}

          <section aria-labelledby="ns-defaults" className="mt-2 flex flex-col gap-1">
            <SectionTitle>
              <span id="ns-defaults">Namespace defaults</span>
            </SectionTitle>
            <p className="-mt-2 mb-2 text-[13px] text-fg-secondary">
              What imports, watched folders and Reprocess run in each namespace unless they pick something else. Runs
              pin the version they started with.
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
                        onChange={(e) =>
                          setDefault.mutate({
                            ns: n.name,
                            pipeline: e.target.value ? Number(e.target.value) : null,
                          })
                        }
                        options={[
                          { value: "", label: "Standard (built in)" },
                          ...list.map((p) => ({
                            value: String(p.id),
                            label: `${p.name} · v${p.current}`,
                          })),
                        ]}
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

          <section aria-labelledby="ns-types" className="mt-2 flex flex-col gap-1">
            <SectionTitle>
              <span id="ns-types">Namespace overrides by content type</span>
            </SectionTitle>
            <p className="-mt-2 mb-2 text-[13px] text-fg-secondary">
              A namespace’s own pipeline for one content type, in place of the type’s pipeline (set in{" "}
              <Link href="/content-types" className="font-semibold text-fg-accent hover:underline">
                Content types
              </Link>
              ) and the namespace default.
            </p>
            <div className="overflow-x-auto rounded-md border border-border">
              <Table aria-label="Namespace overrides by content type">
                <THead className="border-t-0">
                  <tr>
                    <Th>Namespace</Th>
                    <Th>Overrides</Th>
                    <Th>Add one</Th>
                  </tr>
                </THead>
                <tbody>
                  {namespaces.map((n) => {
                    const allowed = can("owner", n.name);
                    const mineOver = overrides.get(n.name) ?? [];
                    const draft = adding[n.name] ?? { subtype: "", pipeline: "" };
                    return (
                      <Tr key={n.name} className="h-12">
                        <Td className="font-medium text-fg">{n.name}</Td>
                        <Td>
                          {mineOver.length ? (
                            <span className="flex flex-wrap gap-1.5">
                              {mineOver.map((o) => (
                                <span
                                  key={o.subtype}
                                  className="inline-flex h-6 items-center gap-1 rounded-pill border border-border px-2 text-[12px] text-fg"
                                >
                                  {typeLabel(o.subtype)} → {list.find((p) => p.id === o.pipeline)?.name}
                                  {allowed && (
                                    <button
                                      type="button"
                                      aria-label={`Remove the override for ${typeLabel(o.subtype)}`}
                                      className="text-fg-muted hover:text-red-dark"
                                      onClick={() => setForType.mutate({ ns: n.name, kind: o.subtype, pipeline: null })}
                                    >
                                      ×
                                    </button>
                                  )}
                                </span>
                              ))}
                            </span>
                          ) : (
                            <span className="text-fg-muted">—</span>
                          )}
                        </Td>
                        <Td>
                          <span className="flex items-center gap-1.5">
                            <Select
                              aria-label={`Content type to override in ${n.name}`}
                              size="sm"
                              className="w-[190px]"
                              value={draft.subtype}
                              disabled={!allowed}
                              title={allowed ? undefined : needRole("owner", n.name)}
                              onChange={(e) =>
                                setAdding((a) => ({ ...a, [n.name]: { ...draft, subtype: e.target.value } }))
                              }
                              options={[
                                { value: "", label: "Content type…" },
                                ...subtypes.map((t) => ({ value: t.key, label: `${t.base} · ${t.label}` })),
                              ]}
                            />
                            <Select
                              aria-label={`Pipeline for it in ${n.name}`}
                              size="sm"
                              className="w-[170px]"
                              value={draft.pipeline}
                              disabled={!allowed}
                              onChange={(e) =>
                                setAdding((a) => ({ ...a, [n.name]: { ...draft, pipeline: e.target.value } }))
                              }
                              options={[
                                { value: "", label: "Pipeline…" },
                                ...list.map((p) => ({ value: String(p.id), label: p.name })),
                              ]}
                            />
                            <Button
                              size="xs"
                              variant="secondary"
                              disabled={!allowed || !draft.subtype || !draft.pipeline || setForType.isPending}
                              onClick={() =>
                                setForType.mutate({ ns: n.name, kind: draft.subtype, pipeline: Number(draft.pipeline) })
                              }
                            >
                              Add
                            </Button>
                          </span>
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
