"use client";

import { useQuery } from "@tanstack/react-query";
import { ChartNoAxesColumn } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Analytics as Api, Namespaces } from "@/app/openapi-client";
import type { ActionCounts } from "@/app/openapi-client/types.gen";
import { DayBars } from "@/components/analytics/day-bars";
import { ACTION_LABEL, ACTIONS, RANGES, rangeDays, totalOf, whyNot } from "@/components/analytics/model";
import { Button } from "@/components/ui/button";
import { EmptyState, PageHeader, Skeleton } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Segmented } from "@/components/ui/tabs";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";

function Counts({ c }: { c: ActionCounts }) {
  return (
    <>
      {ACTIONS.map((a) => (
        <Td key={a} className="tabular text-right text-fg-secondary">
          {c[a] ? count(c[a]) : "—"}
        </Td>
      ))}
    </>
  );
}

function Heads() {
  return (
    <>
      {ACTIONS.map((a) => (
        <Th key={a} className="text-right">
          {ACTION_LABEL[a].many}
        </Th>
      ))}
    </>
  );
}

/**
 * Analytics (docs/analytics.md): what's done with a namespace's resources, per day, per collection and per resource.
 * A namespace's owners see it, an admin of a collection that collection's, admins the whole archive.
 */
export function AnalyticsScreen() {
  const client = useApiClient();
  const { namespace, admin, can, isPartial, loaded } = useArchive();
  const [range, setRange] = useState("30");
  const [collection, setCollection] = useState<number | null>(null);
  const owner = Boolean(namespace) && can("owner", namespace);
  const partial = isPartial(namespace);
  // someone who sees only some collections of the namespace picks one they're an admin of
  const collections = useQuery({
    queryKey: ["collections", namespace],
    queryFn: () => data(Namespaces.listNamespaceCollections({ client, path: { name: namespace! } })),
    enabled: Boolean(namespace),
    staleTime: 60_000,
  });
  const mine = (collections.data ?? []).filter((c) => owner || admin || c.role === "admin");
  const picked = collection != null && mine.some((c) => c.id === collection) ? collection : null;
  const reason = whyNot({ namespace, admin, owner: owner || picked != null, partial: partial || mine.length > 0 });
  const numbers = useQuery({
    queryKey: ["analytics", namespace, picked, range],
    queryFn: () =>
      data(
        Api.getAnalytics({
          client,
          query: { ns: namespace ?? undefined, collection: picked ?? undefined, days: rangeDays(range) },
        }),
      ),
    enabled: loaded && !reason,
    staleTime: 30_000,
  });
  const n = numbers.data;

  return (
    <div className="mx-auto flex w-full max-w-[1100px] flex-col gap-5 px-4 py-6 sm:px-7">
      <PageHeader
        title="Analytics"
        meta={namespace ?? (admin ? "All namespaces" : undefined)}
        actions={<Segmented label="Range" items={RANGES} value={range} onChange={setRange} />}
      >
        <p className="mt-1 max-w-[720px] text-[13.5px] leading-[1.45] text-fg-secondary">
          How the resources here are used: views, plays, searches, downloads and comments. Each is kept with the account
          that did it, never an address or a browser; visitors who aren’t signed in count without one.{" "}
          <Link href="/account/activity" className="font-semibold text-fg-accent hover:underline">
            Your own activity
          </Link>
        </p>
      </PageHeader>
      {namespace && mine.length > 0 && (
        <label className="flex flex-wrap items-center gap-2 text-[13px] font-semibold text-fg-strong">
          Collection
          <select
            value={picked ?? ""}
            onChange={(e) => setCollection(e.target.value ? Number(e.target.value) : null)}
            className="h-9 min-w-0 max-w-full rounded-md border border-border bg-background px-2.5 text-[13.5px] font-normal text-fg"
          >
            {(owner || admin) && <option value="">All of {namespace}</option>}
            {!(owner || admin) && <option value="">Choose…</option>}
            {mine.map((c) => (
              <option key={c.id} value={c.id}>
                {(c.path ?? [c.name]).join(" › ")}
              </option>
            ))}
          </select>
        </label>
      )}
      {!loaded ? (
        <Skeleton className="h-[220px] rounded-md" />
      ) : reason ? (
        <EmptyState icon={<ChartNoAxesColumn />} title="Analytics are for owners">
          {reason}
        </EmptyState>
      ) : numbers.isError ? (
        <EmptyState
          tone="error"
          icon={<ChartNoAxesColumn />}
          title={
            numbers.error instanceof ApiError && numbers.error.status === 403
              ? "Analytics are for owners"
              : "Couldn’t load the analytics"
          }
          actions={<Button onClick={() => numbers.refetch()}>Try again</Button>}
        >
          {numbers.error.message}
        </EmptyState>
      ) : !n ? (
        <Skeleton className="h-[220px] rounded-md" />
      ) : (
        <>
          <dl className="grid grid-cols-2 gap-2.5 sm:grid-cols-3 lg:grid-cols-6">
            {ACTIONS.map((a) => (
              <div key={a} className="flex flex-col gap-1 rounded-md border border-border bg-background px-3.5 py-3">
                <dt className="text-[12px] font-medium text-fg-secondary">{ACTION_LABEL[a].many}</dt>
                <dd className="tabular text-[22px] font-bold leading-none text-fg">{count(n.totals[a] ?? 0)}</dd>
              </div>
            ))}
            <div className="flex flex-col gap-1 rounded-md border border-border bg-background px-3.5 py-3">
              <dt className="text-[12px] font-medium text-fg-secondary">People</dt>
              <dd className="tabular text-[22px] font-bold leading-none text-fg">{count(n.people)}</dd>
              <dd className="text-[11.5px] leading-tight text-fg-muted">
                {n.anonymous ? `and ${count(n.anonymous)} without an account` : "with an account"}
              </dd>
            </div>
          </dl>
          {totalOf(n.totals) === 0 ? (
            <EmptyState icon={<ChartNoAxesColumn />} title="Nothing in this range yet">
              Views, plays, searches, downloads and comments show up here as they happen.
            </EmptyState>
          ) : (
            <>
              <section className="rounded-md border border-border bg-background px-4 py-4">
                {/* only what happened: a measure with nothing in the range would be an empty chart */}
                <DayBars days={n.days} actions={ACTIONS.filter((a) => (n.totals[a] ?? 0) > 0)} />
              </section>
              {(n.collections?.length || n.namespaces?.length) && (
                <section className="flex flex-col gap-2">
                  <h2 className="text-[14px] font-bold text-fg">{n.namespaces ? "Namespaces" : "Collections"}</h2>
                  <div className="overflow-x-auto rounded-md border border-border">
                    <Table aria-label={n.namespaces ? "By namespace" : "By collection"} className="text-[13px]">
                      <THead className="border-t-0">
                        <tr>
                          <Th>{n.namespaces ? "Namespace" : "Collection"}</Th>
                          <Heads />
                        </tr>
                      </THead>
                      <tbody>
                        {(n.namespaces ?? []).map((s) => (
                          <Tr key={s.name} className="h-10 last:border-b-0">
                            <Td className="font-semibold">{s.name}</Td>
                            <Counts c={s} />
                          </Tr>
                        ))}
                        {(n.collections ?? []).map((c) => (
                          <Tr key={c.id} className="h-10 last:border-b-0">
                            <Td className="font-semibold">{(c.path?.length ? c.path : [c.name]).join(" › ")}</Td>
                            <Counts c={c} />
                          </Tr>
                        ))}
                      </tbody>
                    </Table>
                  </div>
                </section>
              )}
              {(n.resources ?? []).length > 0 && (
                <section className="flex flex-col gap-2">
                  <h2 className="text-[14px] font-bold text-fg">Most used resources</h2>
                  <div className="overflow-x-auto rounded-md border border-border">
                    <Table aria-label="Most used resources" className="text-[13px]">
                      <THead className="border-t-0">
                        <tr>
                          <Th>Resource</Th>
                          <Heads />
                        </tr>
                      </THead>
                      <tbody>
                        {(n.resources ?? []).map((r) => (
                          <Tr key={r.id} className="h-10 last:border-b-0">
                            <Td className="max-w-[320px] truncate">
                              <Link href={`/resources/${r.id}`} className="font-semibold text-fg hover:underline">
                                {r.title || `Resource ${r.id}`}
                              </Link>
                              {!namespace && r.namespace && (
                                <span className="ml-2 text-[12px] text-fg-muted">{r.namespace}</span>
                              )}
                            </Td>
                            <Counts c={r} />
                          </Tr>
                        ))}
                      </tbody>
                    </Table>
                  </div>
                </section>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}
