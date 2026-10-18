"use client";

/** The builder's pieces: "only when" and If rows, a trigger card, the join line and an action card. */
import { ArrowDown, ArrowUp, Plus, Trash2 } from "lucide-react";
import { useRef, type KeyboardEvent } from "react";

import type { ActivityKind } from "@/app/openapi-client/types.gen";
import {
  CLOCK,
  NO_VALUE,
  OP_LABEL,
  canRunOnItsOwn,
  joinFor,
  joinText,
  kindFields,
  opsFor,
  rowsOf,
  stepBadge,
  treeOf,
  valueFrom,
  valueText,
  type FieldType,
  type Join,
  type Rows,
  type Step,
  type Trigger,
} from "@/components/automations/model";
import { SchemaForm, type Variable } from "@/components/forms/schema-form";
import type { JsonSchema } from "@/components/forms/schema-model";
import { PRESETS, presetOf } from "@/components/routines/routine-model";
import { SchedulePreview } from "@/components/routines/schedule-fields";
import { Badge } from "@/components/ui/badge";
import { Button, IconButton } from "@/components/ui/button";
import { Field, Input, Select, Switch } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { Segmented } from "@/components/ui/tabs";
import type { ActionDef } from "@/components/workflows/workflow-model";

export type FieldInfo = { path: string; label: string; type: FieldType };

/** Rows of tests, all or any of them: a field, a test that suits it and a value. Enter in the last value adds a row. */
export function RowsEditor({
  value,
  onChange,
  fields,
  idPrefix,
  empty,
  addLabel = "Add a condition",
}: {
  value: unknown;
  onChange: (tree: unknown) => void;
  fields: FieldInfo[];
  idPrefix: string;
  empty?: string;
  addLabel?: string;
}) {
  const box = useRef<HTMLDivElement>(null);
  const rows = rowsOf(value);
  if (!rows)
    return (
      <div className="flex items-center gap-2 text-[13px] text-fg-secondary">
        <span>These conditions nest, so they are edited through the API or by an agent.</span>
        <Button size="xs" variant="ghost" onClick={() => onChange(undefined)}>
          Clear them
        </Button>
      </div>
    );
  const set = (r: Rows) => onChange(treeOf(r) ?? (r.rows.length ? { [r.mode]: r.rows } : undefined));
  const add = () => {
    const first = fields[0];
    set({ ...rows, rows: [...rows.rows, { path: first?.path ?? "", op: opsFor(first?.type ?? "any")[0] }] });
    // the new row's field takes the focus
    requestAnimationFrame(() => box.current?.querySelector<HTMLElement>(`[data-row="${rows.rows.length}"]`)?.focus());
  };
  const typeOf = (path: string) => fields.find((f) => f.path === path)?.type ?? "any";
  const listId = `${idPrefix}-fields`;
  return (
    <div ref={box} className="flex flex-col gap-2">
      {rows.rows.length > 1 && (
        <Segmented
          label="Which must hold"
          value={rows.mode}
          onChange={(v) => set({ ...rows, mode: v as Rows["mode"] })}
          items={[
            { value: "all", label: "All of these" },
            { value: "any", label: "Any of these" },
          ]}
        />
      )}
      <datalist id={listId}>
        {fields.map((f) => (
          <option key={f.path} value={f.path}>
            {f.label}
          </option>
        ))}
      </datalist>
      {rows.rows.map((r, k) => {
        const type = typeOf(r.path);
        const ops = opsFor(type);
        const setRow = (patch: Partial<typeof r>) =>
          set({ ...rows, rows: rows.rows.map((x, i) => (i === k ? { ...x, ...patch } : x)) });
        const onKey = (e: KeyboardEvent) => {
          if (e.key === "Enter" && !e.metaKey && !e.ctrlKey && k === rows.rows.length - 1) {
            e.preventDefault();
            add();
          }
        };
        return (
          <div key={k} className="flex flex-wrap items-center gap-2">
            <Input
              data-row={k}
              aria-label={`Field ${k + 1}`}
              list={listId}
              className="h-9 w-[220px] font-mono text-[12.5px]"
              value={r.path}
              placeholder={fields[0]?.path ?? "event.summary"}
              onChange={(e) => setRow({ path: e.target.value })}
            />
            <Select
              aria-label={`Test ${k + 1}`}
              size="sm"
              className="w-[170px]"
              value={r.op}
              onChange={(e) =>
                setRow({ op: e.target.value, value: NO_VALUE.includes(e.target.value) ? undefined : r.value })
              }
              options={[...new Set([...ops, r.op])].map((o) => ({ value: o, label: OP_LABEL[o] ?? o }))}
            />
            {!NO_VALUE.includes(r.op) && (
              <Input
                aria-label={`Value ${k + 1}`}
                className="h-9 min-w-[160px] flex-1"
                value={valueText(r)}
                placeholder={["in", "not_in", "weekday"].includes(r.op) ? "one, another" : ""}
                onKeyDown={onKey}
                onChange={(e) => setRow({ value: valueFrom(r.op, e.target.value, type) })}
              />
            )}
            <IconButton
              label={`Remove condition ${k + 1}`}
              size={30}
              onClick={() => set({ ...rows, rows: rows.rows.filter((_, i) => i !== k) })}
            >
              <Trash2 />
            </IconButton>
          </div>
        );
      })}
      {!rows.rows.length && empty && <p className="text-[13px] text-fg-muted">{empty}</p>}
      <Button size="xs" variant="ghost" icon={<Plus />} className="self-start" onClick={add}>
        {addLabel}
      </Button>
    </div>
  );
}

/** One trigger: what happens (a kind, a schedule, a time, its webhook, by hand) and its "only when" rows. */
export function TriggerCard({
  trigger: t,
  index,
  kinds,
  onChange,
  onRemove,
  problem,
}: {
  trigger: Trigger;
  index: number;
  kinds: ActivityKind[];
  onChange: (t: Trigger) => void;
  onRemove: () => void;
  problem?: string;
}) {
  const kind = kinds.find((k) => k.name === t.kinds?.[0]);
  const title =
    t.on === "event"
      ? (kind?.label ?? t.kinds?.[0] ?? "Something happens")
      : t.on === "relative"
        ? "A time in an activity"
        : (CLOCK.find((c) => c.on === t.on)?.label ?? t.on);
  const id = `trigger-${index}`;
  return (
    <Panel
      title={title}
      subtitle={<span className="font-mono text-[12px]">as {t.as}</span>}
      actions={
        <IconButton label={`Remove trigger ${t.as}`} size={30} onClick={onRemove}>
          <Trash2 />
        </IconButton>
      }
    >
      <div className="flex flex-col gap-3">
        {problem && (
          <p role="alert" className="text-[12.5px] text-red-dark">
            {problem}
          </p>
        )}
        {(t.on === "event" || t.on === "relative") && (
          <>
            <Field label="What happens" htmlFor={`${id}-kind`}>
              {({ id: fid }) => (
                <Select
                  id={fid}
                  value={t.kinds?.[0] ?? ""}
                  onChange={(e) => onChange({ ...t, kinds: e.target.value ? [e.target.value] : [], where: undefined })}
                  options={[
                    { value: "", label: "Choose…" },
                    ...kinds
                      .filter((k) => !k.noisy || k.name === t.kinds?.[0])
                      .map((k) => ({ value: k.name, label: k.label })),
                  ]}
                />
              )}
            </Field>
            <div className="flex flex-col gap-1.5">
              <span className="text-[13px] font-bold text-fg-strong">Only when</span>
              <RowsEditor
                idPrefix={`${id}-where`}
                value={t.where}
                onChange={(where) => onChange({ ...t, where })}
                fields={kind ? kindFields(kind.schema) : []}
                empty="Every one of these, with no conditions."
                addLabel="Add an only-when condition"
              />
            </div>
          </>
        )}
        {t.on === "schedule" && <ScheduleFields trigger={t} onChange={onChange} id={id} />}
        {t.on === "at" && (
          <div className="flex flex-wrap gap-3">
            <Field label="Date and time" htmlFor={`${id}-at`}>
              {({ id: fid }) => (
                <Input
                  id={fid}
                  type="datetime-local"
                  value={String(t.at ?? "").slice(0, 16)}
                  onChange={(e) => onChange({ ...t, at: e.target.value })}
                />
              )}
            </Field>
            <TimezoneField trigger={t} onChange={onChange} id={id} />
          </div>
        )}
        {t.on === "webhook" && (
          <p className="text-[13px] text-fg-secondary">
            It gets its own address and signing secret when the automation is saved; they show once.
          </p>
        )}
        {t.on === "manual" && <p className="text-[13px] text-fg-secondary">It runs when you press Run now.</p>}
      </div>
    </Panel>
  );
}

function TimezoneField({ trigger: t, onChange, id }: { trigger: Trigger; onChange: (t: Trigger) => void; id: string }) {
  return (
    <Field label="Time zone" htmlFor={`${id}-tz`}>
      {({ id: fid }) => (
        <Input
          id={fid}
          className="w-[220px]"
          value={String(t.timezone ?? "")}
          placeholder="UTC"
          onChange={(e) => onChange({ ...t, timezone: e.target.value })}
        />
      )}
    </Field>
  );
}

/** A schedule: the routine editor's presets, a cron line, the time zone and the next times. */
function ScheduleFields({
  trigger: t,
  onChange,
  id,
}: {
  trigger: Trigger;
  onChange: (t: Trigger) => void;
  id: string;
}) {
  const preset = presetOf(t.cron);
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap gap-3">
        <Field label="When" htmlFor={`${id}-preset`}>
          {({ id: fid }) => (
            <Select
              id={fid}
              className="w-[220px]"
              value={preset === "manual" ? "custom" : preset}
              onChange={(e) => {
                const p = PRESETS.find((x) => x.value === e.target.value);
                if (p?.cron) onChange({ ...t, cron: p.cron });
              }}
              options={PRESETS.filter((p) => p.value !== "manual").map((p) => ({ value: p.value, label: p.label }))}
            />
          )}
        </Field>
        <Field label="Schedule (cron)" htmlFor={`${id}-cron`}>
          {({ id: fid }) => (
            <Input
              id={fid}
              mono
              className="w-[180px]"
              value={String(t.cron ?? "")}
              onChange={(e) => onChange({ ...t, cron: e.target.value })}
            />
          )}
        </Field>
        <TimezoneField trigger={t} onChange={onChange} id={id} />
      </div>
      <SchedulePreview schedule={String(t.cron ?? "").trim() || null} timezone={String(t.timezone ?? "UTC")} />
    </div>
  );
}

const JOINS: { value: Join["mode"]; label: string }[] = [
  { value: "any", label: "Any of these" },
  { value: "all", label: "All of these within a time" },
  { value: "count", label: "Several times within a time" },
  { value: "absent", label: "One, and then not the other" },
  { value: "batch", label: "Collect for a while, then once" },
];

/** With two or more triggers: how they make one run, as a line that reads like the sentence it means. */
export function JoinLine({
  join,
  triggers,
  onChange,
}: {
  join: Join;
  triggers: Trigger[];
  onChange: (j: Join) => void;
}) {
  const names = triggers.map((t) => ({ value: t.as, label: t.as }));
  const small = (label: string, el: React.ReactNode) => (
    <label className="flex items-center gap-1.5 text-[13px] text-fg-secondary">
      {label}
      {el}
    </label>
  );
  return (
    <div className="flex flex-col gap-2 rounded-md border border-border bg-surface px-4 py-3">
      <div className="flex flex-wrap items-center gap-2">
        <Select
          aria-label="How the triggers make a run"
          size="sm"
          className="w-[250px]"
          value={join.mode}
          onChange={(e) => onChange(joinFor(e.target.value as Join["mode"], triggers, join))}
          options={JOINS}
        />
        {join.mode === "count" &&
          small(
            "",
            <>
              <Input
                aria-label="How many times"
                type="number"
                min={1}
                className="h-8 w-[70px]"
                value={String(join.at_least ?? 3)}
                onChange={(e) => onChange({ ...join, at_least: Number(e.target.value) || 1 })}
              />
              <span>times</span>
              <Select
                aria-label="Of which trigger"
                size="sm"
                className="w-[140px]"
                value={join.of ?? ""}
                onChange={(e) => onChange({ ...join, of: e.target.value })}
                options={names}
              />
            </>,
          )}
        {join.mode === "absent" && (
          <>
            <Select
              aria-label="After"
              size="sm"
              className="w-[140px]"
              value={join.after ?? ""}
              onChange={(e) => onChange({ ...join, after: e.target.value })}
              options={names}
            />
            <span className="text-[13px] text-fg-secondary">and then no</span>
            <Select
              aria-label="Missing"
              size="sm"
              className="w-[140px]"
              value={join.missing ?? ""}
              onChange={(e) => onChange({ ...join, missing: e.target.value })}
              options={names}
            />
          </>
        )}
        {join.mode !== "any" &&
          small(
            join.mode === "batch" ? "for" : "within",
            <Input
              aria-label="Within"
              className="h-8 w-[80px] font-mono"
              value={join.within ?? ""}
              placeholder="10m"
              onChange={(e) => onChange({ ...join, within: e.target.value })}
            />,
          )}
        {join.mode !== "any" &&
          join.mode !== "batch" &&
          small(
            "for the same",
            <Input
              aria-label="For the same"
              className="h-8 w-[180px] font-mono text-[12.5px]"
              value={join.key ?? ""}
              placeholder="object.from.0 (optional)"
              onChange={(e) => onChange({ ...join, key: e.target.value || undefined })}
            />,
          )}
      </div>
      <p className="text-[12.5px] text-fg-muted">{joinText(join)}</p>
    </div>
  );
}

/** One action of the Then: which action, its settings from its params, a wait, and whether it asks first. */
export function StepCard({
  step: s,
  index,
  count,
  actions,
  variables,
  policy,
  saved,
  onChange,
  onRemove,
  onMove,
  onPolicy,
  problem,
}: {
  step: Step;
  index: number;
  count: number;
  actions: ActionDef[];
  variables?: Variable[];
  policy: Record<string, string>;
  /** Saved: only then can a step be let run on its own. */
  saved: boolean;
  onChange: (s: Step) => void;
  onRemove: () => void;
  onMove: (by: number) => void;
  onPolicy: (auto: boolean) => void;
  problem?: string;
}) {
  const a = actions.find((x) => x.name === s.action);
  const badge = stepBadge(s, actions, policy);
  const auto = policy[s.id] === "auto";
  return (
    <Panel
      title={`${index + 1}. ${a?.label ?? "Choose an action"}`}
      subtitle={a?.description}
      actions={
        <>
          <Badge tone={badge.tone}>{badge.label}</Badge>
          <IconButton label="Move up" size={30} disabled={index === 0} onClick={() => onMove(-1)}>
            <ArrowUp />
          </IconButton>
          <IconButton label="Move down" size={30} disabled={index === count - 1} onClick={() => onMove(1)}>
            <ArrowDown />
          </IconButton>
          <IconButton label={`Remove action ${index + 1}`} size={30} onClick={onRemove}>
            <Trash2 />
          </IconButton>
        </>
      }
    >
      <div className="flex flex-col gap-3">
        {problem && (
          <p role="alert" className="text-[12.5px] text-red-dark">
            {problem}
          </p>
        )}
        {a && (
          <SchemaForm
            idPrefix={`step-${s.id}`}
            schema={a.params as JsonSchema}
            required={a.required}
            value={s.params}
            onChange={(params) => onChange({ ...s, params })}
            variables={variables}
          />
        )}
        <Field label="Wait first" optional htmlFor={`step-${s.id}-delay`} hint="Like 30m or 2h: it waits, then acts.">
          {({ id, describedBy }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              mono
              className="w-[140px]"
              value={s.delay ?? ""}
              placeholder="30m"
              onChange={(e) => onChange({ ...s, delay: e.target.value.trim() || undefined })}
            />
          )}
        </Field>
        {canRunOnItsOwn(s, actions) && (
          <div className="flex flex-col gap-1">
            <Switch
              checked={auto}
              disabled={!saved}
              onCheckedChange={onPolicy}
              label="Let it run on its own"
              aria-label={`Let ${a?.label ?? "it"} run on its own`}
            />
            <p className="text-[12.5px] text-fg-muted">
              {!saved
                ? "Save it first; then you can let this action run without asking you."
                : auto
                  ? "It runs without asking you first. Switch it off to be asked again."
                  : "It asks you first each time, on the Approvals page or with a yes in your chat app."}
            </p>
          </div>
        )}
      </div>
    </Panel>
  );
}
