"use client";

import { useQuery } from "@tanstack/react-query";

import { Jobs } from "@/app/openapi-client";
import { data, useApiClient } from "@/lib/api/browser";

/** Live-ish job list: polls while anything is queued or running, slowly otherwise. */
export function useJobs(opts: { status?: string; recording?: number; limit?: number } = {}) {
  const client = useApiClient();
  return useQuery({
    queryKey: ["jobs", opts],
    queryFn: () => data(Jobs.listJobs({ client, query: { status: opts.status, recording: opts.recording, limit: opts.limit ?? 50 } })),
    refetchInterval: (q) => (q.state.data?.running ? 3000 : 20000),
  });
}

/** Step names of a job (steps may be strings or {type, ...} specs). */
export function jobSteps(steps: unknown[] | null | undefined): string[] {
  return (steps ?? []).map((s) => (typeof s === "string" ? s : String((s as { type?: string; name?: string })?.name ?? (s as { type?: string })?.type ?? "step")));
}
