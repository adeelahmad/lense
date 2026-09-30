"use client";

import { Check, Copy } from "lucide-react";
import { useState, type ReactNode } from "react";

import { Tooltip } from "@/components/ui/tooltip";
import { absolute, initials, relative } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Loading placeholder lines/blocks. */
export function Skeleton({ className, style }: { className?: string; style?: React.CSSProperties }) {
  return <span aria-hidden className={cn("skeleton block h-3.5", className)} style={style} />;
}

export function SkeletonRows({ rows = 6, className }: { rows?: number; className?: string }) {
  return (
    <div className={cn("flex flex-col gap-4 p-4", className)} aria-busy="true" aria-label="Loading">
      {Array.from({ length: rows }, (_, i) => (
        <div key={i} className="flex items-center gap-4">
          <Skeleton className="w-[38%]" />
          <Skeleton className="w-[14%]" />
          <Skeleton className="w-[10%]" />
          <Skeleton className="w-[18%]" />
        </div>
      ))}
    </div>
  );
}

/** Empty or error state with a next step. */
export function EmptyState({
  icon,
  title,
  children,
  actions,
  className,
  tone = "neutral",
}: {
  icon?: ReactNode;
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
  tone?: "neutral" | "error";
}) {
  return (
    <div
      role={tone === "error" ? "alert" : undefined}
      className={cn("mx-auto flex max-w-md flex-col items-center gap-3 px-6 py-14 text-center", className)}
    >
      {icon && (
        <div
          className={cn(
            "grid size-12 place-items-center rounded-full [&_svg]:size-6",
            tone === "error" ? "bg-red-surface text-red-dark" : "bg-surface-neutral text-fg-secondary",
          )}
        >
          {icon}
        </div>
      )}
      <h2 className="text-[17px] font-bold text-fg">{title}</h2>
      {children && <div className="text-[14px] leading-normal text-fg-secondary">{children}</div>}
      {actions && <div className="mt-2 flex flex-wrap justify-center gap-2">{actions}</div>}
    </div>
  );
}

/** Code with a copy button ("Copied" for 2 s). Dark by default, or inline. */
export function CodeBlock({
  text,
  className,
  inline,
  label,
}: {
  text: string;
  className?: string;
  inline?: boolean;
  label?: string;
}) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard unavailable: the text stays selectable */
    }
  };
  const btn = (
    <button
      type="button"
      onClick={copy}
      aria-label={copied ? "Copied" : `Copy ${label ?? "to clipboard"}`}
      className={cn(
        "inline-flex shrink-0 items-center gap-1 rounded-pill px-2 py-1 text-[12px] font-bold",
        inline ? "text-fg-accent hover:bg-blue-surface" : "text-[var(--term-blue)] hover:bg-white/10",
      )}
    >
      {copied ? <Check className="size-3.5" /> : <Copy className="size-3.5" />}
      {copied ? "Copied" : "Copy"}
    </button>
  );
  if (inline)
    return (
      <span
        className={cn(
          "inline-flex max-w-full items-center gap-2 rounded-sm border border-border bg-surface py-1 pl-3 pr-1",
          className,
        )}
      >
        <code className="truncate font-mono text-[12.5px] text-fg-strong">{text}</code>
        {btn}
      </span>
    );
  return (
    <div className={cn("relative rounded-md bg-term-bg", className)}>
      <pre className="overflow-x-auto p-4 pr-20 font-mono text-[12.5px] leading-relaxed text-term-fg">
        <code>{text}</code>
      </pre>
      <div className="absolute right-2 top-2">{btn}</div>
    </div>
  );
}

/** Relative time ("3 min ago") with the absolute time in a tooltip; `mode="absolute"` flips them. */
export function DateTime({
  iso,
  mode = "relative",
  className,
}: {
  iso: string | null | undefined;
  mode?: "relative" | "absolute";
  className?: string;
}) {
  if (!iso) return <span className={cn("text-fg-muted", className)}>—</span>;
  const main = mode === "relative" ? relative(iso) : absolute(iso);
  return (
    <Tooltip content={mode === "relative" ? absolute(iso) : relative(iso)}>
      <time dateTime={iso} className={cn("tabular", className)} tabIndex={0}>
        {main}
      </time>
    </Tooltip>
  );
}

/** Initials avatar: 24 · 32 · 40 px. */
export function Avatar({
  name,
  size = 32,
  disabled,
  className,
}: {
  name: string | null | undefined;
  size?: 24 | 32 | 40;
  disabled?: boolean;
  className?: string;
}) {
  return (
    <span
      aria-hidden
      style={{ width: size, height: size, fontSize: size * 0.36 }}
      className={cn(
        "grid shrink-0 place-items-center rounded-full bg-surface-neutral font-bold text-fg-strong",
        disabled && "opacity-40",
        className,
      )}
    >
      {initials(name)}
    </span>
  );
}

/** A thin progress bar; omit `value` for indeterminate. */
export function Progress({
  value,
  tone = "intent",
  className,
  label,
}: {
  value?: number | null;
  tone?: "intent" | "green" | "red" | "gate";
  className?: string;
  label?: string;
}) {
  const c = {
    intent: "bg-blue",
    green: "bg-green",
    red: "bg-red",
    gate: "bg-gold",
  }[tone];
  const pct = value == null ? null : Math.max(0, Math.min(1, value)) * 100;
  return (
    <span
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={pct == null ? undefined : Math.round(pct)}
      className={cn("relative block h-1 overflow-hidden rounded-pill bg-surface-neutral", className)}
    >
      {pct == null ? (
        <span className={cn("absolute inset-y-0 left-[20%] w-[30%] animate-pulse rounded-pill opacity-60", c)} />
      ) : (
        <span
          className={cn("absolute inset-y-0 left-0 rounded-pill transition-[width] duration-slow ease-standard", c)}
          style={{ width: `${pct}%` }}
        />
      )}
    </span>
  );
}

/** Page header: title 700/24, a meta line, and actions on the right. */
export function PageHeader({
  title,
  meta,
  actions,
  children,
  className,
}: {
  title: ReactNode;
  meta?: ReactNode;
  actions?: ReactNode;
  children?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-5 flex flex-wrap items-end gap-x-4 gap-y-2", className)}>
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
          <h1 className="text-[24px] font-bold leading-tight tracking-[-.015em] text-fg">{title}</h1>
          {meta && <span className="text-[13.5px] text-fg-secondary">{meta}</span>}
        </div>
        {children}
      </div>
      {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
    </div>
  );
}
