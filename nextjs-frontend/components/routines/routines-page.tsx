"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, Ellipsis, Play, Plus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { Routines } from "@/app/openapi-client";
import type { Routine } from "@/app/openapi-client/types.gen";
import { useGraphChanges, useNames, useRoutines } from "@/components/routines/data";
import { routineText, statusOf, touchesGraph, type RoutineAction } from "@/components/routines/routine-model";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuSeparator, MenuTrigger } from "@/components/ui/menu";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Tabs } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

/** "Routines" with the Routines / Proposed changes / History tabs; people who aren't admins see only the graph tabs. */
export function RoutinesHeader({ tab }: { tab: "routines" | "changes" | "history" }) {
  const { admin } = useArchive();
  const routines = useRoutines();
  const proposed = useGraphChanges("proposed");
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2.5">
        <h1 className="flex-1 text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">
          {admin ? "Routines" : "Graph changes"}
        </h1>
        {admin && tab === "routines" && (
          <Button asChild size="sm" variant="primary">
            <Link href="/routines/new">
              <Plus /> New routine
            </Link>
          </Button>
        )}
      </div>
      <Tabs
        aria-label="Routines and graph changes"
        value={tab}
        items={[
          ...(admin
            ? [{ value: "routines", label: "Routines", count: routines.data?.routines.length, href: "/routines" }]
            : []),
          { value: "changes", label: "Proposed changes", count: proposed.data?.length, href: "/routines/changes" },
          { value: "history", label: "History", href: "/routines/history" },
        ]}
      />
    </div>
  );
}

/** Run now, toggle, delete: shared by the list and a routine's page. */
export function useRoutineActions() {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const refresh = (id: number) => {
    void qc.invalidateQueries({ queryKey: ["routines"] });
    void qc.invalidateQueries({ queryKey: ["routine", id] });
  };
  const run = useMutation({
    mutationFn: (v: { routine: Routine; proposeOnly?: boolean }) =>
      data(
        Routines.runRoutine({ client, path: { rid: v.routine.id }, body: { propose_only: Boolean(v.proposeOnly) } }),
      ),
    onSuccess: (_, v) => {
      refresh(v.routine.id);
      void qc.invalidateQueries({ queryKey: ["routine-runs", v.routine.id] });
      toast({
        tone: "green",
        title: v.proposeOnly ? "Run asked for (propose only)" : "Run asked for",
        body: `${v.routine.name} starts within half a minute.`,
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t start the run", body: e.message }),
  });
  const toggle = useMutation({
    mutationFn: (v: { routine: Routine; enabled: boolean }) =>
      data(Routines.updateRoutine({ client, path: { rid: v.routine.id }, body: { enabled: v.enabled } })),
    onSuccess: (_, v) => {
      refresh(v.routine.id);
      toast({ title: v.enabled ? "Routine on" : "Routine off", body: v.routine.name });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t change the routine", body: e.message }),
  });
  return { run, toggle };
}

export function DeleteRoutineDialog({
  routine,
  onOpenChange,
  onDeleted,
}: {
  routine: Routine | null;
  onOpenChange: (open: boolean) => void;
  onDeleted?: () => void;
}) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const del = useMutation({
    mutationFn: (r: Routine) => data(Routines.deleteRoutine({ client, path: { rid: r.id } })),
    onSuccess: (_, r) => {
      void qc.invalidateQueries({ queryKey: ["routines"] });
      toast({ title: "Routine deleted", body: r.name });
      onOpenChange(false);
      onDeleted?.();
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t delete the routine", body: e.message }),
  });
  return (
    <Dialog
      open={Boolean(routine)}
      onOpenChange={onOpenChange}
      title={`Delete “${routine?.name ?? ""}”?`}
      description="Its runs go too. What it already did stays: imported files, queued jobs and graph changes."
      actions={
        <>
          <Button variant="ghost" onClick={() => onOpenChange(false)}>
            Cancel
          </Button>
          <Button variant="danger" disabled={del.isPending} onClick={() => routine && del.mutate(routine)}>
            Delete routine
          </Button>
        </>
      }
    />
  );
}

/** Run now, with "propose only" next to it when the routine organises the graph. */
export function RunButton({ routine, graph, size = "sm" }: { routine: Routine; graph: boolean; size?: "xs" | "sm" }) {
  const { run } = useRoutineActions();
  const busy = Boolean(routine.running) || (run.isPending && run.variables?.routine.id === routine.id);
  const why = routine.running ? "It’s running now" : "Asking…";
  if (!graph || busy)
    return (
      <Button
        size={size}
        variant="secondary"
        icon={<Play />}
        disabled={busy}
        disabledReason={why}
        onClick={() => run.mutate({ routine })}
      >
        Run now
      </Button>
    );
  return (
    <Menu>
      <MenuTrigger asChild>
        <Button size={size} variant="secondary" icon={<Play />}>
          Run now <ChevronDown />
        </Button>
      </MenuTrigger>
      <MenuContent align="end" className="w-[280px]">
        <MenuItem onSelect={() => run.mutate({ routine })}>Run now</MenuItem>
        <MenuItem onSelect={() => run.mutate({ routine, proposeOnly: true })}>
          Run now, only propose graph changes
        </MenuItem>
      </MenuContent>
    </Menu>
  );
}

export function StatusBadge({ status, running }: { status?: string | null; running?: boolean }) {
  if (running)
    return (
      <Badge tone="intent" dot>
        Running
      </Badge>
    );
  if (!status) return <span className="text-fg-muted">Never run</span>;
  const s = statusOf(status);
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

/** The routines tab: what runs when, the last outcome, on/off and Run now. */
export function RoutinesPage() {
  const { admin, me } = useArchive();
  const router = useRouter();
  const routines = useRoutines();
  const names = useNames();
  const { toggle } = useRoutineActions();
  const [deleting, setDeleting] = useState<Routine | null>(null);
  const list = routines.data?.routines ?? [];

  if (me && !admin)
    return (
      <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
        <RoutinesHeader tab="changes" />
        <EmptyState
          icon={<CalendarClock />}
          title="Routines are for admins"
          actions={
            <Button asChild>
              <Link href="/routines/changes">See proposed graph changes</Link>
            </Button>
          }
        >
          Admins set up what the archive does on a schedule. You can review the graph changes it proposes for namespaces
          you edit.
        </EmptyState>
      </div>
    );

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <RoutinesHeader tab="routines" />
      {routines.isLoading || !me ? (
        <SkeletonRows rows={4} />
      ) : routines.error ? (
        <EmptyState
          tone="error"
          icon={<CalendarClock />}
          title="Couldn’t load routines"
          actions={<Button onClick={() => routines.refetch()}>Try again</Button>}
        >
          {(routines.error as Error).message}
        </EmptyState>
      ) : !list.length ? (
        <EmptyState
          icon={<CalendarClock />}
          title="No routines yet"
          actions={
            <Button asChild variant="primary">
              <Link href="/routines/new">
                <Plus /> New routine
              </Link>
            </Button>
          }
        >
          A routine does things on a schedule, like a cron job: sync sources, run pipelines on new recordings, or
          organise the entity graph overnight.
        </EmptyState>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Routines">
            <THead className="border-t-0">
              <tr>
                <Th>Routine</Th>
                <Th>When</Th>
                <Th>Next run</Th>
                <Th>Last run</Th>
                <Th>On</Th>
                <Th>
                  <span className="sr-only">Actions</span>
                </Th>
              </tr>
            </THead>
            <tbody>
              {list.map((r) => {
                const actions = r.actions as RoutineAction[];
                const ns = r.namespace_names;
                return (
                  <Tr key={r.id} className="h-[62px]">
                    <Td className="max-w-[420px]">
                      <div className="flex min-w-0 flex-col gap-0.5">
                        <Link
                          href={`/routines/${r.id}`}
                          className="font-semibold text-fg hover:text-fg-accent hover:underline"
                        >
                          {r.name}
                        </Link>
                        <span className="line-clamp-2 text-[12px] text-fg-muted">
                          {routineText(actions, names)} ·{" "}
                          {ns == null ? "every namespace" : ns.filter(Boolean).join(", ")}
                        </span>
                      </div>
                    </Td>
                    <Td className="text-[13px] text-fg-secondary">
                      <span className="text-fg">{r.schedule_text}</span>
                      {r.schedule && <span className="block text-[12px] text-fg-muted">{r.timezone}</span>}
                    </Td>
                    <Td className="whitespace-nowrap text-[13px] text-fg-secondary">
                      {r.enabled && r.next_run_at ? <DateTime iso={r.next_run_at} /> : "—"}
                    </Td>
                    <Td className="whitespace-nowrap text-[13px]">
                      <div className="flex flex-col items-start gap-0.5">
                        <StatusBadge status={r.last_status} running={r.running} />
                        {r.last_run_at && <DateTime iso={r.last_run_at} className="text-[12px] text-fg-muted" />}
                      </div>
                    </Td>
                    <Td>
                      <Switch
                        aria-label={`${r.name} on`}
                        checked={r.enabled}
                        disabled={toggle.isPending && toggle.variables?.routine.id === r.id}
                        onCheckedChange={(v) => toggle.mutate({ routine: r, enabled: v })}
                      />
                    </Td>
                    <Td>
                      <div className="flex items-center justify-end gap-1.5">
                        <RunButton routine={r} graph={touchesGraph(actions, names)} />
                        <Menu>
                          <MenuTrigger asChild>
                            <button
                              type="button"
                              aria-label={`More for ${r.name}`}
                              className="grid size-8 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                            >
                              <Ellipsis aria-hidden className="size-[18px]" />
                            </button>
                          </MenuTrigger>
                          <MenuContent align="end">
                            <MenuItem onSelect={() => router.push(`/routines/${r.id}`)}>Runs</MenuItem>
                            <MenuItem onSelect={() => router.push(`/routines/${r.id}?tab=settings`)}>Edit</MenuItem>
                            <MenuSeparator />
                            <MenuItem danger onSelect={() => setDeleting(r)}>
                              Delete…
                            </MenuItem>
                          </MenuContent>
                        </Menu>
                      </div>
                    </Td>
                  </Tr>
                );
              })}
            </tbody>
          </Table>
        </div>
      )}
      <p className="text-[12.5px] text-fg-muted">
        Routines run on the server’s scheduler, which looks every half minute. A routine that is off still runs when you
        press Run now.
      </p>
      <DeleteRoutineDialog routine={deleting} onOpenChange={(o) => !o && setDeleting(null)} />
    </div>
  );
}
