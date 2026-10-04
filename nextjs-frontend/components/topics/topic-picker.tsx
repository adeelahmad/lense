"use client";

import { X } from "lucide-react";

import type { TopicItem } from "@/components/topics/model";
import { Select } from "@/components/ui/field";

/** Pick several topics: chips for the chosen ones (× takes one off) and a list to add another from `choices`. */
export function TopicPicker({
  id,
  value,
  onChange,
  choices,
  all,
  disabled,
  placeholder = "Add a topic…",
  describedBy,
}: {
  id?: string;
  value: number[];
  onChange: (ids: number[]) => void;
  choices: TopicItem[];
  all: TopicItem[];
  disabled?: boolean;
  placeholder?: string;
  describedBy?: string;
}) {
  const label = new Map(all.map((t) => [t.id, t.label]));
  const left = choices.filter((t) => !value.includes(t.id));
  return (
    <div className="flex flex-col gap-1.5">
      {value.length > 0 && (
        <ul className="m-0 flex list-none flex-wrap gap-1.5 p-0">
          {value.map((v) => (
            <li
              key={v}
              className="inline-flex h-7 items-center gap-1 rounded-pill border border-border bg-surface pl-2.5 pr-1 text-[12.5px] font-semibold text-fg"
            >
              {label.get(v) ?? `Topic ${v}`}
              {!disabled && (
                <button
                  type="button"
                  aria-label={`Remove ${label.get(v) ?? `topic ${v}`}`}
                  onClick={() => onChange(value.filter((x) => x !== v))}
                  className="grid size-5 place-items-center rounded-full text-fg-muted hover:bg-surface-neutral hover:text-fg"
                >
                  <X className="size-3" />
                </button>
              )}
            </li>
          ))}
        </ul>
      )}
      {!disabled && left.length > 0 && (
        <Select
          id={id}
          size="sm"
          aria-describedby={describedBy}
          value=""
          onChange={(e) => e.target.value && onChange([...value, Number(e.target.value)])}
          options={[{ value: "", label: placeholder }, ...left.map((t) => ({ value: String(t.id), label: t.label }))]}
        />
      )}
    </div>
  );
}
