"use client";

import { keepPreviousData, useInfiniteQuery, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { Resources, Sources, Speakers } from "@/app/openapi-client";
import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { isActiveJob, latestJobs, speakerChoices, type LibraryQuery } from "@/components/library/model";
import { data, page, useApiClient } from "@/lib/api/browser";
import { useJobs } from "@/lib/hooks/jobs";
import { useArchive } from "@/lib/hooks/session";

/** Rows fetched per request; the server filters, sorts and counts, and we page by offset. */
export const PAGE = 200;

export function recordingsKey(query: LibraryQuery) {
  return ["recordings", "library", query] as const;
}

/**
 * The library's data: the recordings matching `query` (paged, with how many match in all), the latest job of each,
 * and the namespace totals. While any job is queued or running, the rows refresh so statuses update in place.
 */
export function useLibrary(ns: string | null, query: LibraryQuery) {
  const client = useApiClient();
  const qc = useQueryClient();
  const { namespaces: full, partialNamespaces } = useArchive();
  const namespaces = [...full, ...partialNamespaces];
  const jobs = useJobs({ limit: 200 });
  const running = Boolean(jobs.data?.running);

  const recordings = useInfiniteQuery({
    queryKey: recordingsKey(query),
    initialPageParam: 0,
    queryFn: ({ pageParam }) =>
      page(Resources.listRecordings({ client, query: { ...query, limit: PAGE, offset: pageParam } })),
    getNextPageParam: (last, pages) => {
      const loaded = pages.reduce((a, p) => a + p.items.length, 0);
      return last.items.length && loaded < last.total ? loaded : undefined;
    },
    refetchInterval: running ? 10_000 : 60_000,
    // Other filters keep showing the last rows (dimmed) until the new ones arrive, rather than a skeleton.
    placeholderData: keepPreviousData,
  });

  // When a job finishes (or fails), the recording's status changed: refresh the rows now rather than on the next tick.
  const active = useMemo(
    () =>
      (jobs.data?.jobs ?? [])
        .filter((j) => isActiveJob(j))
        .map((j) => j.id)
        .join(","),
    [jobs.data],
  );
  const prevActive = useRef(active);
  useEffect(() => {
    const before = prevActive.current.split(",").filter(Boolean);
    const now = new Set(active.split(",").filter(Boolean));
    if (before.some((id) => !now.has(id))) void qc.invalidateQueries({ queryKey: ["recordings"] });
    prevActive.current = active;
  }, [active, qc]);

  const rows: RecordingSummary[] = useMemo(() => {
    const seen = new Set<number>();
    const out: RecordingSummary[] = [];
    for (const p of recordings.data?.pages ?? []) {
      for (const r of p.items) {
        if (!seen.has(r.id)) {
          seen.add(r.id);
          out.push(r);
        }
      }
    }
    return out;
  }, [recordings.data]);

  const jobsByRecording = useMemo(() => latestJobs(jobs.data?.jobs), [jobs.data]);
  const scope = namespaces.filter((n) => !ns || n.name === ns);
  const total = scope.reduce((a, n) => a + ((n.recordings as number) ?? 0), 0);
  const ms = scope.reduce((a, n) => a + ((n.ms as number) ?? 0), 0);
  const counts = jobs.data?.counts ?? {};

  const pages = recordings.data?.pages;
  return {
    rows,
    /** How many recordings match the query, on every page (null until the first page arrives). */
    matching: pages?.length ? pages[pages.length - 1].total : null,
    jobsByRecording,
    jobsCounts: {
      running: counts.running ?? 0,
      queued: counts.queued ?? 0,
      failed: counts.failed ?? 0,
    },
    total,
    ms,
    recordings,
    jobs,
  };
}

/** Watched folders feeding these namespaces (admins see all; owners see theirs), and the sources behind them (admins). */
export function useWatchedSources(ns: string | null) {
  const client = useApiClient();
  const { admin, can } = useArchive();
  const owner = admin || can("owner");
  const watches = useQuery({
    queryKey: ["watches"],
    queryFn: () => data(Sources.listWatches({ client })),
    enabled: owner,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
  const sources = useQuery({
    queryKey: ["sources"],
    queryFn: () => data(Sources.listSources({ client })),
    enabled: admin,
    staleTime: 30_000,
  });
  const list = (watches.data ?? []).filter((w) => !ns || w.namespace === ns);
  return {
    watches: list,
    sources: sources.data ?? [],
    loading: watches.isLoading,
    enabled: owner,
  };
}

/**
 * Voice matches waiting for a person, per recording ("◆ 1 voice match to review"). The speakers list says which
 * speakers have suggestions; each such speaker's recordings say where they appear. Capped, as the queue is short.
 */
export function useReviewsByRecording(nsList: string[]): Map<number, number> {
  const client = useApiClient();
  const dirs = useQueries({
    queries: nsList.map((ns) => ({
      queryKey: ["speakers", ns],
      queryFn: () => data(Speakers.listSpeakers({ client, query: { ns } })),
      staleTime: 60_000,
    })),
  });
  const pending = dirs
    .flatMap((d) => (d.data?.speakers ?? []).filter((sp) => (sp.suggestions ?? []).length > 0))
    .slice(0, 40);
  const recs = useQueries({
    queries: pending.map((sp) => ({
      queryKey: ["speaker-recordings", sp.id],
      queryFn: () => data(Speakers.listSpeakerRecordings({ client, path: { sid: sp.id } })),
      staleTime: 60_000,
    })),
  });
  const key = recs.map((q) => q.dataUpdatedAt).join(",");
  return useMemo(() => {
    const out = new Map<number, number>();
    for (const q of recs) for (const r of q.data ?? []) out.set(r.id, (out.get(r.id) ?? 0) + 1);
    return out;
    // `recs` is a new array on every render; `key` changes exactly when its data does.
  }, [key]);
}

/** How many recordings need attention and how many are processing, in this namespace (or all of them). */
export function useLibraryCounts(ns: string | null) {
  const client = useApiClient();
  const jobs = useJobs({ limit: 200 });
  const running = Boolean(jobs.data?.running);
  const count = (extra: Pick<LibraryQuery, "attention" | "processing" | "edited_by">) => ({
    queryKey: ["recordings", "count", ns ?? "*", extra] as const,
    queryFn: async () =>
      (await page(Resources.listRecordings({ client, query: { ns: ns ?? undefined, ...extra, limit: 1 } }))).total,
    refetchInterval: running ? 10_000 : 60_000,
  });
  const attention = useQuery(count({ attention: true }));
  const processing = useQuery(count({ processing: true }));
  const mine = useQuery(count({ edited_by: "me" }));
  return { attention: attention.data, processing: processing.data, mine: mine.data };
}

/** Everyone who speaks in these namespaces, merged by name, for the speaker filter. */
export function useSpeakerChoices(nsList: string[]) {
  const client = useApiClient();
  const dirs = useQueries({
    queries: nsList.map((ns) => ({
      queryKey: ["speakers", ns],
      queryFn: () => data(Speakers.listSpeakers({ client, query: { ns } })),
      staleTime: 60_000,
    })),
  });
  const key = dirs.map((d) => d.dataUpdatedAt).join(",");
  return useMemo(
    () => ({
      choices: speakerChoices(dirs.flatMap((d) => d.data?.speakers ?? [])),
      loading: dirs.some((d) => d.isLoading),
    }),
    // `dirs` is a new array on every render; `key` changes exactly when its data does.
    [key],
  );
}

/** The tags on the recordings in scope (one namespace, or all you can read), most used first. */
/** Where the recordings in scope came from, with how many each (the Source filter). */
export function useOrigins(ns: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["recording-origins", ns],
    queryFn: () => data(Resources.listOrigins({ client, query: ns ? { ns } : {} })),
    staleTime: 60_000,
  });
}

/** The languages of the recordings in scope, with how many each (the Language filter). */
export function useLanguages(ns: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["recording-languages", ns],
    queryFn: () => data(Resources.listLanguages({ client, query: ns ? { ns } : {} })),
    staleTime: 60_000,
  });
}

export function useTagCounts(ns: string | null) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["recording-tags", ns],
    queryFn: () => data(Resources.listTags({ client, query: ns ? { ns } : {} })),
    staleTime: 30_000,
  });
}
