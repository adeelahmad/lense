"use client";

import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { ChevronRight, Plus, Shapes } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";

import { Entities } from "@/app/openapi-client";
import { DefineDialog } from "@/components/entities/define-dialog";
import { EntityDrawer } from "@/components/entities/entity-drawer";
import {
  alsoKnownAs,
  entityQuery,
  type EntityFilters,
  type EntityRow,
  filtersFrom,
  PAGE,
} from "@/components/entities/model";
import { SetupTab } from "@/components/entities/setup-tab";
import { TypesTab } from "@/components/entities/types-tab";
import { useCollectionTree } from "@/components/library/use-collections";
import { Badge } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { SearchInput, Select } from "@/components/ui/field";
import { EmptyState, PageHeader, SkeletonRows } from "@/components/ui/states";
import { Pagination, SortTh, Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { count, plural, shortDate } from "@/lib/format";
import { needRole, useArchive } from "@/lib/hooks/session";

/** The entities of one namespace: who and what its recordings mention, with what people wrote about each. */
export function EntitiesPage() {
  const params = useSearchParams();
  const router = useRouter();
  const pathname = usePathname();
  const client = useApiClient();
  const { namespaces, namespace: topNs, can } = useArchive();
  const [defining, setDefining] = useState(false);
  const ns =
    params.get("ns") && namespaces.some((n) => n.name === params.get("ns"))
      ? params.get("ns")!
      : (topNs ?? namespaces[0]?.name ?? null);
  const f = filtersFrom(params, ns ?? "");
  const open = Number(params.get("entity")) || null;
  const view = (["hidden", "types", "setup"] as const).find((v) => v === params.get("view")) ?? "all";
  const listing = view === "all" || view === "hidden";

  const set = (changes: Record<string, string | null>, keepPage = false) => {
    const p = new URLSearchParams(params.toString());
    for (const [k, v] of Object.entries(changes)) {
      if (v) p.set(k, v);
      else p.delete(k);
    }
    if (!keepPage && !("page" in changes)) p.delete("page");
    router.replace(`${pathname}?${p}`);
  };

  // The search box answers as you type; the address follows a moment later.
  const [q, setQ] = useState(f.q);
  useEffect(() => setQ(f.q), [f.q]);
  useEffect(() => {
    if (q === f.q) return;
    const t = setTimeout(() => set({ q: q || null }), 300);
    return () => clearTimeout(t);
  }, [q]);

  const types = useQuery({
    queryKey: ["entity-types", ns],
    queryFn: () => data(Entities.listEntityTypes({ client, query: { ns: ns ?? undefined } })),
    staleTime: 60_000,
  });
  const tree = useCollectionTree(ns);
  const list = useQuery({
    queryKey: ["entities", "page", f],
    queryFn: () => data(Entities.listEntities({ client, query: entityQuery(f) })),
    enabled: Boolean(ns) && listing,
    placeholderData: keepPreviousData,
  });
  const rows = (list.data?.items ?? []) as unknown as EntityRow[];

  if (!namespaces.length)
    return (
      <div className="px-4 py-6 md:px-6">
        <PageHeader title="Entities" />
        <EmptyState icon={<Shapes />} title="No namespaces yet">
          Entities are kept per namespace. Once you have access to one, the people, organisations and topics its
          recordings mention show here.
        </EmptyState>
      </div>
    );

  const sortBy = (key: EntityFilters["sort"]) => ({
    active: f.sort === key,
    dir: (key === "name" ? "asc" : "desc") as "asc" | "desc",
    onSort: () => set({ sort: key === "mentions" ? null : key }),
  });

  return (
    <div className="flex flex-col gap-3 px-4 py-6 md:px-6">
      <PageHeader
        className="mb-0"
        title="Entities"
        meta={ns && list.data && listing ? `${ns} · ${plural(list.data.total, "entity", "entities")}` : undefined}
        actions={
          <span className="flex flex-wrap items-center gap-3">
            {ns && (
              <Button
                size="sm"
                variant="primary"
                icon={<Plus />}
                disabled={!can("editor", ns)}
                disabledReason={!can("editor", ns) ? needRole("editor", ns) : undefined}
                onClick={() => setDefining(true)}
              >
                Add entity
              </Button>
            )}
            {namespaces.length > 1 && (
              <label className="flex items-center gap-2 text-[13px] font-semibold text-fg-secondary">
                Namespace
                <Select
                  size="sm"
                  className="w-[200px]"
                  value={ns ?? ""}
                  onChange={(e) => set({ ns: e.target.value, collection: null, entity: null })}
                  options={namespaces.map((n) => n.name)}
                  aria-label="Namespace"
                />
              </label>
            )}
          </span>
        }
      />
      <Tabs
        aria-label="Entity views"
        value={view}
        onChange={(v) => set({ view: v === "all" ? null : v, entity: null })}
        items={[
          { value: "all", label: "Entities" },
          { value: "hidden", label: "Hidden" },
          { value: "types", label: "Types" },
          { value: "setup", label: "Setup" },
        ]}
      />
      {view === "types" && ns && <TypesTab ns={ns} />}
      {view === "setup" && ns && <SetupTab ns={ns} />}
      {listing && (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <SearchInput
              className="w-full sm:w-[280px]"
              placeholder="Find by name or alias"
              aria-label="Find entities"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            <Select
              size="sm"
              className="w-[170px]"
              aria-label="Type"
              value={f.type}
              onChange={(e) => set({ type: e.target.value || null })}
              options={[
                { value: "", label: "All types" },
                ...(types.data ?? []).map((t) => ({ value: t.type, label: t.label })),
              ]}
            />
            <Select
              size="sm"
              className="w-[220px]"
              aria-label="Collection"
              value={f.collection ? String(f.collection) : ""}
              onChange={(e) => set({ collection: e.target.value || null })}
              options={[
                { value: "", label: "Every collection" },
                ...(tree.data ?? []).map((c) => ({
                  value: String(c.id),
                  label: `${"  ".repeat(c.depth ?? 0)}${(c.path ?? [c.name]).at(-1)}`,
                })),
              ]}
            />
          </div>
          {list.isLoading && <SkeletonRows rows={6} />}
          {list.isError && (
            <Banner
              tone="error"
              title="Couldn’t load the entities."
              action={
                <Button size="sm" variant="secondary" onClick={() => list.refetch()}>
                  Try again
                </Button>
              }
            >
              {list.error.message}
            </Banner>
          )}
          {list.data && !rows.length && (
            <EmptyState
              icon={<Shapes />}
              title="No entities here"
              className="rounded-lg border border-border"
              actions={
                f.q || f.type || f.collection ? (
                  <Button size="sm" onClick={() => set({ q: null, type: null, collection: null })}>
                    Clear filters
                  </Button>
                ) : !f.hidden && can("editor", ns) ? (
                  <Button size="sm" variant="primary" onClick={() => setDefining(true)}>
                    Add an entity
                  </Button>
                ) : undefined
              }
            >
              {f.q || f.type || f.collection
                ? "Nothing matches these filters."
                : f.hidden
                  ? "Nobody has hidden an entity in this namespace."
                  : "Entities appear once recordings are analysed."}
            </EmptyState>
          )}
          {list.data && rows.length > 0 && (
            <div className="overflow-hidden rounded-md border border-border">
              <Table aria-label="Entities">
                <THead className="border-t-0">
                  <tr>
                    <SortTh {...sortBy("name")}>Name</SortTh>
                    <Th className="w-[140px]">Type</Th>
                    <Th className="hidden lg:table-cell">Description</Th>
                    <SortTh {...sortBy("mentions")} className="w-[110px]">
                      Mentions
                    </SortTh>
                    <SortTh {...sortBy("recordings")} className="hidden w-[120px] md:table-cell">
                      Recordings
                    </SortTh>
                    <SortTh {...sortBy("recent")} className="hidden w-[130px] md:table-cell">
                      Last said
                    </SortTh>
                    <Th className="w-8">
                      <span className="sr-only">Open</span>
                    </Th>
                  </tr>
                </THead>
                <tbody>
                  {rows.map((e) => {
                    const aka = alsoKnownAs(e);
                    return (
                      <Tr
                        key={e.id}
                        selected={open === e.id}
                        className="h-[50px] cursor-pointer"
                        onClick={() => set({ entity: String(e.id) }, true)}
                      >
                        <Td>
                          <button
                            type="button"
                            className="text-left font-semibold text-fg hover:text-fg-accent hover:underline"
                            onClick={(ev) => {
                              ev.stopPropagation();
                              set({ entity: String(e.id) }, true);
                            }}
                          >
                            {e.name}
                          </button>
                          {e.builtin ? (
                            <Badge tone="gate" className="ml-2">
                              always there
                            </Badge>
                          ) : (
                            e.defined && (
                              <Badge tone="intent" className="ml-2">
                                defined
                              </Badge>
                            )
                          )}
                          {aka.length > 0 && (
                            <span className="block truncate text-[12px] text-fg-muted">also {aka.join(", ")}</span>
                          )}
                        </Td>
                        <Td>{e.builtin ? <span className="text-fg-muted">—</span> : <Badge>{e.type_label}</Badge>}</Td>
                        <Td className="hidden max-w-[360px] lg:table-cell">
                          <span className="line-clamp-2 text-[13px] text-fg-secondary">{e.description || "—"}</span>
                        </Td>
                        <Td className="tabular">{count(e.mentions)}</Td>
                        <Td className="tabular hidden md:table-cell">{count(e.recordings)}</Td>
                        <Td className="tabular hidden text-fg-secondary md:table-cell">
                          {e.last ? shortDate(e.last) : "—"}
                        </Td>
                        <Td>
                          <ChevronRight aria-hidden className="size-4 text-fg-muted" />
                        </Td>
                      </Tr>
                    );
                  })}
                </tbody>
              </Table>
              <Pagination
                offset={f.offset}
                limit={PAGE}
                total={list.data.total}
                loading={list.isFetching}
                onChange={(o) => set({ page: o ? String(o / PAGE + 1) : null })}
              />
            </div>
          )}
        </>
      )}
      {ns && (
        <DefineDialog
          ns={ns}
          types={types.data ?? []}
          open={defining}
          onOpenChange={setDefining}
          onDefined={(id) => {
            setDefining(false);
            set({ entity: String(id), view: null });
          }}
        />
      )}
      {open && ns && listing && (
        <EntityDrawer id={open} ns={ns} types={types.data ?? []} onClose={() => set({ entity: null }, true)} />
      )}
    </div>
  );
}
