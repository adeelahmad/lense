"use client";

import { Input } from "@/components/ui/field";
import { firstDay } from "@/components/access/until";
import { cn } from "@/lib/utils";

/**
 * The "Until" field of a grant (docs/access.md#grants-that-end): a day, or empty for no end. Empty by default, so
 * giving access works as it always has.
 */
export function UntilInput({
  value,
  onChange,
  id,
  label = "Until (empty: no end)",
  className,
  disabled,
}: {
  value: string;
  onChange: (day: string) => void;
  id?: string;
  label?: string;
  className?: string;
  disabled?: boolean;
}) {
  return (
    <Input
      id={id}
      type="date"
      aria-label={id ? undefined : label}
      title="Empty: no end"
      min={firstDay()}
      value={value}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
      className={cn("w-[160px]", className)}
    />
  );
}
