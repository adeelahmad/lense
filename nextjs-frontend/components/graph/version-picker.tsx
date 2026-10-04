"use client";

import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";

import { Graph2 as GraphApi } from "@/app/openapi-client";
import { OPS, type Page } from "@/components/routines/history-model";
import { data, useApiClient } from "@/lib/api/browser";

type Tag = { name: string; version: number; note?: string | null };

/** "3 Oct, 14:02": short enough for a menu. */
function when(at: string): string {
  const d = new Date(at);
  return Number.isNaN(d.getTime())
    ? at
    : d.toLocaleString(undefined, { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" });
}

/**
 * Which version of the entity graph the canvas shows: now, a named version, or one of the latest changes
 * (docs/graph-history.md). Shown only once the graph has a history.
 */
export function VersionPicker({
  asOf,
  namespace,
  onChange,
}: {
  asOf: string | null;
  namespace: string | null;
  onChange: (version: string | null) => void;
}) {
  const client = useApiClient();
  const history = useQuery({
    queryKey: ["graph-history", "picker", namespace],
    queryFn: async () =>
      (await data(GraphApi.graphHistoryList({ client, query: { namespace, limit: 30 } }))) as unknown as Page,
    staleTime: 30_000,
  });
  const tags = useQuery({
    queryKey: ["graph-tags"],
    queryFn: async () => (await data(GraphApi.graphTags({ client }))) as unknown as Tag[],
    staleTime: 30_000,
  });
  const versions = history.data?.versions ?? [];
  const named = tags.data ?? [];
  if (!versions.length && !named.length && !asOf) return null;
  const known = new Set([...versions.map((v) => String(v.version - 1)), ...named.map((t) => t.name)]);
  return (
    <label className="inline-flex h-8 items-center gap-1.5 rounded-[7px] pl-2 text-[12.5px] font-semibold text-fg [&_svg]:size-3.5">
      <History aria-hidden className="text-fg-muted" />
      <span className="sr-only">Show the graph as of</span>
      <select
        value={asOf ?? ""}
        onChange={(e) => onChange(e.target.value || null)}
        className="h-8 max-w-[220px] rounded-[7px] border-0 bg-transparent pr-6 text-[12.5px] font-semibold text-fg hover:bg-surface-neutral focus:outline-none focus-visible:ring-2 focus-visible:ring-blue"
      >
        <option value="">Now</option>
        {asOf && !known.has(asOf) && <option value={asOf}>Version {asOf}</option>}
        {named.length > 0 && (
          <optgroup label="Named versions">
            {named.map((t) => (
              <option key={t.name} value={t.name}>
                {t.name} (version {t.version})
              </option>
            ))}
          </optgroup>
        )}
        {versions.length > 0 && (
          <optgroup label="Before a change">
            {versions.map((v) => (
              <option key={v.version} value={String(v.version - 1)}>
                Before {(OPS[v.op] ?? v.op).toLowerCase()} {Object.values(v.names ?? {})[0] ?? ""}, {when(v.at)}
              </option>
            ))}
          </optgroup>
        )}
      </select>
    </label>
  );
}
