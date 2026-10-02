"use client";

import { Switch } from "@/components/ui/field";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";

/** Why the Meaning switch can't be used, for the people who can't change that. */
export const MEANING_OFF = "Search by meaning is off here. An admin switches it on in Settings → Search.";

/**
 * "Meaning": also find passages that say the same in other words (search by meaning, docs/api.md#search). Where an
 * admin hasn't switched it on, the switch stays, disabled, and says why.
 */
export function MeaningToggle({
  checked,
  available,
  onChange,
  className,
}: {
  checked: boolean;
  /** undefined while the server hasn't said yet. */
  available: boolean | undefined;
  onChange: (on: boolean) => void;
  className?: string;
}) {
  const off = available === false;
  const control = (
    <span className={cn("inline-flex items-center gap-2", className)} tabIndex={off ? 0 : undefined}>
      <Switch
        checked={checked && !off}
        onCheckedChange={onChange}
        disabled={available !== true}
        label={<span className="text-[13px] font-semibold text-fg-strong">Meaning</span>}
      />
      <span className="hidden text-[12.5px] text-fg-muted sm:inline">also what says the same in other words</span>
    </span>
  );
  return off ? <Tooltip content={MEANING_OFF}>{control}</Tooltip> : control;
}

/** The mark on a hit that was found by what it means, not by its words. */
export function ByMeaning({ className }: { className?: string }) {
  return (
    <span
      className={cn(
        "mr-1.5 inline-flex h-[18px] items-center whitespace-nowrap rounded-pill border border-green-border bg-green-surface px-1.5 align-[1px] font-sans text-[11px] font-semibold text-green-dark",
        className,
      )}
    >
      By meaning
    </span>
  );
}
