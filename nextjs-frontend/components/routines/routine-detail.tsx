"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, ChevronRight, Undo2 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Routines } from "@/app/openapi-client";
import { ActivityPanel, BudgetPanel, Cost, HeldNotice } from "@/components/costs/costs";
import type { RoutineRun } from "@/app/openapi-client/types.gen";
import { useNames } from "@/components/routines/data";
import { RoutineEditor } from "@/components/routines/routine-editor";
import { DeleteRoutineDialog, RunButton, StatusBadge, useRoutineActions } from "@/components/routines/routines-page";
import {
  ACTION_LABEL,
  actionText,
  resultText,
  took,
  touchesGraph,
  triggerText,
  type RoutineAction,
} from "@/components/routines/routine-model";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/field";
import { DateTime, EmptyState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

function RunLog({ id }: { id: number }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["routine-run", id],
    queryFn: () => data(Routines.getRun({ client, path: { run_id: id } })),
  });
  if (q.isLoading) return <Skeleton className="h-16 w-full" />;
  if (q.error) return <p className="text-[12.5px] text-red-dark">{(q.error as Error).message}</p>;
  const lines = q.data?.log ?? [];
  return lines.length ? (
    <pre className="max-h-[320px] overflow-auto rounded-sm bg-term-bg p-3 font-mono text-[12px] leading-relaxed text-term-fg">
      {lines.join("\n")}
    </pre>
  ) : (
    <p className="text-[12.5px] text-fg-muted">Nothing in the log.</p>
  );
}

function RunRow({ run, onUndo }: { run: RoutineRun; onUndo: (r: RoutineRun) => void }) {
  const [open, setOpen] = useState(false);
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const decide = useMutation({
    mutationFn: (go: boolean) => data(Routines.decideHeldRun({ client, path: { run_id: run.id }, body: { run: go } })),
    onSuccess: (_, go) => {
      void qc.invalidateQueries({ queryKey: ["routine-runs", run.routine] });
      void qc.invalidateQueries({ queryKey: ["routine", run.routine] });
      toast(
        go
          ? { tone: "green", title: "Running it once", body: "It starts within half a minute." }
          : { title: "Skipped" },
      );
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t do that", body: e.message }),
  });
  const applied = run.changes?.applied ?? 0;
  const proposed = run.changes?.proposed ?? 0;
  const results = (run.results ?? []) as Record<string, unknown>[];
  return (
    <li className="border-b border-border last:border-b-0">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1.5 px-4 py-3">
        <button
          type="button"
          aria-expanded={open}
          onClick={() => setOpen(!open)}
          className="flex min-w-0 flex-1 items-center gap-2.5 text-left"
        >
          {open ? (
            <ChevronDown aria-hidden className="size-4 shrink-0 text-fg-muted" />
          ) : (
            <ChevronRight aria-hidden className="size-4 shrink-0 text-fg-muted" />
          )}
          <StatusBadge status={run.status === "running" ? null : run.status} running={run.status === "running"} />
          <span className="text-[13.5px] font-semibold text-fg">
            <DateTime iso={run.started_at} />
          </span>
          <span className="text-[12.5px] text-fg-muted">
            {triggerText(run.trigger, run.by)}
            {took(run.started_at, run.finished_at) ? ` · took ${took(run.started_at, run.finished_at)}` : ""}
          </span>
        </button>
        {(run.cost_usd != null || run.tokens != null) && (
          <Cost
            usd={run.cost_usd}
            tokens={run.tokens}
            estimate={run.cost_estimate}
            className="text-[12.5px] text-fg-secondary"
          />
        )}
        {proposed > 0 && (
          <Link
            href={`/routines/changes?run=${run.id}`}
            className="text-[12.5px] font-semibold text-fg-accent hover:underline"
          >
            {plural(proposed, "proposed change")}
          </Link>
        )}
        {applied > 0 && (
          <Button size="xs" variant="danger-ghost" icon={<Undo2 />} onClick={() => onUndo(run)}>
            Undo this run’s {plural(applied, "change")}
          </Button>
        )}
      </div>
      <div
        className={cn(
          "flex flex-col gap-2 pl-[42px] pr-4",
          (open || results.length > 0 || run.error || run.hold) && "pb-3",
        )}
      >
        {run.status === "held" ? (
          <HeldNotice
            hold={run.hold as { why?: string; missed?: number }}
            busy={decide.isPending}
            onRun={() => decide.mutate(true)}
            onSkip={() => decide.mutate(false)}
          />
        ) : run.hold?.why ? (
          <p className="text-[12.5px] text-fg-secondary">{String(run.hold.why)}</p>
        ) : null}
        {results.length > 0 && (
          <ol className="flex flex-col gap-0.5 text-[12.5px]">
            {results.map((r, i) => (
              <li key={i} className={r.status === "error" ? "text-red-dark" : "text-fg-secondary"}>
                <span className="font-semibold text-fg">
                  {i + 1}. {ACTION_LABEL[r.type as RoutineAction["type"]] ?? String(r.type)}:
                </span>{" "}
                {resultText(r)}
              </li>
            ))}
          </ol>
        )}
        {run.error && <p className="text-[12.5px] text-red-dark">{run.error}</p>}
        {open && <RunLog id={run.id} />}
      </div>
    </li>
  );
}

/** A routine: its recent runs (with their logs, and undo for graph changes) and its settings. */
export function RoutineDetail({ id, tab: initialTab }: { id: number; tab?: "runs" | "costs" | "settings" }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { admin, me } = useArchive();
  const names = useNames();
  const { toggle } = useRoutineActions();
  const [tab, setTab] = useState<"runs" | "costs" | "settings">(initialTab ?? "runs");
  const [undoing, setUndoing] = useState<RoutineRun | null>(null);
  const [deleting, setDeleting] = useState(false);

  const q = useQuery({
    queryKey: ["routine", id],
    queryFn: () => data(Routines.getRoutine({ client, path: { rid: id } })),
    enabled: admin,
    refetchInterval: (query) => (query.state.data?.running ? 5_000 : 30_000),
  });
  const r = q.data;
  const runs = useQuery({
    queryKey: ["routine-runs", id],
    queryFn: () => data(Routines.listRuns({ client, path: { rid: id }, query: { limit: 30 } })),
    enabled: admin,
    refetchInterval: r?.running ? 5_000 : 30_000,
  });

  const undo = useMutation({
    mutationFn: (run: RoutineRun) => data(Routines.undoRun({ client, path: { run_id: run.id } })),
    onSuccess: (res) => {
      setUndoing(null);
      void qc.invalidateQueries({ queryKey: ["routine-runs", id] });
      void qc.invalidateQueries({ queryKey: ["graph-changes"] });
      toast(
        res.failed
          ? {
              tone: "red",
              title: `Undid ${plural(res.undone, "change")}; ${res.failed} couldn’t be undone`,
              body: "An entity in them was deleted since.",
            }
          : { tone: "green", title: `Undid ${plural(res.undone, "change")}` },
      );
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t undo the run", body: e.message }),
  });

  if (me && !admin)
    return (
      <EmptyState icon={<CalendarClock />} title="Routines are for admins">
        Admins set up what the archive does on a schedule.
      </EmptyState>
    );
  if (q.isLoading || !me)
    return (
      <div className="flex flex-col gap-4 p-6" aria-busy="true" aria-label="Loading routine">
        <Skeleton className="h-7 w-72" />
        <SkeletonRows rows={4} />
      </div>
    );
  if (q.error || !r) {
    const e = q.error as ApiError | null;
    return (
      <EmptyState
        tone={e?.status === 404 ? "neutral" : "error"}
        icon={<CalendarClock />}
        title={e?.status === 404 ? "This routine doesn’t exist" : "Couldn’t load the routine"}
        actions={
          <Button asChild variant="secondary">
            <Link href="/routines">All routines</Link>
          </Button>
        }
      >
        {e?.message}
      </EmptyState>
    );
  }

  const actions = r.actions as RoutineAction[];
  const list = runs.data ?? [];
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <nav aria-label="Breadcrumb" className="text-[12px] font-medium text-fg-muted">
        <Link href="/routines" className="hover:text-fg hover:underline">
          Routines
        </Link>{" "}
        › {r.name}
      </nav>
      <div className="flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">{r.name}</h1>
          <p className="mt-1 text-[13.5px] text-fg-secondary">
            Runs {r.schedule_text}
            {r.schedule ? ` (${r.timezone})` : ""}
            {r.enabled && r.next_run_at ? (
              <>
                {" "}
                · next <DateTime iso={r.next_run_at} />
              </>
            ) : null}{" "}
            · {r.namespace_names == null ? "every namespace" : r.namespace_names.filter(Boolean).join(", ")}
          </p>
          {r.description && <p className="mt-1 text-[13px] text-fg-muted">{r.description}</p>}
        </div>
        <Switch
          checked={r.enabled}
          label="On"
          disabled={toggle.isPending}
          onCheckedChange={(v) => toggle.mutate({ routine: r, enabled: v })}
        />
        <RunButton routine={r} graph={touchesGraph(actions, names)} />
        <Button size="sm" variant="danger-ghost" onClick={() => setDeleting(true)}>
          Delete
        </Button>
      </div>
      <ol className="flex flex-col gap-1 text-[13px] text-fg">
        {actions.map((a, i) => (
          <li key={i}>
            {i + 1}. {actionText(a, names)}
          </li>
        ))}
      </ol>
      <Tabs
        aria-label="Runs and settings"
        value={tab}
        onChange={(v) => setTab(v as "runs" | "costs" | "settings")}
        items={[
          { value: "runs", label: "Runs", count: runs.data?.length },
          { value: "costs", label: "Costs and activity" },
          { value: "settings", label: "Settings" },
        ]}
      />
      {tab === "settings" ? (
        <RoutineEditor key={r.id} routine={r} onSaved={() => setTab("runs")} />
      ) : tab === "costs" ? (
        <div className="flex flex-col gap-4">
          <BudgetPanel resource={`routine:${r.id}`} what="this routine" />
          <ActivityPanel resource={`routine:${r.id}`} />
        </div>
      ) : runs.isLoading ? (
        <SkeletonRows rows={3} />
      ) : runs.error ? (
        <EmptyState
          tone="error"
          icon={<CalendarClock />}
          title="Couldn’t load the runs"
          actions={<Button onClick={() => runs.refetch()}>Try again</Button>}
        >
          {(runs.error as Error).message}
        </EmptyState>
      ) : list.length ? (
        <ul aria-label="Recent runs" className="overflow-hidden rounded-md border border-border">
          {list.map((run) => (
            <RunRow key={run.id} run={run} onUndo={setUndoing} />
          ))}
        </ul>
      ) : (
        <p className="text-[13px] text-fg-secondary">
          No runs yet.{" "}
          {r.enabled && r.next_run_at
            ? "It runs at the next scheduled time, or press Run now."
            : "Press Run now, or turn it on to run on its schedule."}
        </p>
      )}

      <Dialog
        open={Boolean(undoing)}
        onOpenChange={(o) => !o && setUndoing(null)}
        title="Undo this run’s changes?"
        description={
          undoing
            ? `This takes back the ${plural(undoing.changes?.applied ?? 0, "merge or link", "merges and links")} the run made. Changes it only proposed stay to review.`
            : undefined
        }
        actions={
          <>
            <Button variant="ghost" onClick={() => setUndoing(null)}>
              Cancel
            </Button>
            <Button variant="danger" disabled={undo.isPending} onClick={() => undoing && undo.mutate(undoing)}>
              Undo changes
            </Button>
          </>
        }
      />
      <DeleteRoutineDialog
        routine={deleting ? r : null}
        onOpenChange={setDeleting}
        onDeleted={() => router.push("/routines")}
      />
    </div>
  );
}

/** A new routine's settings. */
export function NewRoutine() {
  const { admin, me } = useArchive();
  if (me && !admin)
    return (
      <EmptyState icon={<CalendarClock />} title="Routines are for admins">
        Admins set up what the archive does on a schedule.
      </EmptyState>
    );
  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <nav aria-label="Breadcrumb" className="text-[12px] font-medium text-fg-muted">
        <Link href="/routines" className="hover:text-fg hover:underline">
          Routines
        </Link>{" "}
        › New routine
      </nav>
      <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">New routine</h1>
      <RoutineEditor />
    </div>
  );
}
