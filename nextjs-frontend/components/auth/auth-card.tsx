import type { ReactNode } from "react";

import { Brand } from "@/components/brand";

/** The centred card used by sign-in, setup and password reset (Access AC1–AC2). */
export function AuthCard({ title, description, children, footer }: { title: string; description?: ReactNode; children: ReactNode; footer?: ReactNode }) {
  return (
    <div className="flex flex-col items-center gap-8">
      <Brand size={26} />
      <section className="w-full rounded-xl border border-border bg-background p-7 shadow-1">
        <h1 className="text-[22px] font-bold leading-tight tracking-[-.01em] text-fg">{title}</h1>
        {description && <div className="mt-1.5 text-[14px] leading-normal text-fg-secondary">{description}</div>}
        <div className="mt-6 grid gap-5">{children}</div>
        {footer && <div className="mt-6 text-center text-[13.5px] text-fg-secondary">{footer}</div>}
      </section>
    </div>
  );
}
