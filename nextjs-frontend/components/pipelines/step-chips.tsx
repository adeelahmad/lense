import { cn } from "@/lib/utils";

/** A pipeline's steps as small outlined chips (PL1). */
export function StepChips({
  steps,
  className,
}: {
  steps: (string | { type: string; name?: string })[];
  className?: string;
}) {
  return (
    <span className={cn("flex flex-wrap gap-[3px]", className)}>
      {steps.map((s, i) => {
        const t = typeof s === "string" ? s : s.type;
        const name = typeof s === "string" ? undefined : s.name;
        return (
          <span
            key={i}
            title={name && name !== t ? `${name} (${t})` : undefined}
            className="h-5 whitespace-nowrap rounded-pill border border-border px-[7px] text-[11px] font-medium leading-[18px] text-fg-secondary"
          >
            {t}
          </span>
        );
      })}
    </span>
  );
}
