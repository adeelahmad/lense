import type { ReactNode } from "react";

import { LensMark } from "@/components/brand";
import { cn } from "@/lib/utils";

/** The mark and the wordmark, as on the sign-in card (Access AC1–AC2). */
export function AuthBrand({ className, suffix }: { className?: string; suffix?: ReactNode }) {
  return (
    <div className={cn("flex items-center gap-[9px]", className)}>
      <LensMark size={34} />
      <span className="whitespace-nowrap text-[18px] font-extrabold leading-none tracking-[-0.03em] text-blue">
        Lens
      </span>
      {suffix && <span className="text-[12px] text-fg-muted">{suffix}</span>}
    </div>
  );
}

/** The centred card used by sign-in, setup and password reset (Access AC1–AC2): 24px radius, 28px padding. */
export function AuthCard({
  title,
  description,
  children,
  footer,
  wide,
}: {
  title: ReactNode;
  description?: ReactNode;
  children: ReactNode;
  footer?: ReactNode;
  /** Setup is a little wider (440px) than sign-in (400px). */
  wide?: boolean;
}) {
  return (
    <section
      className={cn(
        "mx-auto flex w-full flex-col gap-4 rounded-xl border border-border bg-background p-6 sm:p-7",
        wide ? "max-w-[440px]" : "max-w-[400px]",
      )}
    >
      <AuthBrand />
      <div className="flex flex-col gap-1.5">
        <h1 className="text-[22px] font-bold leading-[1.25] text-fg">{title}</h1>
        {description && <div className="text-[14px] leading-normal text-fg-secondary">{description}</div>}
      </div>
      {children}
      {footer && <div className="text-[12.5px] leading-[1.45] text-fg-muted">{footer}</div>}
    </section>
  );
}

const ALERT = {
  error: {
    cls: "border-red-border bg-red-surface",
    glyph: "✕",
    color: "text-red",
  },
  gate: {
    cls: "border-gold-border bg-gold-surface",
    glyph: "◆",
    color: "text-gold-dark",
  },
  success: {
    cls: "border-green-border bg-green-surface",
    glyph: "✓",
    color: "text-green-dark",
  },
  info: {
    cls: "border-blue-border bg-blue-surface",
    glyph: "●",
    color: "text-blue",
  },
};

/** The message at the top of an auth card: wrong password (red ✕), too many attempts (gold ◆), notices. */
export function AuthAlert({
  tone,
  children,
  className,
}: {
  tone: keyof typeof ALERT;
  children: ReactNode;
  className?: string;
}) {
  const a = ALERT[tone];
  return (
    <div
      role={tone === "error" || tone === "gate" ? "alert" : "status"}
      className={cn(
        "flex gap-2.5 rounded-[10px] border px-3 py-2.5 text-[13.5px] font-medium leading-[1.4] text-fg",
        a.cls,
        className,
      )}
    >
      <span aria-hidden className={cn("font-extrabold", a.color)}>
        {a.glyph}
      </span>
      <span className="min-w-0 flex-1">{children}</span>
    </div>
  );
}
