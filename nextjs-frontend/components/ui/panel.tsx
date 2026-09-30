import type { ReactNode } from "react";

import type { Tone } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const PANEL: Record<Tone, string> = {
  neutral: "bg-surface border-border",
  intent: "bg-blue-surface border-blue-border",
  green: "bg-green-surface border-green-border",
  red: "bg-red-surface border-red-border",
  gate: "bg-gold-surface border-gold-border",
};

/** A bordered card: plain white with a hairline, or a semantic tint. No shadow at rest. */
export function Panel({
  tone,
  title,
  subtitle,
  actions,
  className,
  bodyClassName,
  children,
  as: Tag = "section",
}: {
  tone?: Tone;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  className?: string;
  bodyClassName?: string;
  children?: ReactNode;
  as?: "section" | "div" | "article";
}) {
  return (
    <Tag className={cn("rounded-md border", tone ? PANEL[tone] : "border-border bg-background", className)}>
      {(title || actions) && (
        <header className="flex items-start gap-3 px-4 pb-1 pt-3.5">
          <div className="min-w-0 flex-1">
            {title && <h2 className="text-[15px] font-bold leading-snug text-fg">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-[13px] text-fg-secondary">{subtitle}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className={cn("p-4", (title || actions) && "pt-2", bodyClassName)}>{children}</div>
    </Tag>
  );
}

/** Section heading inside a page: 700/16. */
export function SectionTitle({
  children,
  actions,
  className,
}: {
  children: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-3 flex items-center gap-3", className)}>
      <h2 className="flex-1 text-[16px] font-bold leading-tight text-fg">{children}</h2>
      {actions}
    </div>
  );
}

/** Small uppercase label (KEY POINTS, CHAPTERS). */
export function Label({
  children,
  className,
  as: Tag = "div",
}: {
  children: ReactNode;
  className?: string;
  as?: "div" | "h3" | "span" | "dt";
}) {
  return <Tag className={cn("label-caps", className)}>{children}</Tag>;
}
