"use client";

import type { ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

export type LibraryTab<T extends string> = {
  value: T | string;
  label: string;
  count?: ReactNode;
  disabledReason?: string;
};

/** The library's view tabs (Aladdin underline tabs); a tab the backend can't back yet stays visible, disabled, with why. */
export function LibraryTabs<T extends string>({
  items,
  value,
  onChange,
}: {
  items: LibraryTab<T>[];
  value: T;
  onChange: (v: T) => void;
}) {
  return (
    <div
      role="tablist"
      aria-label="Library views"
      className="flex overflow-x-auto border-b border-border [scrollbar-width:none]"
    >
      {items.map((it) => {
        const on = it.value === value;
        const cls = cn(
          "-mb-px inline-flex h-[42px] shrink-0 items-center gap-2 whitespace-nowrap border-b-[3px] px-3.5 text-[14px] transition-colors duration-fast",
          on ? "border-blue font-bold text-blue" : "border-transparent font-medium text-fg-secondary hover:text-fg",
          it.disabledReason && "cursor-not-allowed opacity-50 hover:text-fg-secondary",
        );
        const inner = (
          <>
            {it.label}
            {it.count != null && <span className="font-mono text-[11px] font-medium text-fg-muted">{it.count}</span>}
          </>
        );
        if (it.disabledReason) {
          return (
            <Tooltip key={it.value} content={it.disabledReason}>
              <button
                type="button"
                role="tab"
                aria-selected={false}
                aria-disabled
                className={cls}
                onClick={(e) => e.preventDefault()}
              >
                {inner}
              </button>
            </Tooltip>
          );
        }
        return (
          <button
            key={it.value}
            type="button"
            role="tab"
            aria-selected={on}
            className={cls}
            onClick={() => onChange(it.value as T)}
          >
            {inner}
          </button>
        );
      })}
    </div>
  );
}
