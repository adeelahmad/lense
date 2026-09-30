"use client";

import type { ReactNode } from "react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

/** The full-width primary button of an auth card; disabled it turns grey (as in the design), not faded blue. */
export function AuthSubmit({
  children,
  pending,
  pendingText,
  disabled,
  disabledReason,
  className,
}: {
  children: ReactNode;
  pending?: boolean;
  pendingText?: string;
  disabled?: boolean;
  disabledReason?: ReactNode;
  className?: string;
}) {
  return (
    <Button
      type="submit"
      variant="primary"
      size="lg"
      disabled={disabled || pending}
      disabledReason={pending ? undefined : disabledReason}
      className={cn(
        "w-full justify-start disabled:bg-surface-neutral disabled:text-fg-muted disabled:opacity-100 aria-disabled:bg-surface-neutral aria-disabled:text-fg-muted aria-disabled:opacity-100",
        className,
      )}
    >
      {pending ? (pendingText ?? children) : children}
    </Button>
  );
}
