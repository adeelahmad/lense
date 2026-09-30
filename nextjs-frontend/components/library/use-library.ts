"use client";

import { useInfiniteQuery, useQueries, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useRef } from "react";

import { Recordings, Sources, Speakers } from "@/app/openapi-client";
import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { isActiveJob, latestJobs } from "@/components/library/model";
import { data, useApiClient } from "@/lib/api/browser";
import { useJobs } from "@/lib/hooks/jobs";
import { useArchive } from "@/lib/hooks/session";

/** Rows fetched per request. The backend orders by date, newest first, and has no total, so we page by offset. */
export const PAGE = 200;

export function recordingsKey(ns: string | null) {
  return ["recordings", "library", ns ?? "*"] as const;
}

/**
 * The library's data: recordings (paged, newest first), the latest job of each, and the namespace totals.
 * While any job is queued or running, the rows refresh so statuses update in place.
 */
export function useLibrary(ns: string | null) {
  const client = useApiClient();
  const qc = useQueryClient();
  const { namespaces } = useArchive();
  const jobs = useJobs({ limit: 200 });
  const running = Boolean(jobs.data?.running);

  const recordings = useInfiniteQuery({
    queryKey: recordingsKey(ns),
    initialPageParam: 0,
    queryFn: ({ pageParam }) => data(Recordings.listRecordings({ client, query: { ns: ns ?? undefined, limit: PAGE, offset: pageParam } })),
    getNextPageParam: (last, pages) => (last.length < PAGE ? undefined : pages.length * PAGE),
    refetchInterval: running ? 10_000 : 60_000,
  });

  // When a job finishes (or fails), the recording's status changed: refresh the rows now rather than on the next tick.
  const active = useMemo(() => (jobs.data?.jobs ?? []).filter((j) => isActiveJob(j)).map((j) => j.id).join(","), [jobs.data]);
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
    for (const page of recordings.data?.pages ?? []) {
      for (const r of page) {
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

  return {
    rows,
    jobsByRecording,
    jobsCounts: { running: counts.running ?? 0, queued: counts.queued ?? 0, failed: counts.failed ?? 0 },
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
  return { watches: list, sources: sources.data ?? [], loading: watches.isLoading, enabled: owner };
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
  const pending = dirs.flatMap((d) => (d.data?.speakers ?? []).filter((sp) => (sp.suggestions ?? []).length > 0)).slice(0, 40);
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
