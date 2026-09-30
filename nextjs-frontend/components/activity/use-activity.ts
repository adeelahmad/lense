"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { Admin, Jobs, Users } from "@/app/openapi-client";
import type { JobList } from "@/app/openapi-client/types.gen";
import { useJobEvents } from "@/components/activity/job-events";
import { stepSpecs, workerState, type JobRecord, type WorkerState } from "@/components/activity/job-model";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/**
 * A job list kept fresh by the live feed (see job-events.ts). Its key matches lib/hooks/jobs.ts, so the feed patches
 * it in place; it only polls while the feed is down.
 */
export function useJobList(opts: { status?: string; limit?: number } = {}) {
  const client = useApiClient();
  const feed = useJobEvents();
  const key = { status: opts.status, limit: opts.limit ?? 200 };
  const q = useQuery({
    queryKey: ["jobs", key],
    queryFn: () => data(Jobs.listJobs({ client, query: key })),
    refetchInterval: (query) => (feed === "live" ? 60_000 : query.state.data?.running ? 3000 : 20_000),
  });
  return { ...q, feed, jobs: (q.data?.jobs ?? []) as JobRecord[], counts: q.data?.counts ?? {} };
}

/** Workers (admins only): name, steps, host, heartbeat, current job. */
export function useWorkers(enabled = true) {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["workers"],
    queryFn: () => data(Jobs.listWorkers({ client })),
    enabled: admin && enabled,
    refetchInterval: 15_000,
  });
}

/** workers.stale_minutes and workers.max_attempts from Settings (admins can read them). */
export function useWorkerSettings() {
  const client = useApiClient();
  const { admin } = useArchive();
  const q = useQuery({
    queryKey: ["settings"],
    queryFn: () => data(Admin.getSettings({ client })),
    enabled: admin,
    staleTime: 5 * 60_000,
  });
  const values = ((q.data as Record<string, { values?: Record<string, unknown> }> | undefined)?.workers?.values ?? {}) as {
    stale_minutes?: number;
    max_attempts?: number;
  };
  return { staleMinutes: values.stale_minutes ?? 15, maxAttempts: values.max_attempts ?? 3, loaded: q.isSuccess };
}

/** Each worker's state (busy / idle / silent), knowing which jobs are really running. */
export function useWorkerStates(workers: { name: string; heartbeat_at?: string | null; current?: unknown }[] | undefined, jobs: JobRecord[]) {
  return useMemo(() => {
    const running = new Set(jobs.filter((j) => j.status === "running").map((j) => j.id));
    const now = Date.now();
    const out: Record<string, WorkerState> = {};
    for (const w of workers ?? []) out[w.name] = workerState(w, now, running.has(Number(w.current)));
    return out;
  }, [workers, jobs]);
}

/** Emails → names (admins can list people; everyone else sees emails, and "You"). */
export function usePeopleNames(): Record<string, string> {
  const client = useApiClient();
  const { admin, me } = useArchive();
  const q = useQuery({
    queryKey: ["users"],
    queryFn: () => data(Users.listUsers({ client })),
    enabled: admin,
    staleTime: 5 * 60_000,
  });
  return useMemo(() => {
    const out: Record<string, string> = {};
    for (const u of (q.data ?? []) as { email: string; name?: string | null }[]) if (u.name) out[u.email] = u.name;
    if (me?.user.email && me.user.name) out[me.user.email] = me.user.name;
    return out;
  }, [q.data, me]);
}

/** Cancel and retry, with toasts; the lists refresh from the live feed. */
export function useJobActions() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = (id: number) => {
    void qc.invalidateQueries({ queryKey: ["jobs"] });
    void qc.invalidateQueries({ queryKey: ["job", id] });
  };
  const cancel = useMutation({
    mutationFn: (job: JobRecord) => data(Jobs.cancelJob({ client, path: { jid: job.id } })),
    onSuccess: (_d, job) => {
      toast({ title: job.status === "running" ? "Stopping after this step" : "Cancelled", body: job.title ?? undefined });
      refresh(job.id);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t cancel", body: e.message }),
  });
  const retry = useMutation({
    mutationFn: (job: JobRecord) => data(Jobs.retryJob({ client, path: { jid: job.id } })),
    onSuccess: (_d, job) => {
      const step = stepSpecs(job.steps)[Math.min(job.step_index ?? 0, Math.max(0, (job.steps?.length ?? 1) - 1))];
      toast({ title: `Retrying${step ? ` from ${step.label}` : ""}`, body: job.title ?? undefined });
      refresh(job.id);
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t retry", body: e.message }),
  });
  return { cancel, retry };
}

/** Space id → namespace name, for the namespaces this person can read. */
export function useSpaceNames(): Record<number, string> {
  const { namespaces } = useArchive();
  return useMemo(() => Object.fromEntries(namespaces.map((n) => [n.id, n.name])), [namespaces]);
}

export type { JobList };
