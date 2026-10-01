"use client";

import { useQueries, useQuery } from "@tanstack/react-query";

import { Metadata, Resources } from "@/app/openapi-client";
import { profileProblems, publishState, type PublishState } from "@/components/iiif/metadata-model";
import { keys, type NamespaceProfile, type RecordingMeta } from "@/components/iiif/queries";
import { data, useApiClient } from "@/lib/api/browser";

export type CollectionItem = {
  id: number;
  title: string;
  recorded_at?: string | null;
  duration_ms?: number | null;
  meta?: RecordingMeta;
  state?: PublishState;
  problems: number;
};

type RecordingRow = {
  id: number;
  title?: string | null;
  recorded_at?: string | null;
  duration_ms?: number | null;
};

/** How many Manifests' metadata is checked at once on the collection page. */
export const PAGE = 50;

/**
 * The recordings of a namespace with each one's publish state (private · published · needs attention), newest first.
 * Metadata is fetched per recording, one page at a time.
 */
export function useCollectionItems(ns: string, offset: number, profile: NamespaceProfile | undefined) {
  const client = useApiClient();
  const list = useQuery({
    queryKey: ["iiif-collection-recordings", ns],
    queryFn: async () =>
      (await data(Resources.listRecordings({ client, query: { ns, limit: 1000 } }))) as unknown as RecordingRow[],
  });
  const rows = (list.data ?? []).slice().sort((a, b) => (b.recorded_at ?? "").localeCompare(a.recorded_at ?? ""));
  const page = rows.slice(offset, offset + PAGE);
  const metas = useQueries({
    queries: page.map((r) => ({
      queryKey: keys.meta(r.id),
      queryFn: async () =>
        (await data(Metadata.getRecordingMetadata({ client, path: { rid: r.id } }))) as unknown as RecordingMeta,
      staleTime: 30_000,
    })),
  });
  const items: CollectionItem[] = page.map((r, i) => {
    const m = metas[i]?.data;
    const problems = m ? (m.problems.length ? m.problems.length : profileProblems(m.meta, profile, ns).length) : 0;
    return {
      id: r.id,
      title: r.title || `Recording ${r.id}`,
      recorded_at: r.recorded_at,
      duration_ms: r.duration_ms,
      meta: m,
      state: m ? publishState(m.meta.access, problems) : undefined,
      problems,
    };
  });
  return {
    list,
    total: rows.length,
    items,
    loadingMeta: metas.some((q) => q.isPending),
  };
}
