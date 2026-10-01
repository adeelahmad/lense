"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRightLeft, Cpu, Pause, Play, Server } from "lucide-react";
import Link from "next/link";
import type { ReactNode } from "react";

import { Jobs } from "@/app/openapi-client";
import type { WorkerInfo } from "@/app/openapi-client/types.gen";
import {
  pauseNote,
  stepLabel,
  stepSpecs,
  workerLoad,
  type JobRecord,
  type WorkerState,
} from "@/components/activity/job-model";
import { useJobList, useWorkerSettings, useWorkerStates, useWorkers } from "@/components/activity/use-activity";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { relative } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const BADGE: Record<WorkerState, { tone: "intent" | "neutral" | "gate"; word: string }> = {
  busy: { tone: "intent", word: "Busy" },
  idle: { tone: "neutral", word: "Idle" },
  paused: { tone: "neutral", word: "Paused" },
  silent: { tone: "gate", word: "Silent" },
};

type Action = "pause" | "drain" | "resume";

/** Pause, drain or resume a worker (admins), then show what it does now. */
function useWorkerControl() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ name, action }: { name: string; action: Action }) =>
      data(Jobs.controlWorker({ client, path: { name, action } })),
    onSuccess: (w, v) => {
      void qc.invalidateQueries({ queryKey: ["workers"] });
      toast({
        title: { pause: `${v.name} paused`, drain: `Draining ${v.name}`, resume: `${v.name} resumed` }[v.action],
        body: v.action === "resume" ? "It takes runs again." : (pauseNote(w) ?? undefined),
      });
    },
    onError: (e: Error, v) => toast({ tone: "red", title: `Couldn’t ${v.action} ${v.name}`, body: e.message }),
  });
}

function WorkerCard({
  w,
  state,
  jobs,
  staleMinutes,
  maxAttempts,
  control,
}: {
  w: WorkerInfo;
  state: WorkerState;
  jobs: JobRecord[];
  staleMinutes: number;
  maxAttempts: number;
  control: ReturnType<typeof useWorkerControl>;
}) {
  const current = w.current != null ? jobs.find((j) => j.id === Number(w.current)) : undefined;
  const steps = w.steps ?? [];
  const canTake = jobs.filter((j) => j.status === "queued" && j.next_step && steps.includes(j.next_step)).length;
  const beat = w.heartbeat_at ? relative(w.heartbeat_at) : "never";
  const quietMin = w.heartbeat_at ? Math.round((Date.now() - Date.parse(w.heartbeat_at)) / 60_000) : null;
  const b = BADGE[state];
  const Icon = state === "silent" ? Cpu : Server;
  let now: ReactNode;
  if (current) {
    const step = stepSpecs(current.steps)[
      Math.min(current.step_index ?? 0, Math.max(0, (current.steps?.length ?? 1) - 1))
    ];
    now = (
      <Link href={`/activity/${current.id}`} className="hover:underline">
        {step?.label ?? stepLabel(current.next_step ?? "")} · {current.title ?? `Recording ${current.recording}`}
      </Link>
    );
  } else if (w.current != null)
    now = (
      <Link href={`/activity/${String(w.current)}`} className="hover:underline">
        Run #{String(w.current)}
      </Link>
    );
  else if (state === "silent")
    now =
      quietMin != null && quietMin >= staleMinutes
        ? `Silent ${quietMin} min — any job it held goes back on the queue (up to ${maxAttempts} attempts).`
        : `Silent ${quietMin ?? "?"} min. After ${staleMinutes} min its job goes back on the queue.`;
  else now = "Nothing running";
  const busy = w.current != null && w.current !== "";
  const note = pauseNote(w);
  const pending = control.isPending && control.variables?.name === w.name;
  const act = (action: Action) => control.mutate({ name: w.name, action });

  return (
    <article
      aria-label={w.name}
      className={cn(
        "flex flex-col gap-3.5 rounded-lg border border-border bg-surface p-[18px]",
        state === "silent" && "opacity-[.85]",
      )}
    >
      <div className="flex items-center gap-2.5">
        <span className="grid size-[34px] shrink-0 place-items-center rounded-[10px] bg-surface-neutral text-fg-secondary">
          <Icon aria-hidden className="size-[18px]" />
        </span>
        <span className="flex min-w-0 flex-1 flex-col gap-[3px]">
          <span className="truncate text-[14.5px] font-bold leading-tight text-fg">{w.name}</span>
          <span className="truncate text-[12px] text-fg-muted">
            {w.host ?? "unknown host"} · {state === "silent" ? "last heartbeat" : "heartbeat"} {beat}
          </span>
        </span>
        <Badge tone={b.tone} dot>
          {b.word}
        </Badge>
      </div>
      <div className="flex flex-wrap gap-1.5" aria-label="Steps it runs">
        {steps.map((s) => (
          <span
            key={s}
            className="h-[22px] rounded-[6px] border border-border px-2 font-mono text-[11.5px] font-medium leading-5 text-fg-secondary"
          >
            {s}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="label-caps">Now · {w.paused ? "takes no new runs" : `can take ${canTake} queued`}</div>
        <div className="text-[13px] leading-snug text-fg-strong">{now}</div>
        {note && <div className="text-[12.5px] leading-snug text-fg-secondary">{note}</div>}
      </div>
      <div className="flex flex-col gap-1.5">
        <div className="label-caps">Load</div>
        <div className="tabular text-[13px] leading-snug text-fg-strong">{workerLoad(w)}</div>
      </div>
      <div className="flex flex-wrap gap-2">
        {w.paused ? (
          <Button variant="secondary" size="sm" icon={<Play />} disabled={pending} onClick={() => act("resume")}>
            Resume
          </Button>
        ) : (
          <Button variant="secondary" size="sm" icon={<Pause />} disabled={pending} onClick={() => act("pause")}>
            Pause
          </Button>
        )}
        <Button
          variant="ghost"
          size="sm"
          icon={<ArrowRightLeft />}
          disabled={pending || !busy || Boolean(w.draining) || state === "silent"}
          disabledReason={
            w.draining
              ? "Already handing its run back"
              : state === "silent"
                ? `It’s silent: its run goes back on the queue after ${staleMinutes} min anyway`
                : !busy
                  ? "It isn’t running anything to hand back"
                  : undefined
          }
          onClick={() => act("drain")}
        >
          Drain
        </Button>
      </div>
    </article>
  );
}

/** A3: every worker with the steps it runs, its heartbeat and what it is doing now. Admins only. */
export function WorkersView() {
  const { admin } = useArchive();
  const workers = useWorkers();
  const list = useJobList({ limit: 200 });
  const states = useWorkerStates(workers.data, list.jobs);
  const { staleMinutes, maxAttempts } = useWorkerSettings();
  const control = useWorkerControl();

  if (!admin)
    return (
      <EmptyState icon={<Server />} title="Workers are visible to admins">
        Your runs are on the Runs tab. An admin can see which machines run which steps.
      </EmptyState>
    );
  if (workers.isLoading)
    return (
      <div className="grid gap-4 p-6 md:grid-cols-2 xl:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-[230px] rounded-lg" />
        ))}
      </div>
    );
  if (workers.error)
    return (
      <EmptyState
        tone="error"
        icon={<Server />}
        title="Couldn’t load workers"
        actions={<Button onClick={() => workers.refetch()}>Try again</Button>}
      >
        {(workers.error as Error).message}
      </EmptyState>
    );
  const ws = [...(workers.data ?? [])].sort(
    (a, b) =>
      (states[a.name] === "silent" ? 1 : 0) - (states[b.name] === "silent" ? 1 : 0) || a.name.localeCompare(b.name),
  );
  if (!ws.length)
    return (
      <EmptyState icon={<Server />} title="No workers have checked in">
        Start the server with background work on (RUN_BACKGROUND=true), or run{" "}
        <code className="font-mono text-[12.5px]">lens worker</code> on another machine.
      </EmptyState>
    );
  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {ws.map((w) => (
          <WorkerCard
            key={w.name}
            w={w}
            state={states[w.name]}
            jobs={list.jobs}
            staleMinutes={staleMinutes}
            maxAttempts={maxAttempts}
            control={control}
          />
        ))}
      </div>
      <p className="max-w-[900px] text-[13px] leading-normal text-fg-secondary">
        Steps each worker runs are shown as chips. A worker that stays silent past {staleMinutes} min (Settings →
        Workers) has its job retried on another worker, up to {maxAttempts} attempts. Pause stops a worker taking new
        runs; the one it has carries on to the end. Drain also hands that run back to the queue after the step it’s on,
        for another worker to carry on. A paused worker stays paused when it restarts under the same name (the server’s
        own workers are named after its process, so they start afresh).
      </p>
    </div>
  );
}
