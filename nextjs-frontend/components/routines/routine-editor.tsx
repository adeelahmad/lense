"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useId, useMemo, useState } from "react";

import { Routines } from "@/app/openapi-client";
import type { Routine, Watch } from "@/app/openapi-client/types.gen";
import { usePipelineCatalog, useWorkflowCatalog } from "@/components/pipelines/catalog-header";
import { useWatches, watchName } from "@/components/routines/data";
import {
  ACTION_LABEL,
  PICK_LABEL,
  PRESETS,
  suggestedName,
  actionProblem,
  cleanActions,
  newAction,
  presetOf,
  scheduleFor,
  type Pick,
  type Preset,
  type RoutineAction,
} from "@/components/routines/routine-model";
import { Banner } from "@/components/ui/banner";
import { Button, IconButton } from "@/components/ui/button";
import { Checkbox, Field, Input, Select, Switch, Textarea } from "@/components/ui/field";
import { Menu, MenuContent, MenuItem, MenuTrigger } from "@/components/ui/menu";
import { Panel } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";

function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

function zones(): string[] {
  try {
    const f = (Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    return f ? ["UTC", ...f("timeZone")] : ["UTC"];
  } catch {
    return ["UTC"];
  }
}

/** "Fri 3 Oct, 03:00" in the routine's time zone. */
function when(iso: string, tz: string): string {
  const o: Intl.DateTimeFormatOptions = {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  };
  try {
    return new Date(iso).toLocaleString("en-GB", { ...o, timeZone: tz });
  } catch {
    return new Date(iso).toLocaleString("en-GB", o);
  }
}

function useDebounced<T>(value: T, ms = 400): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** What the schedule means and its next five runs, from the server, as it's typed. */
function SchedulePreview({ schedule, timezone }: { schedule: string | null; timezone: string }) {
  const client = useApiClient();
  const s = useDebounced(schedule);
  const tz = useDebounced(timezone);
  const q = useQuery({
    queryKey: ["schedule-preview", s, tz],
    queryFn: () => data(Routines.previewSchedule({ client, query: { schedule: s!, timezone: tz || "UTC" } })),
    enabled: Boolean(s),
    retry: false,
    staleTime: 60_000,
  });
  if (!schedule) return <p className="text-[12.5px] text-fg-muted">Runs only when someone presses Run now.</p>;
  if (q.isLoading || s !== schedule || tz !== timezone)
    return <p className="text-[12.5px] text-fg-muted">Checking the schedule…</p>;
  if (q.error)
    return (
      <p role="alert" className="text-[12.5px] text-red-dark">
        {(q.error as Error).message}
      </p>
    );
  if (!q.data) return null;
  return (
    <div className="rounded-sm border border-border bg-surface px-3 py-2 text-[12.5px]">
      <p className="font-semibold text-fg">Runs {q.data.text}</p>
      {q.data.next.length ? (
        <ul className="mt-1 flex flex-col gap-0.5 text-fg-secondary">
          {q.data.next.map((t) => (
            <li key={t} className="tabular">
              {when(t, q.data.timezone)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-fg-muted">It never comes round.</p>
      )}
    </div>
  );
}

function PickSelect({ value, onChange }: { value: Pick | undefined; onChange: (v: Pick) => void }) {
  return (
    <Field label="On which recordings" hint="New ones are those that came in since the routine last ran">
      {({ id, describedBy }) => (
        <Select
          id={id}
          aria-describedby={describedBy}
          value={value ?? "new"}
          onChange={(e) => onChange(e.target.value as Pick)}
          options={(Object.keys(PICK_LABEL) as Pick[]).map((k) => ({
            value: k,
            label: PICK_LABEL[k][0].toUpperCase() + PICK_LABEL[k].slice(1),
          }))}
        />
      )}
    </Field>
  );
}

function LimitInput({ value, onChange }: { value: number | null | undefined; onChange: (v: number | null) => void }) {
  return (
    <Field label="At most" optional hint="Recordings a run takes; 500 when empty">
      {({ id, describedBy }) => (
        <Input
          id={id}
          aria-describedby={describedBy}
          type="number"
          min={1}
          max={5000}
          className="w-[140px]"
          value={value ?? ""}
          onChange={(e) => onChange(e.target.value ? Math.round(Number(e.target.value)) : null)}
        />
      )}
    </Field>
  );
}

/** One action's settings. */
function ActionSettings({
  action: a,
  onChange,
  watches,
}: {
  action: RoutineAction;
  onChange: (a: RoutineAction) => void;
  watches: Watch[];
}) {
  const pipelines = usePipelineCatalog();
  const workflows = useWorkflowCatalog();
  const wfs = workflows.data?.workflows ?? [];

  if (a.type === "sync") {
    const picked = a.watches ?? null;
    return (
      <div className="flex flex-col gap-3">
        <Segmented
          label="Which folders"
          value={picked == null ? "all" : "some"}
          onChange={(v) => onChange({ ...a, watches: v === "all" ? null : [] })}
          items={[
            { value: "all", label: "Every watched folder" },
            { value: "some", label: "Pick folders" },
          ]}
        />
        {picked == null ? (
          <p className="text-[12.5px] text-fg-muted">
            Scans the watched folders of the routine’s namespaces now (those that are on), so new files come in and run
            their pipelines.
          </p>
        ) : watches.length ? (
          <div className="flex flex-col gap-2">
            {watches.map((w) => (
              <Checkbox
                key={w.id}
                checked={picked.includes(w.id)}
                onCheckedChange={(on) =>
                  onChange({ ...a, watches: on ? [...picked, w.id] : picked.filter((x) => x !== w.id) })
                }
                label={
                  <>
                    {watchName(w)} <span className="text-fg-muted">→ {w.namespace}</span>
                  </>
                }
              />
            ))}
          </div>
        ) : (
          <p className="text-[12.5px] text-fg-muted">No watched folders yet: add one in Sources.</p>
        )}
      </div>
    );
  }

  if (a.type === "sensors")
    return (
      <p className="text-[12.5px] text-fg-muted">
        Removes sensor readings and hourly summaries older than each sensor keeps them, labels new log patterns where
        triage is on, and writes the daily digests of sensors that keep them, whatever the routine’s namespaces.
      </p>
    );

  if (a.type === "pipeline")
    return (
      <div className="flex flex-col gap-3">
        <Field label="Pipeline">
          {({ id }) => (
            <Select
              id={id}
              value={a.pipeline == null ? "" : String(a.pipeline)}
              onChange={(e) => onChange({ ...a, pipeline: e.target.value ? Number(e.target.value) : null })}
              options={[
                { value: "", label: "Each namespace’s own pipeline" },
                ...(pipelines.data?.pipelines ?? []).map((p) => ({ value: String(p.id), label: p.name })),
              ]}
            />
          )}
        </Field>
        <div className="flex flex-wrap items-start gap-3">
          <div className="min-w-[240px] flex-1">
            <PickSelect value={a.recordings} onChange={(v) => onChange({ ...a, recordings: v })} />
          </div>
          <LimitInput value={a.limit} onChange={(v) => onChange({ ...a, limit: v })} />
        </div>
      </div>
    );

  const wf = wfs.find((w) => w.id === a.workflow);
  const graph = wf?.scope === "graph";
  return (
    <div className="flex flex-col gap-3">
      <Field
        label="Workflow"
        hint={
          wf
            ? graph
              ? "Organises the entity graph of the routine’s namespaces"
              : "Queued on each recording as a workflow step"
            : undefined
        }
      >
        {({ id, describedBy }) => (
          <Select
            id={id}
            aria-describedby={describedBy}
            value={a.workflow == null ? "" : String(a.workflow)}
            onChange={(e) =>
              onChange({ ...a, workflow: e.target.value ? Number(e.target.value) : null, version: null })
            }
            options={[
              { value: "", label: "Choose a workflow…" },
              ...wfs.map((w) => ({
                value: String(w.id),
                label: `${w.name}${w.scope === "graph" ? " · graph" : ""}`,
              })),
            ]}
          />
        )}
      </Field>
      {wf &&
        (graph ? (
          <Checkbox
            checked={Boolean(a.propose_only)}
            onCheckedChange={(v) => onChange({ ...a, propose_only: v })}
            label="Only propose changes (make none, even the sure ones)"
          />
        ) : (
          <div className="flex flex-wrap items-start gap-3">
            <div className="min-w-[240px] flex-1">
              <PickSelect value={a.recordings} onChange={(v) => onChange({ ...a, recordings: v })} />
            </div>
            <LimitInput value={a.limit} onChange={(v) => onChange({ ...a, limit: v })} />
          </div>
        ))}
    </div>
  );
}

/** A routine's settings: name, schedule, namespaces and its actions in order. New when `routine` is omitted. */
export function RoutineEditor({ routine, onSaved }: { routine?: Routine; onSaved?: () => void }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const router = useRouter();
  const toast = useToast();
  const { namespaces } = useArchive();
  const watches = useWatches();
  const workflows = useWorkflowCatalog();
  const zoneList = useId();
  const creating = !routine;

  const [name, setName] = useState(routine?.name ?? "");
  const [description, setDescription] = useState(routine?.description ?? "");
  const [enabled, setEnabled] = useState(routine?.enabled ?? true);
  const [preset, setPreset] = useState<Preset>(routine ? presetOf(routine.schedule) : "daily");
  const [custom, setCustom] = useState(routine?.schedule ?? "0 3 * * *");
  const [timezone, setTimezone] = useState(routine?.timezone ?? browserZone());
  const [spaces, setSpaces] = useState<number[] | null>(routine?.namespaces ?? null);
  const [actions, setActions] = useState<RoutineAction[]>(
    (routine?.actions as RoutineAction[] | undefined) ?? [newAction("sync")],
  );
  const [error, setError] = useState<string | null>(null);
  const allZones = useMemo(zones, []);

  const schedule = scheduleFor(preset, custom);
  const graphWorkflow = (id: number) => workflows.data?.workflows.find((w) => w.id === id)?.scope === "graph";
  const named = name.trim() || suggestedName(preset, actions);
  const problem =
    preset === "custom" && !schedule
      ? "Type a cron schedule, or pick another."
      : spaces != null && !spaces.length
        ? "Pick at least one namespace, or run it over all of them."
        : actionProblem(actions);

  const save = useMutation({
    mutationFn: async () => {
      const body = {
        name: named,
        description: description.trim() || null,
        enabled,
        schedule,
        timezone: timezone.trim() || "UTC",
        namespaces: spaces,
        actions: cleanActions(actions, graphWorkflow),
      };
      if (creating) return (await data(Routines.createRoutine({ client, body }))).id;
      await data(Routines.updateRoutine({ client, path: { rid: routine.id }, body }));
      return routine.id;
    },
    onMutate: () => setError(null),
    onSuccess: (id) => {
      void qc.invalidateQueries({ queryKey: ["routines"] });
      void qc.invalidateQueries({ queryKey: ["routine", id] });
      toast({ tone: "green", title: creating ? "Routine created" : "Routine saved", body: named });
      if (creating) router.push(`/routines/${id}`);
      onSaved?.();
    },
    onError: (e: Error) => setError(e.message),
  });

  const setAt = (i: number, a: RoutineAction) => setActions(actions.map((x, k) => (k === i ? a : x)));
  const moveAt = (i: number, d: -1 | 1) => {
    const next = [...actions];
    [next[i], next[i + d]] = [next[i + d], next[i]];
    setActions(next);
  };
  const ws = (watches.data ?? []) as Watch[];
  const shownWatches = spaces == null ? ws : ws.filter((w) => spaces.includes(w.space));

  return (
    <form
      className="flex max-w-[760px] flex-col gap-5"
      onSubmit={(e) => {
        e.preventDefault();
        if (!problem && !save.isPending) save.mutate();
      }}
    >
      <Panel title="About">
        <div className="flex flex-col gap-3">
          <Field label="Name">
            {({ id }) => (
              <Input
                id={id}
                value={name}
                placeholder={suggestedName(preset, actions)}
                maxLength={80}
                onChange={(e) => setName(e.target.value)}
                autoFocus={creating}
              />
            )}
          </Field>
          <Field label="Description" optional>
            {({ id }) => (
              <Textarea id={id} rows={2} value={description} onChange={(e) => setDescription(e.target.value)} />
            )}
          </Field>
          <Switch checked={enabled} onCheckedChange={setEnabled} label="On: run at the scheduled times" />
        </div>
      </Panel>

      <Panel title="When">
        <div className="flex flex-col gap-3">
          <div className="flex flex-wrap items-start gap-3">
            <Field label="Schedule" className="min-w-[220px] flex-1">
              {({ id }) => (
                <Select
                  id={id}
                  value={preset}
                  onChange={(e) => setPreset(e.target.value as Preset)}
                  options={PRESETS.map((p) => ({ value: p.value, label: p.label }))}
                />
              )}
            </Field>
            {preset !== "manual" && (
              <Field label="Time zone" className="w-[240px]">
                {({ id }) => (
                  <>
                    <Input id={id} list={zoneList} value={timezone} onChange={(e) => setTimezone(e.target.value)} />
                    <datalist id={zoneList}>
                      {allZones.map((z) => (
                        <option key={z} value={z} />
                      ))}
                    </datalist>
                  </>
                )}
              </Field>
            )}
          </div>
          {preset === "custom" && (
            <Field label="Cron" hint="minute hour day month weekday, e.g. 30 2 * * 1-5; or @daily, @weekly">
              {({ id, describedBy }) => (
                <Input
                  id={id}
                  aria-describedby={describedBy}
                  mono
                  value={custom}
                  onChange={(e) => setCustom(e.target.value)}
                />
              )}
            </Field>
          )}
          <SchedulePreview schedule={schedule} timezone={timezone.trim() || "UTC"} />
        </div>
      </Panel>

      <Panel title="Where" subtitle="The namespaces its actions work on">
        <div className="flex flex-col gap-3">
          <Segmented
            label="Namespaces"
            value={spaces == null ? "all" : "some"}
            onChange={(v) => setSpaces(v === "all" ? null : [])}
            items={[
              { value: "all", label: "Every namespace" },
              { value: "some", label: "Pick namespaces" },
            ]}
          />
          {spaces != null && (
            <div className="flex flex-wrap gap-x-5 gap-y-2">
              {namespaces.map((n) => (
                <Checkbox
                  key={n.id}
                  checked={spaces.includes(n.id)}
                  onCheckedChange={(on) => setSpaces(on ? [...spaces, n.id] : spaces.filter((x) => x !== n.id))}
                  label={n.name}
                />
              ))}
            </div>
          )}
        </div>
      </Panel>

      <section aria-label="Actions" className="flex flex-col gap-3">
        <div className="flex items-center gap-3">
          <h2 className="flex-1 text-[16px] font-bold text-fg">What it does, in order</h2>
          <Menu>
            <MenuTrigger asChild>
              <Button size="sm" variant="secondary" icon={<Plus />} disabled={actions.length >= 20}>
                Add action
              </Button>
            </MenuTrigger>
            <MenuContent align="end">
              {(Object.keys(ACTION_LABEL) as RoutineAction["type"][]).map((t) => (
                <MenuItem key={t} onSelect={() => setActions([...actions, newAction(t)])}>
                  {ACTION_LABEL[t]}
                </MenuItem>
              ))}
            </MenuContent>
          </Menu>
        </div>
        {actions.map((a, i) => (
          <Panel
            key={i}
            title={`${i + 1}. ${ACTION_LABEL[a.type]}`}
            actions={
              <>
                <IconButton label="Move up" size={30} disabled={i === 0} onClick={() => moveAt(i, -1)}>
                  <ArrowUp />
                </IconButton>
                <IconButton
                  label="Move down"
                  size={30}
                  disabled={i === actions.length - 1}
                  onClick={() => moveAt(i, 1)}
                >
                  <ArrowDown />
                </IconButton>
                <IconButton label="Remove" size={30} onClick={() => setActions(actions.filter((_, k) => k !== i))}>
                  <Trash2 />
                </IconButton>
              </>
            }
          >
            <ActionSettings action={a} onChange={(x) => setAt(i, x)} watches={shownWatches} />
          </Panel>
        ))}
        {!actions.length && (
          <p className="rounded-md border border-dashed border-border px-4 py-5 text-[13.5px] text-fg-secondary">
            Add an action: sync sources, run a pipeline, run a workflow, or tidy sensor data. Each runs even when the
            one before it failed.
          </p>
        )}
      </section>

      {error && (
        <Banner tone="error" title="Couldn’t save.">
          {error}
        </Banner>
      )}
      <div className="flex items-center gap-2">
        <Button
          type="submit"
          variant="primary"
          disabled={Boolean(problem) || save.isPending}
          disabledReason={problem ?? "Saving…"}
        >
          {creating ? "Create routine" : "Save changes"}
        </Button>
        {creating && (
          <Button variant="ghost" onClick={() => router.push("/routines")}>
            Cancel
          </Button>
        )}
      </div>
    </form>
  );
}
