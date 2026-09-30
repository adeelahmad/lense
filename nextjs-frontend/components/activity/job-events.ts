"use client";

/**
 * One live feed of job changes per browser tab (GET /api/v1/events, server-sent events), shared by the top-bar
 * drawer, the Activity page and every job list in the React Query cache. While the stream is down, lists fall back
 * to polling; after it reconnects it resumes from the last change it saw, so nothing is missed.
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useSession } from "next-auth/react";
import { useEffect, useRef, useSyncExternalStore } from "react";

import type { JobList } from "@/app/openapi-client/types.gen";
import { applyCount, applyJobEvent, type JobRecord } from "@/components/activity/job-model";
import { streamSSE } from "@/lib/api/sse";

export type FeedStatus = "connecting" | "live" | "polling";
type Listener = (job: JobRecord) => void;

const listeners = new Set<Listener>();
const openListeners = new Set<() => void>();
const statusListeners = new Set<() => void>();
let status: FeedStatus = "connecting";
let controller: AbortController | null = null;
let token: string | undefined;
let stopTimer: ReturnType<typeof setTimeout> | undefined;
/** Lists were fetched when the page mounted; only a reconnect needs a catch-up refetch. */
let openedBefore = false;

function setStatus(s: FeedStatus) {
  if (s === status) return;
  status = s;
  statusListeners.forEach((l) => l());
}

function wait(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve) => {
    const t = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(t);
      resolve();
    });
  });
}

async function run(accessToken: string, signal: AbortSignal) {
  let since = "";
  let failures = 0;
  while (!signal.aborted) {
    try {
      const path = `/api/v1/events${since ? `?since=${encodeURIComponent(since)}` : ""}`;
      const onOpen = () => {
        failures = 0;
        setStatus("live");
        if (openedBefore) openListeners.forEach((l) => l());
        openedBefore = true;
      };
      for await (const msg of streamSSE(path, {
        accessToken,
        signal,
        onOpen,
      })) {
        if (msg.event !== "job") continue;
        let job: JobRecord;
        try {
          job = JSON.parse(msg.data) as JobRecord;
        } catch {
          continue;
        }
        if (job.updated_at && job.updated_at > since) since = job.updated_at;
        listeners.forEach((l) => l(job));
      }
    } catch {
      if (signal.aborted) return;
      failures += 1;
    }
    if (signal.aborted) return;
    setStatus("polling");
    // Back off 2, 4, 8… up to 30 s between attempts.
    await wait(Math.min(30_000, 1000 * 2 ** Math.min(failures + 1, 5)), signal);
  }
}

function ensure(accessToken: string | undefined) {
  if (stopTimer) clearTimeout(stopTimer);
  stopTimer = undefined;
  if (!accessToken) {
    setStatus("polling");
    return;
  }
  if (controller && token === accessToken) return;
  controller?.abort();
  token = accessToken;
  controller = new AbortController();
  setStatus("connecting");
  void run(accessToken, controller.signal);
}

function release() {
  if (listeners.size || statusListeners.size) return;
  // Pages hand over quickly (drawer → page); keep the stream a moment instead of reconnecting.
  stopTimer = setTimeout(() => {
    controller?.abort();
    controller = null;
    token = undefined;
    status = "connecting";
    openedBefore = false;
  }, 5000);
}

/** Subscribe to job changes; returns the feed's status (live, or polling while it reconnects). */
export function useJobEvents(onJob?: Listener, onOpen?: () => void): FeedStatus {
  const { data: session } = useSession();
  const accessToken = session?.accessToken;
  const jobRef = useRef(onJob);
  const openRef = useRef(onOpen);
  useEffect(() => {
    jobRef.current = onJob;
    openRef.current = onOpen;
  });

  useEffect(() => {
    const l: Listener = (j) => jobRef.current?.(j);
    const o = () => openRef.current?.();
    listeners.add(l);
    openListeners.add(o);
    ensure(accessToken);
    return () => {
      listeners.delete(l);
      openListeners.delete(o);
      release();
    };
  }, [accessToken]);

  return useSyncExternalStore(
    (cb) => {
      statusListeners.add(cb);
      return () => {
        statusListeners.delete(cb);
        release();
      };
    },
    () => status,
    () => "connecting",
  );
}

type JobsKeyOpts = { status?: string; recording?: number; limit?: number } | undefined;

/** Patch every cached job list (query keys ["jobs", {status, recording, limit}]) with one change. */
export function patchJobLists(qc: QueryClient, job: JobRecord) {
  for (const q of qc.getQueryCache().findAll({ queryKey: ["jobs"] })) {
    const opts = q.queryKey[1] as JobsKeyOpts;
    if (opts && typeof opts !== "object") continue;
    if (opts?.recording != null && Number(opts.recording) !== job.recording) continue;
    const statuses = opts?.status ? opts.status.split(",") : null;
    qc.setQueryData<JobList>(q.queryKey, (old) => {
      if (!old || !Array.isArray(old.jobs)) return old;
      const before = old.jobs.find((j) => j.id === job.id)?.status;
      let jobs = applyJobEvent(old.jobs as JobRecord[], job, statuses);
      if (opts?.limit && jobs.length > opts.limit) jobs = jobs.slice(0, opts.limit);
      const counts = opts?.recording != null ? old.counts : applyCount(old.counts ?? {}, before, job.status);
      const running = Boolean((counts.queued ?? 0) + (counts.running ?? 0));
      return { ...old, jobs, counts, running };
    });
  }
  qc.setQueryData<JobRecord>(["job", job.id], (old) => (old ? { ...old, ...job, log: old.log } : old));
}

/**
 * Keep every job list fresh from the live feed. Mount once (the top bar does); lists refetch when the stream
 * (re)connects, to catch anything that changed while it was down.
 */
export function useJobCacheSync(onJob?: Listener): FeedStatus {
  const qc = useQueryClient();
  return useJobEvents(
    (job) => {
      patchJobLists(qc, job);
      onJob?.(job);
    },
    () => void qc.invalidateQueries({ queryKey: ["jobs"] }),
  );
}
