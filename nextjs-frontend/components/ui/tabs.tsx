"use client";

import Link from "next/link";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

export type TabItem = { value: string; label: ReactNode; count?: ReactNode; href?: string; disabled?: boolean };

/** Underlined tabs (Aladdin): blue 3px underline and bold label on the active one; optional counts. */
export function Tabs({
  items,
  value,
  onChange,
  className,
  size = "md",
  "aria-label": ariaLabel,
}: {
  items: TabItem[];
  value: string;
  onChange?: (v: string) => void;
  className?: string;
  size?: "sm" | "md";
  "aria-label"?: string;
}) {
  return (
    <div role="tablist" aria-label={ariaLabel} className={cn("flex gap-1 overflow-x-auto border-b border-border", className)}>
      {items.map((it) => {
        const on = it.value === value;
        const cls = cn(
          "-mb-px inline-flex shrink-0 items-center gap-2 whitespace-nowrap rounded-t-[3px] border-b-[3px] transition-colors duration-fast",
          size === "sm" ? "px-3 py-2 text-[13px]" : "px-3.5 py-2.5 text-[14px]",
          on ? "border-blue font-bold text-blue" : "border-transparent font-medium text-fg-secondary hover:text-fg",
          it.disabled && "cursor-not-allowed opacity-50",
        );
        const inner = (
          <>
            {it.label}
            {it.count != null && <span className="font-mono text-[11px] font-medium text-fg-muted">{it.count}</span>}
          </>
        );
        return it.href && !it.disabled ? (
          <Link key={it.value} href={it.href} role="tab" aria-selected={on} className={cls}>
            {inner}
          </Link>
        ) : (
          <button key={it.value} type="button" role="tab" aria-selected={on} disabled={it.disabled} onClick={() => onChange?.(it.value)} className={cls}>
            {inner}
          </button>
        );
      })}
    </div>
  );
}

/** Segmented control (Table / List, scope pickers). */
export function Segmented({ items, value, onChange, className }: { items: { value: string; label: ReactNode; icon?: ReactNode }[]; value: string; onChange: (v: string) => void; className?: string }) {
  return (
    <div role="radiogroup" className={cn("inline-flex rounded-pill bg-surface-neutral p-1", className)}>
      {items.map((it) => (
        <button
          key={it.value}
          type="button"
          role="radio"
          aria-checked={it.value === value}
          onClick={() => onChange(it.value)}
          className={cn(
            "inline-flex h-7 items-center gap-1.5 rounded-pill px-3 text-[13px] font-semibold transition-colors duration-fast [&_svg]:size-3.5",
            it.value === value ? "bg-background text-fg shadow-1" : "text-fg-secondary hover:text-fg",
          )}
        >
          {it.icon}
          {it.label}
        </button>
      ))}
    </div>
  );
}
