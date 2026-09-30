"use client";

import { useQuery } from "@tanstack/react-query";

import { Collections, Entities, Search, Speakers } from "@/app/openapi-client";
import type { BatchSelection } from "@/app/openapi-client/types.gen";
import { useRecordingIndex } from "@/components/search/data";
import { data, useApiClient } from "@/lib/api/browser";

/**
 * The recordings a selection covers, when the app can list them (so you can see and exclude them before running).
 * `complete` is false when the list is cut short (more than the API returns at once).
 */
export function useSelectionRecordings(sel: BatchSelection) {
  const client = useApiClient();
  const index = useRecordingIndex();
  const f = (sel.filter ?? {}) as {
    q?: string;
    namespaces?: string[];
    speakers?: number[];
    entities?: number[];
  };
  const q = useQuery({
    queryKey: ["selection-recordings", sel],
    queryFn: async (): Promise<{ ids: number[]; complete: boolean }> => {
      if (sel.recordings?.length) return { ids: sel.recordings, complete: true };
      if (sel.collection) {
        const c = await data(Collections.getCollection({ client, path: { cid: sel.collection } }));
        return {
          ids: c.recordings.map((r) => r.id),
          complete: c.count <= c.recordings.length,
        };
      }
      if (f.q) {
        const r = await data(
          Search.searchTranscripts({
            client,
            query: { q: f.q, ns: f.namespaces?.[0], limit: 200 },
          }),
        );
        const ids = [...new Set(r.hits.map((h) => h.recording_id))];
        return { ids, complete: r.hits.length >= r.total };
      }
      if (sel.speaker) {
        const r = await data(
          Speakers.listSpeakerRecordings({
            client,
            path: { sid: sel.speaker },
          }),
        );
        return { ids: r.map((x) => x.id), complete: true };
      }
      const ents = sel.entity ? [sel.entity] : (f.entities ?? []);
      if (ents.length) {
        const all = await Promise.all(
          ents.map((eid) =>
            data(
              Entities.listEntityMentions({
                client,
                path: { eid },
                query: { limit: 200 },
              }),
            ),
          ),
        );
        const ids = [...new Set(all.flatMap((m) => m.items.map((x) => Number(x.recording_id))))];
        return { ids, complete: all.every((m) => m.items.length >= m.total) };
      }
      return { ids: [], complete: false };
    },
    enabled: Boolean(
      sel.recordings?.length || sel.collection || f.q || sel.speaker || sel.entity || f.entities?.length,
    ),
    staleTime: 30_000,
  });
  // A whole namespace: straight from the recordings list.
  if (
    sel.namespace &&
    !sel.entity &&
    !sel.speaker &&
    !f.q &&
    !f.entities?.length &&
    !sel.collection &&
    !sel.recordings?.length
  ) {
    const ids = (index.data ?? []).filter((r) => r.namespace === sel.namespace).map((r) => r.id);
    return {
      ids: index.data ? ids : null,
      complete: (index.data?.length ?? 0) < 1000,
      isLoading: index.isLoading,
      byId: index.byId,
    };
  }
  return {
    ids: q.data?.ids ?? null,
    complete: q.data?.complete ?? false,
    isLoading: q.isLoading,
    byId: index.byId,
  };
}
