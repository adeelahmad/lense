"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlarmClock, Pencil, Play, Plus } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { Schedules } from "@/app/openapi-client";
import type { Schedule } from "@/app/openapi-client/types.gen";
import { scheduleFor, type Preset } from "@/components/routines/routine-model";
import { SchedulePreview, ZoneInput } from "@/components/routines/schedule-fields";
import {
  FILTERS,
  changesOf,
  counts,
  draftOf,
  filtered,
  lastOf,
  noNext,
  presetsFor,
  whatText,
  type Draft,
  type Filter,
} from "@/components/schedules/model";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Dialog } from "@/components/ui/dialog";
import { Field, Input, Select, Switch } from "@/components/ui/field";
import { DateTime, EmptyState, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { ApiError, data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

export function useSchedules() {
  const client = useApiClient();
  return useQuery({
    queryKey: ["schedules"],
    queryFn: () => data(Schedules.listSchedules({ client })),
    // a routine asked to run starts within half a minute; keep "Running" and the last run fresh
    refetchInterval: 15_000,
  });
}

/** What a run now did, for its toast. */
function ranText(r: Schedule, got: { status: string; why?: string | null; backup?: string | null }): string {
  if (got.status === "asked") return `${r.name} starts within half a minute.`;
  if (got.backup) return `Took ${got.backup}.`;
  if (got.status === "held") return `${got.why ?? "It’s over a budget"}. Decide on its automation’s page.`;
  return got.why ? `${lastOf(got.status).label}: ${got.why}` : `${r.name}: ${lastOf(got.status).label.toLowerCase()}.`;
}

/** When it runs, changed in place: presets, a cron, an at trigger's time, the time zone, and the next runs. */
function EditSchedule({ row, onClose }: { row: Schedule; onClose: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const [d, setDraft] = useState<Draft>(() => draftOf(row));
  const save = useMutation({
    mutationFn: (body: NonNullable<ReturnType<typeof changesOf>>) =>
      data(Schedules.changeSchedule({ client, path: { sid: row.id }, body })),
    onSuccess: (got) => {
      void qc.invalidateQueries({ queryKey: ["schedules"] });
      toast({ tone: "green", title: "Schedule saved", body: `${got.name}: ${got.text}.` });
      onClose();
    },
  });
  const set = (p: Partial<Draft>) => setDraft({ ...d, ...p });
  const cron = row.on === "at" ? null : scheduleFor(d.preset, d.custom);
  const submit = () => {
    const body = changesOf(row, d);
    if (body) save.mutate(body);
    else onClose();
  };
  return (
    <Dialog
      open
      onOpenChange={(o) => !o && onClose()}
      title={`When ${row.name} runs`}
      description={
        row.kind === "automation" ? `Its ${row.trigger} trigger. Saving checks the automation again.` : undefined
      }
      actions={
        <>
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button variant="primary" disabled={save.isPending} onClick={submit}>
            Save
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {row.on === "at" ? (
          <Field label="At" hint="It runs once, at this time">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                type="datetime-local"
                value={d.at}
                onChange={(e) => set({ at: e.target.value })}
              />
            )}
          </Field>
        ) : (
          <Field label="Schedule">
            {({ id }) => (
              <Select
                id={id}
                value={d.preset}
                onChange={(e) => {
                  const preset = e.target.value as Preset;
                  // starting a custom schedule from the one picked before saves retyping it
                  set({
                    preset,
                    custom: preset === "custom" ? (scheduleFor(d.preset, d.custom) ?? d.custom) : d.custom,
                  });
                }}
                options={presetsFor(row)}
              />
            )}
          </Field>
        )}
        {row.on !== "at" && d.preset === "custom" && (
          <Field label="Cron" hint="minute hour day month weekday, e.g. 30 2 * * 1-5; or @daily, @weekly">
            {({ id, describedBy }) => (
              <Input
                id={id}
                aria-describedby={describedBy}
                mono
                value={d.custom}
                onChange={(e) => set({ custom: e.target.value })}
              />
            )}
          </Field>
        )}
        {(row.on === "at" || d.preset !== "manual") && (
          <Field label="Time zone">
            {({ id }) => <ZoneInput id={id} value={d.timezone} onChange={(timezone) => set({ timezone })} />}
          </Field>
        )}
        {row.on !== "at" && (
          <SchedulePreview
            schedule={cron}
            timezone={d.timezone.trim() || "UTC"}
            none={row.kind === "backups" ? "No backups are taken on a schedule." : undefined}
          />
        )}
        {save.error && (
          <p role="alert" className="text-[12.5px] text-red-dark">
            {(save.error as Error).message}
          </p>
        )}
      </div>
    </Dialog>
  );
}

/** The crontab: every routine, automation schedule and the backup, soonest first, each switched, changed and run here. */
export function SchedulesPage() {
  const { admin, me } = useArchive();
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const list = useSchedules();
  const [filter, setFilter] = useState<Filter>("all");
  const [editing, setEditing] = useState<Schedule | null>(null);
  const [overBudget, setOverBudget] = useState<{ row: Schedule; why: string } | null>(null);
  const rows = list.data?.schedules ?? [];
  const n = counts(rows);
  const shown = filtered(rows, n[filter] ? filter : "all");
  const refresh = () => void qc.invalidateQueries({ queryKey: ["schedules"] });

  const toggle = useMutation({
    mutationFn: (v: { row: Schedule; enabled: boolean }) =>
      data(Schedules.changeSchedule({ client, path: { sid: v.row.id }, body: { enabled: v.enabled } })),
    onSuccess: (got) => {
      refresh();
      void qc.invalidateQueries({ queryKey: ["routines"] });
      void qc.invalidateQueries({ queryKey: ["automations"] });
      toast({
        title: got.enabled ? "On" : "Off",
        body: got.enabled ? `${got.name}: ${got.text}.` : `${got.name} won’t run on its own.`,
      });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t switch it", body: e.message }),
  });
  const run = useMutation({
    mutationFn: (v: { row: Schedule; overBudget?: boolean }) =>
      data(Schedules.runSchedule({ client, path: { sid: v.row.id }, body: { over_budget: Boolean(v.overBudget) } })),
    onSuccess: (got, v) => {
      setOverBudget(null);
      refresh();
      const bad = ["failed", "error"].includes(got.status);
      toast({ tone: bad ? "red" : "green", title: bad ? "The run failed" : "Run now", body: ranText(v.row, got) });
    },
    onError: (e: Error, v) => {
      // over budget: say where it stands and let the person choose (docs/budgets.md)
      if (e instanceof ApiError && e.status === 409) setOverBudget({ row: v.row, why: e.message });
      else toast({ tone: "red", title: "Couldn’t run it", body: e.message });
    },
  });
  const switchTo = (row: Schedule, on: boolean) => {
    // the backup can't be on without a schedule: choosing one switches it on
    if (on && row.kind === "backups" && !row.cron) setEditing(row);
    else toggle.mutate({ row, enabled: on });
  };

  return (
    <div className="flex flex-col gap-4 px-4 pb-10 pt-[18px] md:px-6">
      <div className="flex flex-col gap-1">
        <div className="flex flex-wrap items-center gap-2.5">
          <h1 className="flex-1 text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">Schedules</h1>
          {admin && (
            <>
              {/* the Routines page (the list, proposed graph changes, history) lives behind this one */}
              <Button asChild size="sm">
                <Link href="/routines">Routines</Link>
              </Button>
              <Button asChild size="sm" variant="primary">
                <Link href="/routines/new">
                  <Plus /> New routine
                </Link>
              </Button>
            </>
          )}
        </div>
        <p className="text-[13px] text-fg-secondary">
          Everything that runs on a clock, in one place:{" "}
          {admin ? "routines, your automations and backups" : "your automations"}.
        </p>
      </div>
      {list.isLoading || !me ? (
        <SkeletonRows rows={4} />
      ) : list.error ? (
        <EmptyState
          tone="error"
          icon={<AlarmClock />}
          title="Couldn’t load schedules"
          actions={<Button onClick={() => list.refetch()}>Try again</Button>}
        >
          {(list.error as Error).message}
        </EmptyState>
      ) : !rows.length ? (
        <EmptyState
          icon={<AlarmClock />}
          title="Nothing runs on a clock yet"
          // New routine is in the header already
          actions={
            <Button asChild>
              <Link href="/automations">Automations</Link>
            </Button>
          }
        >
          A routine or an automation with a schedule shows here, with when it runs next, how it last went and a switch.
        </EmptyState>
      ) : (
        <>
          {Object.values(n).filter(Boolean).length > 2 && (
            <Segmented
              label="Show"
              value={n[filter] ? filter : "all"}
              onChange={(v) => setFilter(v as Filter)}
              className="self-start"
              items={FILTERS.filter((f) => n[f.value]).map((f) => ({
                value: f.value,
                label: (
                  <>
                    {f.label} <span className="tabular text-fg-muted">{n[f.value]}</span>
                  </>
                ),
              }))}
            />
          )}
          <div className="overflow-hidden rounded-md border border-border">
            <Table aria-label="Schedules">
              <THead className="border-t-0">
                <tr>
                  <Th>What</Th>
                  <Th>When</Th>
                  <Th>Next run</Th>
                  <Th className="hidden md:table-cell">Last run</Th>
                  <Th>On</Th>
                  <Th>
                    <span className="sr-only">Actions</span>
                  </Th>
                </tr>
              </THead>
              <tbody>
                {shown.map((r) => {
                  const last = lastOf(r.running ? "running" : r.last_status);
                  const why = noNext(r);
                  return (
                    <Tr key={r.id} className="h-[62px]">
                      <Td className="max-w-[360px]">
                        <div className="flex min-w-0 flex-col gap-0.5">
                          <Link href={r.href} className="font-semibold text-fg hover:text-fg-accent hover:underline">
                            {r.name}
                          </Link>
                          <span className="line-clamp-2 text-[12px] text-fg-muted">{whatText(r)}</span>
                        </div>
                      </Td>
                      <Td className="text-[13px] text-fg-secondary">
                        <span className="text-fg">{r.text}</span>
                        {(r.cron || r.on === "at") && (
                          <span className="block text-[12px] text-fg-muted">
                            {r.cron && <span className="font-mono">{r.cron} · </span>}
                            {r.timezone}
                          </span>
                        )}
                      </Td>
                      <Td className="whitespace-nowrap text-[13px] text-fg-secondary">
                        {r.next_run_at ? (
                          <DateTime iso={r.next_run_at} />
                        ) : (
                          <span className="text-fg-muted">{why ?? "—"}</span>
                        )}
                      </Td>
                      <Td className="hidden whitespace-nowrap text-[13px] md:table-cell">
                        {r.last_run_at || r.running ? (
                          <div className="flex flex-col items-start gap-0.5">
                            <Badge tone={last.tone} dot>
                              {last.label}
                            </Badge>
                            {r.last_run_at && <DateTime iso={r.last_run_at} className="text-[12px] text-fg-muted" />}
                          </div>
                        ) : (
                          <span className="text-fg-muted">Never run</span>
                        )}
                      </Td>
                      <Td>
                        <Switch
                          aria-label={`${r.name}${r.trigger ? ` (${r.trigger})` : ""} on`}
                          checked={r.enabled}
                          disabled={toggle.isPending && toggle.variables?.row.id === r.id}
                          onCheckedChange={(v) => switchTo(r, v)}
                        />
                      </Td>
                      <Td>
                        <div className="flex items-center justify-end gap-1.5">
                          <IconButton
                            label={`Change when ${r.name}${r.trigger ? ` (${r.trigger})` : ""} runs`}
                            onClick={() => setEditing(r)}
                          >
                            <Pencil />
                          </IconButton>
                          <Button
                            size="sm"
                            variant="secondary"
                            icon={<Play />}
                            aria-label={`Run ${r.name}${r.trigger ? ` (${r.trigger})` : ""} now`}
                            disabled={r.running || (run.isPending && run.variables?.row.id === r.id)}
                            disabledReason={r.running ? "It’s running now" : "Starting…"}
                            onClick={() => run.mutate({ row: r })}
                          >
                            Run now
                          </Button>
                        </div>
                      </Td>
                    </Tr>
                  );
                })}
              </tbody>
            </Table>
          </div>
          <p className="text-[12.5px] text-fg-muted">
            Run now works when something is switched off too. An automation’s switch turns the whole automation on or
            off, whatever starts it.
          </p>
        </>
      )}
      {editing && <EditSchedule key={editing.id} row={editing} onClose={() => setEditing(null)} />}
      <Dialog
        open={Boolean(overBudget)}
        onOpenChange={(o) => !o && setOverBudget(null)}
        title="Run it over budget?"
        description={overBudget?.why.replace(/ Run it anyway with over_budget\.$/, "")}
        actions={
          <>
            <Button variant="ghost" onClick={() => setOverBudget(null)}>
              Don’t run
            </Button>
            <Button
              variant="primary"
              disabled={run.isPending}
              onClick={() => overBudget && run.mutate({ row: overBudget.row, overBudget: true })}
            >
              Run anyway
            </Button>
          </>
        }
      />
    </div>
  );
}
