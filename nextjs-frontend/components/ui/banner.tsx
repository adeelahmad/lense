import { X } from "lucide-react";
import type { ReactNode } from "react";

import { cn } from "@/lib/utils";

const B = {
  info: { cls: "bg-blue-surface border-blue-border", g: "●", c: "var(--aladdin-blue)" },
  warning: { cls: "bg-gold-surface border-gold-border", g: "◆", c: "var(--gold-dark)" },
  error: { cls: "bg-red-surface border-red-border", g: "✕", c: "var(--red-dark)" },
  success: { cls: "bg-green-surface border-green-border", g: "✓", c: "var(--green-dark)" },
};

/** Page-level message: info (blue) · warning ◆ (gold) · error ✕ (red) · success ✓, with an action or dismiss. */
export function Banner({
  tone = "info",
  title,
  children,
  action,
  onDismiss,
  className,
}: {
  tone?: keyof typeof B;
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
  className?: string;
}) {
  const b = B[tone];
  return (
    <div role={tone === "error" ? "alert" : "status"} className={cn("flex items-start gap-3 rounded-md border px-4 py-3 text-[13.5px]", b.cls, className)}>
      <span aria-hidden className="mt-px shrink-0 font-bold" style={{ color: b.c }}>
        {b.g}
      </span>
      <div className="min-w-0 flex-1 leading-snug text-fg-strong">
        {title && <span className="font-bold text-fg">{title} </span>}
        {children}
      </div>
      {action && <div className="shrink-0">{action}</div>}
      {onDismiss && (
        <button type="button" onClick={onDismiss} aria-label="Dismiss" className="-mr-1 grid size-6 shrink-0 place-items-center rounded-full text-fg-secondary hover:bg-black/5">
          <X className="size-3.5" />
        </button>
      )}
    </div>
  );
}
