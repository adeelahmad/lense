"use client";

import { useQuery } from "@tanstack/react-query";

import { Batches, Jobs } from "@/app/openapi-client";
import type { Job } from "@/app/openapi-client/types.gen";
import { data, useApiClient } from "@/lib/api/browser";

const ACTIVE = new Set(["running", "sample", "paused"]);

export function useBatch(id: number) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["batch", id],
    queryFn: () => data(Batches.getBatch({ client, path: { bid: id } })),
    refetchInterval: (q) => (q.state.data && ACTIVE.has(q.state.data.status) ? 4000 : false),
  });
}

export function useBatchResults(id: number, enabled = true) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["batch-results", id],
    queryFn: () => data(Batches.getBatchResults({ client, path: { bid: id } })),
    enabled,
    staleTime: 5_000,
  });
}

export type BatchJob = Job & {
  batch?: number | null;
  started_at?: string | null;
  finished_at?: string | null;
  attempts?: number | null;
};

/** The batch's jobs, one per recording (the jobs list has no batch filter, so recent jobs are filtered here). */
export function useBatchJobs(id: number, active: boolean) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["batch-jobs", id],
    queryFn: async () =>
      (await data(Jobs.listJobs({ client, query: { limit: 500 } }))).jobs.filter(
        (j) => (j as BatchJob).batch === id,
      ) as BatchJob[],
    refetchInterval: active ? 4000 : false,
  });
}

export function useBatchList() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["batches"],
    queryFn: () => data(Batches.listBatches({ client })),
    refetchInterval: (q) => ((q.state.data ?? []).some((b) => ACTIVE.has(b.status)) ? 5000 : false),
  });
}
