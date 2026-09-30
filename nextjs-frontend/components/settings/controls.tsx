"use client";

import { useRef, type KeyboardEvent, type ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type Choice = {
  value: string;
  label: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  reason?: ReactNode;
};

/** Arrow keys move the selection in a radio group (roving focus). */
function useRovingRadios<T extends HTMLElement>(
  options: Choice[],
  value: string,
  onChange: (v: string) => void,
  disabled?: boolean,
) {
  const refs = useRef<(T | null)[]>([]);
  const onKeyDown = (e: KeyboardEvent, i: number) => {
    const dir =
      e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    if (!dir || disabled) return;
    e.preventDefault();
    for (let step = 1; step <= options.length; step++) {
      const j = (i + dir * step + options.length) % options.length;
      if (!options[j].disabled) {
        onChange(options[j].value);
        refs.current[j]?.focus();
        return;
      }
    }
  };
  const tabIndexOf = (i: number) => {
    const current = options.findIndex((o) => o.value === value);
    return (current === -1 ? i === 0 : i === current) ? 0 : -1;
  };
  return { refs, onKeyDown, tabIndexOf };
}

/**
 * Radio cards: a bold label and a short line under it; the chosen one has a 2px blue border on a blue tint
 * (token scope, audio in reports, IIIF access).
 */
export function ChoiceCards({
  options,
  value,
  onChange,
  columns = options.length,
  label,
  disabled,
  disabledReason,
  className,
  size = "md",
}: {
  options: Choice[];
  value: string;
  onChange: (v: string) => void;
  columns?: number;
  /** Accessible name of the group. */
  label: string;
  disabled?: boolean;
  disabledReason?: ReactNode;
  className?: string;
  size?: "sm" | "md";
}) {
  const { refs, onKeyDown, tabIndexOf } = useRovingRadios<HTMLButtonElement>(options, value, onChange, disabled);
  const group = (
    <div
      role="radiogroup"
      aria-label={label}
      aria-disabled={disabled || undefined}
      className={cn("grid gap-2", className)}
      style={{ gridTemplateColumns: `repeat(${columns}, minmax(0, 1fr))` }}
    >
      {options.map((o, i) => {
        const on = o.value === value;
        const off = disabled || o.disabled;
        const card = (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-disabled={off || undefined}
            tabIndex={tabIndexOf(i)}
            onKeyDown={(e) => onKeyDown(e, i)}
            onClick={() => !off && onChange(o.value)}
            className={cn(
              "flex min-w-0 flex-col items-start gap-[3px] rounded-md text-left transition-colors duration-fast",
              // 2px border when chosen, 1px otherwise, with padding that keeps the content in place.
              on ? "border-2 border-blue bg-blue-surface" : "border border-border bg-background hover:bg-surface",
              size === "sm" ? (on ? "p-2" : "p-[9px]") : on ? "p-3" : "p-[13px]",
              off && "cursor-not-allowed opacity-60 hover:bg-background",
            )}
          >
            <span className={cn("font-bold leading-[1.2] text-fg", size === "sm" ? "text-[12.5px]" : "text-[13.5px]")}>
              {o.label}
            </span>
            {o.hint && (
              <span className={cn("leading-[1.35] text-fg-secondary", size === "sm" ? "text-[11px]" : "text-[12px]")}>
                {o.hint}
              </span>
            )}
          </button>
        );
        return o.disabled && o.reason ? (
          <Tooltip key={o.value} content={o.reason}>
            {card}
          </Tooltip>
        ) : (
          card
        );
      })}
    </div>
  );
  return disabled && disabledReason ? (
    <Tooltip content={disabledReason}>
      <div>{group}</div>
    </Tooltip>
  ) : (
    group
  );
}

/** Pill options in a row (Speaker separation method, audit filters). The chosen one is blue-tinted. */
export function Pills({
  options,
  value,
  onChange,
  label,
  className,
}: {
  options: Choice[];
  value: string;
  onChange: (v: string) => void;
  label: string;
  className?: string;
}) {
  const { refs, onKeyDown, tabIndexOf } = useRovingRadios<HTMLButtonElement>(options, value, onChange);
  return (
    <div role="radiogroup" aria-label={label} className={cn("flex flex-wrap gap-1.5", className)}>
      {options.map((o, i) => {
        const on = o.value === value;
        const pill = (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-disabled={o.disabled || undefined}
            tabIndex={tabIndexOf(i)}
            onKeyDown={(e) => onKeyDown(e, i)}
            onClick={() => !o.disabled && onChange(o.value)}
            className={cn(
              "h-8 rounded-pill border px-3 text-[12.5px] font-semibold transition-colors duration-fast",
              on
                ? "border-blue bg-blue-surface text-fg-accent"
                : "border-border bg-background text-fg-strong hover:bg-surface",
              o.disabled && "cursor-not-allowed opacity-50",
            )}
          >
            {o.label}
          </button>
        );
        return o.disabled && o.reason ? (
          <Tooltip key={o.value} content={o.reason}>
            {pill}
          </Tooltip>
        ) : (
          pill
        );
      })}
    </div>
  );
}

/** Segmented control where some options can be unavailable (with a reason on hover and focus). */
export function SegmentedChoice({
  options,
  value,
  onChange,
  label,
  className,
}: {
  options: Choice[];
  value: string;
  onChange: (v: string) => void;
  label: string;
  className?: string;
}) {
  const { refs, onKeyDown, tabIndexOf } = useRovingRadios<HTMLButtonElement>(options, value, onChange);
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn("inline-flex gap-0.5 rounded-pill bg-surface-neutral p-[3px]", className)}
    >
      {options.map((o, i) => {
        const on = o.value === value;
        const seg = (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={on}
            aria-disabled={o.disabled || undefined}
            tabIndex={tabIndexOf(i)}
            onKeyDown={(e) => onKeyDown(e, i)}
            onClick={() => !o.disabled && onChange(o.value)}
            className={cn(
              "h-7 flex-1 whitespace-nowrap rounded-pill px-3 text-[12.5px] font-semibold transition-colors duration-fast",
              on ? "bg-background text-fg shadow-1" : "text-fg-secondary hover:text-fg",
              o.disabled && "cursor-not-allowed opacity-60 hover:text-fg-secondary",
            )}
          >
            {o.label}
          </button>
        );
        return o.disabled && o.reason ? (
          <Tooltip key={o.value} content={o.reason}>
            {seg}
          </Tooltip>
        ) : (
          seg
        );
      })}
    </div>
  );
}
