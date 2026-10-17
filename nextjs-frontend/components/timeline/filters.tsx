"use client";

import { X } from "lucide-react";
import { useEffect, useState } from "react";

import type { ActivityKind } from "@/app/openapi-client/types.gen";
import { kindsByFamily, NO_FILTERS, isFiltered, type Filters } from "@/components/timeline/model";
import { Button } from "@/components/ui/button";
import { Checkbox, Input, Select } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { cn } from "@/lib/utils";

export type Actor = { id: string; name: string };

/** A text filter applied on Enter or when it loses focus, so each keystroke doesn't reload the timeline. */
function TextFilter({
  label,
  value,
  onApply,
  placeholder,
  list,
  className,
}: {
  label: string;
  value: string;
  onApply: (v: string) => void;
  placeholder?: string;
  list?: string;
  className?: string;
}) {
  const [draft, setDraft] = useState(value);
  useEffect(() => setDraft(value), [value]);
  const apply = () => draft.trim() !== value && onApply(draft.trim());
  return (
    <Input
      aria-label={label}
      placeholder={placeholder}
      value={draft}
      list={list}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={apply}
      onKeyDown={(e) => {
        if (e.key === "Enter") {
          e.preventDefault();
          apply();
        }
      }}
      className={cn("h-8 text-[13px]", className)}
    />
  );
}

/** Kind chips by family (Email, Calendar, Files, …) in a popover; noisy kinds are named as such. */
function KindPicker({
  kinds,
  value,
  onChange,
}: {
  kinds: ActivityKind[];
  value: string[];
  onChange: (v: string[]) => void;
}) {
  const toggle = (k: string) => onChange(value.includes(k) ? value.filter((x) => x !== k) : [...value, k]);
  const label = kinds.find((k) => k.name === value[0])?.label ?? value[0];
  return (
    <Popover>
      <PopoverTrigger asChild>
        <Button size="sm" variant={value.length ? "primary" : "secondary"}>
          {value.length === 0 ? "Every kind" : value.length === 1 ? label : `${value.length} kinds`}
        </Button>
      </PopoverTrigger>
      <PopoverContent className="flex max-h-[60vh] w-[320px] flex-col gap-3 overflow-y-auto p-3">
        {kindsByFamily(kinds).map(([family, ks]) => (
          <fieldset key={family} className="flex flex-col gap-1.5">
            <legend className="mb-1 text-[12px] font-bold text-fg-secondary">{family}</legend>
            <div className="flex flex-wrap gap-1.5">
              {ks.map((k) => {
                const on = value.includes(k.name);
                return (
                  <button
                    key={k.name}
                    type="button"
                    aria-pressed={on}
                    onClick={() => toggle(k.name)}
                    className={cn(
                      "h-7 rounded-pill border px-2.5 text-[12.5px] font-semibold",
                      on
                        ? "border-blue bg-blue-surface text-blue-dark"
                        : "border-border text-fg-secondary hover:bg-surface-neutral",
                    )}
                  >
                    {k.label}
                    {k.noisy && <span className="font-normal text-fg-muted"> · noisy</span>}
                  </button>
                );
              })}
            </div>
          </fieldset>
        ))}
        {value.length > 0 && (
          <Button size="xs" variant="link" className="self-start" onClick={() => onChange([])}>
            Every kind
          </Button>
        )}
      </PopoverContent>
    </Popover>
  );
}

/** The timeline's filters: words, unreviewed and important, kinds, source or person, namespace, entity, dates and
 * automation. Inline on a computer; stacked in a sheet on a phone (`stacked`). */
export function FiltersBar({
  value,
  onChange,
  kinds,
  actors,
  namespaces,
  tags,
  stacked,
}: {
  value: Filters;
  onChange: (f: Filters) => void;
  kinds: ActivityKind[];
  /** People and sources seen on the timeline, for "Source or person". */
  actors: Actor[];
  namespaces: string[];
  /** Entities seen in tags, offered as you type. */
  tags: string[];
  stacked?: boolean;
}) {
  const set = (patch: Partial<Filters>) => onChange({ ...value, ...patch });
  const people = actors.filter((a) => !a.id.startsWith("lens:automation/"));
  const automations = actors.filter((a) => a.id.startsWith("lens:automation/"));
  const listId = stacked ? "timeline-tags-sheet" : "timeline-tags";
  return (
    <div className={cn("flex gap-2", stacked ? "flex-col items-stretch" : "flex-wrap items-center")}>
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <Checkbox label="Unreviewed only" checked={value.unreviewed} onCheckedChange={(v) => set({ unreviewed: v })} />
        <Checkbox label="Important" checked={value.important} onCheckedChange={(v) => set({ important: v })} />
      </div>
      <KindPicker kinds={kinds} value={value.kinds} onChange={(v) => set({ kinds: v })} />
      <Select
        size="sm"
        aria-label="Source or person"
        className={stacked ? undefined : "w-44"}
        value={value.actor[0] ?? ""}
        onChange={(e) => set({ actor: e.target.value ? [e.target.value] : [] })}
        options={[
          { value: "", label: "Any source or person" },
          ...people.map((a) => ({ value: a.id, label: a.name })),
          ...value.actor.filter((id) => !people.some((a) => a.id === id)).map((id) => ({ value: id, label: id })),
        ]}
      />
      {namespaces.length > 1 && (
        <Select
          size="sm"
          aria-label="Namespace"
          className={stacked ? undefined : "w-40"}
          value={value.namespace[0] ?? ""}
          onChange={(e) => set({ namespace: e.target.value ? [e.target.value] : [] })}
          options={[{ value: "", label: "Every namespace" }, ...namespaces.map((n) => ({ value: n, label: n }))]}
        />
      )}
      <TextFilter
        label="Entity"
        placeholder="Entity"
        value={value.tag}
        onApply={(v) => set({ tag: v })}
        list={listId}
        className={stacked ? undefined : "w-36"}
      />
      <datalist id={listId}>
        {tags.map((t) => (
          <option key={t} value={t} />
        ))}
      </datalist>
      <span className="flex items-center gap-1.5">
        <Input
          type="date"
          aria-label="From"
          value={value.from}
          max={value.to || undefined}
          onChange={(e) => set({ from: e.target.value })}
          className="h-8 w-[140px] text-[13px]"
        />
        <span className="text-[12px] text-fg-muted">to</span>
        <Input
          type="date"
          aria-label="To"
          value={value.to}
          min={value.from || undefined}
          onChange={(e) => set({ to: e.target.value })}
          className="h-8 w-[140px] text-[13px]"
        />
      </span>
      {(automations.length > 0 || value.automation) && (
        <Select
          size="sm"
          aria-label="Automation"
          className={stacked ? undefined : "w-44"}
          value={value.automation}
          onChange={(e) => set({ automation: e.target.value })}
          options={[
            { value: "", label: "Any automation" },
            ...automations.map((a) => ({ value: a.id.split("/")[1], label: a.name })),
            ...(value.automation && !automations.some((a) => a.id.endsWith(`/${value.automation}`))
              ? [{ value: value.automation, label: `Automation ${value.automation}` }]
              : []),
          ]}
        />
      )}
      {isFiltered(value) && (
        <Button size="sm" variant="ghost" icon={<X />} onClick={() => onChange(NO_FILTERS)}>
          Clear
        </Button>
      )}
    </div>
  );
}
